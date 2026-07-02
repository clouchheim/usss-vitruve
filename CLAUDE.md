# usss-vitruve — Vitruve → Teamworks AMS Integration

Custom integration pulling VBT (velocity-based training) data from Vitruve and
loading it into US Ski & Snowboard's Teamworks AMS instance, against a
dedicated Vitruve VBT form.

## Status

**v1 implemented** (`vitruve_sync/` package + `.github/workflows/vitruve_sync.yml`,
scheduled every 30 minutes). Built against the real "Vitruve VBT" form once
it existed — see "Learnings from first real sample" and "Transform" below
for how the design got there. Verified so far: 18 unit tests
(`tests/`, synthetic fixtures only, no real athlete data) plus one full
`main.run()` smoke test with mocked Vitruve/Teamworks clients confirming the
whole pipeline — match, transform, write, dedup-skip-on-rerun, multi-day
anomaly flagging, unrecognized-metric alerting. **The dedup read path's
shape is now confirmed against a real request/response pair** (see "Dedup /
idempotency" below) — the write path (`eventimport`) and the rest of the
pipeline are still **not yet verified against the real live APIs**. See
"Before turning the schedule on" below.

Still open/unimplemented:
- Manual-mapping-form fallback (Approach B) for athletes the name match
  can't resolve — currently just logged as `athlete_unmatched` /
  `athlete_ambiguous_name` counts, not auto-resolved against a backup form.
- The 13 excluded metrics (1RM, Fatigue, Jump *) — add their AMS columns and
  uncomment the matching keys in `vitruve_sync/transform.py`'s
  `KNOWN_METRIC_FIELDS` once done.
- No update path for a unit whose rep count grows after first import (see
  "Dedup" below) — accepted trade-off of the synchronise-based dedup
  design, not an oversight.

Diagnostic script `scripts/pull_vitruve_last_week.py` (read-only, Vitruve
side only) remains for capturing fresh samples if needed.

Diagnostic script `scripts/pull_teamworks_events.py` (read-only, Teamworks
side only — calls `synchronise` via the real `TeamworksClient`, never
`eventimport`) exists to pull real events back for inspection: run the real
sync once via `workflow_dispatch`, then run this script with the resulting
Teamworks userId(s) and unit ids (`workoutId:exerciseId`, from the sync's
own log lines) passed as `USER_IDS`/`CANDIDATE_UNIT_IDS` to confirm
`find_existing_unit_ids`'s extraction logic actually locates them in a live
response, and inspect the saved raw JSON (`samples/teamworks-events-*.json`,
gitignored) to double-check the payload shape by eye.

**Note on sample data realism:** the CSV/JSON sample pulled so far has
implausible magnitudes for force/power/weight fields (e.g. "Mean Force"
~1 N, "Weight" ~0.1 kg on a squat) — consistent with this being Vitruve
sandbox/demo data rather than physiologically real numbers (the API docs'
own example `/users` response uses celebrity names, so a demo org is
plausible). Treat the *structural* findings below as reliable; don't read
sports-science meaning into the specific numeric values until we're pointed
at production data.

Source docs in repo root:
- `vitruve_api_docs.pdf` — Vitruve Exports API
- `teamworks_api_docs.docx` — Teamworks AMS v1 API (OpenAPI excerpts for
  `usersynchronise` and `eventimport`)
- `teamworks_event_read_endpoints.md` — `synchronise` (event) and
  `eventsearch` OpenAPI excerpts, pasted once dedup design needed them;
  `synchronise` is the one actually used (see below), `eventsearch`'s
  excerpt is kept for history only
- `AMS_EVENTIMPORT_NOTES.md` — field-tested notes from a prior AMS integration
  (`usss-mocap`) covering gotchas not in the official docs

## Vitruve Exports API (from `vitruve_api_docs.pdf`)

- Base: `https://api-exports.vitruve.fit`, auth via `x-api-key` header, org-scoped key.
- `GET /vbt-workouts` — completed workouts for the org. Filtered by a coarse
  date-range enum (`this-week`, `last-7days`, `last-30days`, `last-12months`,
  `all`, etc.) — **no arbitrary `since=timestamp` cursor**. Paginated via
  `offset`/`limit` (max 100). Deeply nested: workout → exercises[] → series[]
  → repetitions[] → metricValues[] (`{metric, value, unit}` triples). The
  metric set is not fixed per rep — concentric vs eccentric reps can carry
  different metrics, and metric names are free-text display labels (e.g.
  `"Mean Power per Body Weight (MPV)"`), not stable keys. Response has no PII
  beyond `userId` (a UUID) — no names/emails in this endpoint.
- `GET /users` — org roster: `id` (UUID), `name`, `surname`, `email`.
  Candidate join key for athlete matching. Contains PII — never log/print
  the name/email fields, only `id`.
- `GET /legacy-ids` — maps legacy string IDs to new UUIDs; only relevant if
  USSS has pre-migration Vitruve data.
- Because there's no delta cursor, **dedup must happen on our side** — every
  scheduled run re-pulls a window that overlaps previously-imported data.

## Teamworks AMS API (from `teamworks_api_docs.docx` + `AMS_EVENTIMPORT_NOTES.md`)

Three v1 endpoints matter for this integration; all use **HTTP Basic Auth**
(base64 `username:password` in the `Authorization` header — no session/token
for v1) plus an optional `X-APP-ID` header to identify our integration to
Teamworks support, and all require `?informat=json&format=json` on the URL.

**`POST /api/v1/usersynchronise`** — roster lookup, used for athlete matching
and to resolve the `userId` needed on every `eventimport` call.
- Cursor-paginated (100/page): `{"paginate": "True", "cursor": ""}`, follow
  `cursor` until null/empty.
- Supports delta sync via `lastSynchronisationTimeOnServer` (pass `0` for a
  full pull, persist the value returned and pass it back next time to get
  only changed/new users) — this is the recommended pattern per Teamworks'
  own docs, cheaper than a full pull every run. **As implemented:** full
  pull every run (`lastSynchronisationTimeOnServer: 0` always) — delta sync
  needs a persisted, merged local roster cache (handling `mergedUsers`/
  `idsOfDeletedUsers`) which is real complexity not worth taking on blind at
  this org's roster size. Revisit only if the roster grows enough to matter.
- **Confirmed against a real call** (previously "detect by shape,
  undocumented" — now verified): the user list lives under a top-level
  `"users"` key, with objects shaped like `{"userId": 13354, "firstName":
  "...", "lastName": "...", "dob", "middleName", "emailAddress", "knownAs",
  "organisationalId", "username", "phoneNumbers", "sex", "uuid"}`. Code uses
  `"users"` directly now, falling back to the old shape-detection heuristic
  (first list-of-dicts value in the response) only if that key is ever
  absent — cheap insurance against a future API change, not the primary
  path anymore. Note **`emailAddress` does exist on the Teamworks side** —
  doesn't change the matching decision below since Vitruve's `/users` has
  no email field at all, so there's still nothing to join on, but worth
  knowing it's there for other purposes.
- **Caveat:** if our API account is a restricted Coach-role account (likely,
  given the "only touch the Vitruve VBT form" access model), `usersynchronise`
  won't return users removed from a group the account can see. This needs to
  be confirmed with whoever provisions the Teamworks credential: the account
  needs visibility into the **full athlete roster** for matching purposes,
  which is a different permission than form read/write access — call this out
  explicitly when the account is set up, since "scoped to only the VBT form"
  and "can resolve every athlete's userId" are two separate asks.
- Do **not** use `/api/v1/groupmembers` for this — scoped to one named group,
  unreliable pagination, and a group-name typo or an athlete not yet added to
  that group produces an indistinguishable generic "not found."

**`POST /api/v1/eventimport`** — the write path. Creates or updates a form
"Event" (AMS forms are no-code; a "VBT session" is just an Event Form with a
custom label).
```json
{
  "formName": "Exact Form Name",
  "startDate": "dd/MM/yyyy",
  "finishDate": "dd/MM/yyyy",
  "startTime": "h:mm AM/PM",
  "userId": { "userId": 12345 },
  "rows": [
    { "row": 0, "pairs": [ { "key": "Field Name", "value": "stringified value" } ] }
  ]
}
```
- Every `value` is a string regardless of the field's real type in AMS.
- `key` must exactly match the field name configured in the AMS form builder
  (case-sensitive) — we need these exact strings from the user once the form
  exists, not guessed from a UI label.
- **Returns HTTP 200 even on failure.** Must always parse the body and check
  for `state == "SUCCESSFULLY_IMPORTED"` as an allowlist — never treat 200 as
  success by itself, and never try to enumerate failure strings.
- `existingEventId` **replaces the entire event's contents**, it does not
  merge — an update call must resend the full desired state.
- For a form with single-value ("event-level") fields plus a repeating table:
  `row: 0`'s `pairs` = only the event-level fields; `row: 1..N` = one table
  row each, `pairs` = only that row's table columns. A single-value field
  must never appear in more than one row (rejected even with a blank value on
  the extra rows). The whole event (event fields + full table) must go in one
  call — a second call on the same `existingEventId` clobbers rather than
  appends.
- Confirmed failure mode from the prior integration: submitting one
  `eventimport` call per fine-grained record "to be safe" is fine
  functionally but explodes event count (one run produced 3,000+ events from
  data that should have produced ~105). **Batch into the fewest events that
  make semantic sense.**

**`POST /api/v1/synchronise`** (event, not `usersynchronise`) — read path
used for dedup (see "Dedup / idempotency" below). **Course-corrected from
the original plan:** this doc originally picked `/api/v1/eventsearch`
instead, reasoning that Teamworks' own docs recommend `synchronise` for a
local-cache pattern and `eventsearch` for server-side date filtering
without one. In practice, a real tested request/response pair (provided
directly, not from the OpenAPI excerpt) showed `/api/v1/synchronise` is
what actually works for this — code now calls that endpoint. `eventsearch`
was never confirmed against a live call and is no longer used; the OpenAPI
excerpt for it is kept in `teamworks_event_read_endpoints.md` for history
only.
- Request (confirmed): `formName` (singular string, exact match),
  `startDate` (`dd/mm/yyyy`) — we use 8 days back from run date — and
  `userIds` (array of ints, **mandatory**: omitting it returns no events
  for anyone, not "all events", so the code always short-circuits rather
  than ever calling without one). Pagination, per the OpenAPI excerpt (not
  yet exercised by a real multi-page response): omit `pagination` on the
  first page, then send `{"pagination": {"paginate": true, "cursor":
  "..."}}` on subsequent pages.
- Response (confirmed): events live under a top-level `"export"` key —
  `{"export": {"events": [...]}}` — alongside a top-level
  `lastSynchronisationTimeOnServer` we don't use (this design never
  persists it — see "As implemented" below). The pagination cursor's exact
  location on this endpoint isn't confirmed beyond a single-page real
  example, so code checks both a top-level `cursor` and an `export`-nested
  one rather than betting on one.
- **Per-event custom-field shape is now confirmed**, not guessed: each
  event's `rows[0]["pairs"]` (row 0 = event-level fields) contains
  `{"key": "Vitruve ID", "value": "..."}` — exactly matching how
  `transform.build_event_payload` writes it. Extraction now tries that
  location first, falling back to a recursive scan of the whole event
  object only as a safety net against a future shape change. This closes
  out what was previously flagged as the single biggest unverified
  assumption in the whole integration.
- **Observed but not yet accounted for:** the real example's `rows[0]`
  also contained `Sex`, `DOB`, and `Age` pairs we never send via
  `eventimport` — likely default Teamworks profile fields the form (or
  platform) auto-attaches, not something our code manages. Worth
  confirming with whoever administers the form; in the meantime, note DOB
  is PII — nothing in this pipeline logs full event dumps (only IDs/counts
  — see Observability), and the diagnostic script below writes raw events
  only to the gitignored `samples/` directory, never to source control.

## Proposed architecture

```
[Scheduled trigger] → [Extract: Vitruve] → [Match: athlete resolution]
  → [Transform: flatten] → [Dedup check against Teamworks] → [Load: Teamworks AMS]
```

- **Runner**: GitHub Actions on a cron schedule. `API_KEY` (Vitruve) already
  lives in repo secrets; Teamworks Basic Auth credentials too (`A360_USER` /
  `A360_PASSWORD`), scoped to read/write only the Vitruve VBT form plus
  roster-read (see caveat above).
- **State persistence**: none — no local file, no external DB. Dedup is
  answered fresh every run by querying Teamworks' own `synchronise`
  endpoint for what's already there. See "Dedup / idempotency" below for
  the full reasoning and the one real risk this carries.

## Athlete matching — settled: full name is the ground-truth join key

| Approach | How | Tradeoff |
|---|---|---|
| A. Live match via `/users` | Match Vitruve `/users` to Teamworks roster (`usersynchronise`) by email each run | Dead — the real `/users` pull has no `email` field, see below |
| B. Hardcoded mapping form | Separate Teamworks form: `vitruveUserId → teamworksAthleteId`, maintained manually | No fuzzy-matching risk, needs manual upkeep |
| C. Full-name match | Match Vitruve `/users` (`name` + `surname`) to Teamworks `usersynchronise` (first + last name) as one exact-match unit | Settled primary — see below |

**Updated after pulling the real `/users` endpoint:** contrary to the docs'
example response, the actual org's `/users` records only carry `id`, `name`,
`surname` — **no `email` field is present.** That kills approach A. Decision:
**(first name, last name) together is the ground-truth join key** — it's the
highest-fidelity data genuinely common to both systems. Match by comparing
the normalized (case-insensitive) full-name tuple from Vitruve `/users`
against Teamworks `usersynchronise` records directly, rather than a
partial-match cascade — the exact matching implementation (single query vs.
building an index first) is an implementation detail, but the join key
itself is the full name pair, not a fallback chain of partial matches.
Know its limits going in: no fuzzy matching, no accent/unicode
normalization, no handling of hyphens/middle names/suffixes, and a genuine
duplicate full name in the org produces an ambiguous "no unique match," not
a wrong-but-silent one. **Approach B (manual mapping form) is the fallback
for anything that doesn't resolve uniquely** — surface those cases for a
human to resolve rather than guessing. If a Teamworks user object ever does
turn out to expose an email field once we test `usersynchronise` live, that
would be a legitimate future upgrade, but the design doesn't depend on it.

## Learnings from first real sample (`/vbt-workouts?date=last-7days` + website CSV export)

Cross-referenced a real API pull against the human-facing CSV export Vitruve's
website generates for the same two sessions, for one athlete. This confirmed
some assumptions and overturned others:

1. **The CSV export — what a coach actually looks at — only ever contains
   `Type* = concentric` rows.** Eccentric-phase data exists in the raw API
   (`repetitions[]` entries with `"type": "eccentric"`) but Vitruve's own
   default export drops it entirely. The CSV's `# Rep.` numbering (rep 1, 2,
   3...) counts concentric reps only.
2. **Concentric and eccentric are separate `repetitions[]` entries, not a
   paired concentric+eccentric reading per physical rep**, and pairing is
   **not reliably 1:1 or alternating** — real series were observed with a
   trailing unpaired eccentric, and back-to-back eccentric entries with no
   concentric between them. Treating "one physical rep" as "one concentric
   entry, ignore eccentric" sidesteps this pairing ambiguity entirely and
   matches what coaches already see in the CSV.
3. **Metric vocabulary is confirmed and enumerable.** Concentric reps carry
   27 distinct metrics, eccentric reps carry a 22-metric subset (missing
   `1RM`, `1RM / Body Weight`, and the three `Fatigue` metrics — all
   concentric-phase-only concepts). The metric name strings in the API
   response match the CSV column headers exactly, minus the parenthetical
   unit suffix (e.g. API `"Mean Propulsive Velocity"` ↔ CSV `"Mean
   Propulsive Velocity (m/s)"`) — the CSV headers are a reliable, complete
   reference for the full metric-to-AMS-field mapping table:
   `1RM`, `1RM / Body Weight`, `Acceleration Index`, `Fatigue (PV)`,
   `Fatigue [MPV]`, `Fatigue [MV]`, `Jump Contact Time`, `Jump Contraction
   Time`, `Jump Flight Time`, `Jump Height`, `Jump Modified RSI`, `Jump Net
   Impulse`, `Jump Positive Impulse`, `Jump RSI`, `Mean Acceleration`, `Mean
   Force`, `Mean Force / Body Weight`, `Mean Power [MPV]`, `Mean Power [MV]`,
   `Mean Power / Body Weight [MPV]`, `Mean Power / Body Weight [MV]`, `Mean
   Propulsive Velocity`, `Mean Velocity`, `Peak Acceleration`, `Peak Force`,
   `Peak Force / Body Weight`, `Peak Power`, `Peak Power / Body Weight`,
   `Peak Velocity`, `Peak Velocity / Body Weight`, `Repetition Duration`,
   `ROM (Range of Motion)`, `Time to Peak Velocity`, `Weight`, `Weight / Body
   Weight`.
4. **Metric applicability varies by exercise type** — jump-specific metrics
   (`Jump Height`, `Jump Flight Time`, etc.) and load-based metrics (`1RM`,
   `Fatigue`) are blank unless the exercise/rig supports them (e.g. no force
   plate, or a bodyweight exercise with no external load). The AMS table
   needs to tolerate blank cells per row — this is normal, not a data defect.
5. **A Vitruve `series` corresponds to a `# Set` in the CSV** — one series =
   one continuous recorded set. Use it directly as a "Set #" column rather
   than inventing a different aggregation.
6. **Important data-quality finding: a Vitruve "workout" is not guaranteed to
   be a single-day session.** The athlete's own two sessions were clean
   (each `workout.startedAt`/`completedAt` same day, matching what they
   actually did), but a different workout in the same pull had exercises
   whose `series[].completedAt` timestamps spanned multiple calendar days
   under one `completedAt`-marked-"completed" workout. **Do not trust
   workout-level `startedAt`/`completedAt` as the event date — derive it from
   the exercise's own series `completedAt` timestamps instead**, and treat
   any exercise whose series span more than one calendar day as a
   data-quality anomaly to log/alert on rather than something to silently
   resolve — this may be stale/left-open workout data, or possibly seeded
   demo data, not a case worth building complex cross-day-splitting logic
   for in v1.

## Transform — finalized against the real "Vitruve VBT" form

The form now exists in Teamworks. Actual field list:

- **Non-table (row 0) field:** `Exercise Name` — the only event-level field.
  Session date/time is *not* a form field; it's carried on the top-level
  `eventimport` payload (`startDate`/`finishDate`/`startTime`), derived from
  the exercise's series `completedAt` per finding 6 above, not workout-level
  timestamps.
- **Table fields:** `Type` (concentric/eccentric), `Set` (see below), plus
  22 metric columns, each named exactly `f"{metric} ({unit})"` using
  Vitruve's own metric/unit strings — e.g. `Mean Propulsive Velocity (m/s)`,
  `Mean Power [MPV] (W)`. This is the full 22-metric vocabulary shared by
  both concentric and eccentric reps (confirmed against the 27/22 lists
  above) — **currently excludes 13 concentric-only/jump-only metrics**:
  ```
  1RM (kg)
  1RM / Body Weight (%)
  Fatigue (PV) (%)
  Fatigue [MPV] (%)
  Fatigue [MV] (%)
  Jump Contact Time (ms)
  Jump Contraction Time (ms)
  Jump Flight Time (ms)
  Jump Height (m)
  Jump Modified RSI (m/s)
  Jump Net Impulse (N)
  Jump Positive Impulse (N)
  Jump RSI (m/s)
  ```
  These aren't hypothetical gaps — the sampled squat data already had real
  `1RM`/`1RM / Body Weight` values on some concentric reps that would
  silently vanish with no error, since a missing column and a blank cell
  look identical from the API's perspective. Given the stated design
  principle ("no harm in extra fields, harm in missing ones"), added to the
  form. Note the 8 `Jump *` metrics are sourced from the CSV header list,
  not yet observed in a real API response (this org's sampled sessions had
  no jump-mat/force-plate exercise), so their exact metric-name strings are
  unconfirmed against the live API — worth a quick sanity check against a
  real jump-mat session once one exists.

**Finalized shape: one `eventimport` call per exercise entry, one table row
per repetition (concentric AND eccentric, not concentric-only as originally
recommended).** Tagging each row with `Type` sidesteps the eccentric/
concentric pairing ambiguity from finding 2 entirely — rows are a straight
1:1 flatten of the `repetitions[]` array, no pairing logic needed.

**`Set` numbering:** raw `series` UUIDs aren't meaningful to a coach, so
assign ascending integers per exercise (1, 2, 3...) by sorting that
exercise's `series` by `completedAt` and numbering in order — every
repetition row carries its parent series' number. Sorting explicitly by
`completedAt` (rather than trusting raw API array order) is a cheap
safeguard even though the two are likely already the same.

**Metric-key safety:** only ever emit a `pairs` entry for a metric whose
`f"{metric} ({unit})"` string is a known, confirmed form column. An
unrecognized metric name should be logged/alerted on, not silently dropped
*or* sent as an unknown key — untested what `eventimport` does with a `key`
that has no matching form field, so don't rely on it being harmless. Worth
confirming empirically during the first smoke test (send one row with a
deliberately-unknown key and see what comes back).

Per-repetition metric presence is confirmed to vary at the **individual
repetition** level, not just by concentric/eccentric type — two reps of the
same type and exercise can have different subsets of `metricValues` present
(e.g. `Mean Acceleration` shows up on some concentric reps but not others in
the same series). The transform must check what's actually present per rep,
never assume a fixed set per `Type`.

## Load into Teamworks AMS

- `POST /api/v1/eventimport`, Basic Auth, `X-APP-ID` header identifying this
  integration (e.g. `usss.vitruve-integration.v1`).
- One call per (athlete, workout, exercise) unit per the transform section
  above (all repetitions, concentric and eccentric, tagged by `Type`) —
  never split a single event's event-level fields and table across multiple
  calls, and never call twice against the same `existingEventId` expecting
  a merge.
- Always parse the response body; success is `state ==
  "SUCCESSFULLY_IMPORTED"` (allowlist, not blocklist). Any other state, or a
  transport-level error, counts as a write failure for observability
  purposes.
- Before wiring this into the scheduled pipeline: smoke-test a single
  real record end-to-end by hand, per the field notes — a wrong assumption
  at scale is expensive to clean up in a live AMS instance.

## Dedup / idempotency

**Superseded design decision:** v1 originally planned a git-committed JSON
state file (`state/dedup_state.json`) as the dedup source of truth, with
row-count comparison to detect late-arriving reps and an update-via-
`existingEventId` path. That's been replaced with a simpler, self-healing
design that queries Teamworks itself fresh every run. The old
row-count/state-file reasoning is kept here only as history, not as the
current design.

**Also superseded (endpoint choice):** the stateless redesign originally
planned to use `/api/v1/eventsearch` for the existence check, reasoning
from Teamworks' own docs about local-cache vs. server-side-filtering
patterns. A real tested request/response pair showed that reasoning didn't
hold in practice — `/api/v1/eventsearch` was never actually confirmed
against a live call, and `/api/v1/synchronise` is what verifiably works.
Code uses `synchronise`; the paragraphs below describe the current,
corrected design.

**As implemented (`vitruve_sync/teamworks_client.py::find_existing_unit_ids`,
called once per run from `main.py`):** no local state at all. Each run:

1. Resolves athlete matches and computes this run's candidate unit IDs
   (`f"{workoutId}:{exerciseId}"`) as before.
2. Makes **one** `POST /api/v1/synchronise` call — `formName: "Vitruve
   VBT"`, `userIds` scoped to this run's matched athletes (mandatory on
   this endpoint — omitting it returns no events for anyone, not "all
   events"), `startDate` 8 days back (wider than Vitruve's own
   `last-7days` pull, as a buffer against timezone edge cases) — and
   collects every returned event's `Vitruve ID` value from `rows[0]["pairs"]`.
3. Any candidate unit already found there is **skipped**; everything else
   is created fresh via `eventimport` (never with `existingEventId` — this
   design only ever creates, it never updates an existing event).

Teamworks itself is the single source of truth, queried fresh every run —
no risk of a lost/corrupted local file causing duplicate events, and no
separate git-commit step needed in the GitHub Actions workflow.

**Trade-off accepted along with this:** because it's create-only, an
exercise that gains more reps *after* first import (the multi-day-workout
anomaly case above) will never be picked up on a later run — its unit ID
is already "found," so it's skipped forever. Given that case is already
treated as a data-quality anomaly to log/alert on rather than solve (see
finding 6), this was judged an acceptable simplification, but it is a real
behavior change from "resend full state on change" to "first import wins."

**Previously flagged as the single biggest unverified risk — now
confirmed:** a real `synchronise` request/response pair (real endpoint,
fake data, structure confirmed accurate) showed each event's custom
fields live in `rows[0]["pairs"]` as `{"key": "Vitruve ID", "value":
"..."}` — not a flat top-level key, and not under `eventsearch` at all.
`_find_candidate_unit_id` now checks that confirmed location first, with
the old defensive recursive scan kept only as a fallback against a future
shape change. **Still worth a live end-to-end check before turning the
cron on**, since this confirms the read-side shape but not yet a full
write-then-read round trip in production: run `workflow_dispatch` once,
then use `scripts/pull_teamworks_events.py` (or run `workflow_dispatch`
again) to confirm the second pass sees the first run's units as
already-existing, not re-created.

## Credentials

- Vitruve: `API_KEY` (already in repo secrets), `x-api-key` header.
- Teamworks: `A360_USER` / `A360_PASSWORD` (repo secrets, Basic Auth), plus
  fixed `X-APP-ID: usss.vitruve-integration.v1`.
- Access-model ask for whoever provisions the Teamworks account: write access
  limited to the Vitruve VBT form, **and** read access to the full user
  roster via `usersynchronise` for athlete-matching — confirm this doesn't
  get inadvertently blocked by the same scoping that restricts form access,
  per the Coach-account caveat above.

## Implementation (`vitruve_sync/`)

- `config.py` — constants: Teamworks base URL
  `https://usopc.smartabase.com/athlete360-usss`, form name `Vitruve VBT`,
  AMS field name constants, `LOCAL_TIMEZONE = "America/Denver"` (assumed —
  confirm against a real smoke test), `VITRUVE_DATE_RANGE = "last-7days"`,
  `TEAMWORKS_SEARCH_LOOKBACK_DAYS` for the `synchronise` dedup window's
  `startDate`.
- `vitruve_client.py` / `teamworks_client.py` — stdlib `urllib` only, no
  external dependencies. All v1 Teamworks endpoints require
  `?informat=json&format=json` on every call (in the OpenAPI spec, easy to
  miss) — baked into `TeamworksClient._post`. `teamworks_client.py` also
  holds `find_existing_unit_ids` (the `synchronise`-based dedup check,
  confirmed shape) and its extraction helpers — see "Dedup / idempotency"
  above.
- `matching.py` — full-name join per "Athlete matching" above.
- `transform.py` — `KNOWN_METRIC_FIELDS` is the live 22-metric AMS column
  whitelist; the 13 excluded metrics are commented out inline, ready to
  uncomment once those AMS columns exist. `build_event_payload` always
  creates (never sends `existingEventId`) — dedup happens entirely before
  this is called, via `main.py`'s two-pass flow.
- `main.py` — pass 1 resolves athlete matches and computes this run's
  candidate unit IDs; one `find_existing_unit_ids` call checks all of them
  against Teamworks at once; pass 2 builds and writes only the units not
  already there. Logs only IDs/counts, never names/emails.
- `.github/workflows/vitruve_sync.yml` — `cron: "*/30 * * * *"` +
  `workflow_dispatch`. No state to commit back to the repo anymore, so no
  git-write permissions or commit step needed. **Scheduled workflows only
  run on the repo's default branch** — this won't fire until merged there.
  `workflow_dispatch` works from any branch that has the file, so it's
  usable for a manual smoke test before merging.
- `tests/` — 18 unit tests against synthetic fixtures (no real athlete
  data), covering matching, transform, and the synchronise dedup extraction
  logic in isolation. Verified separately with a full `main.run()` smoke
  test using mocked Vitruve/Teamworks clients (not committed as a test — no
  real API access in the environment this was built in) confirming the
  end-to-end wiring: match → build payload → write → skip-on-rerun →
  anomaly/unknown-metric alerting all fired correctly against fixture data.

**Deliberately not implemented in v1** (see "Status" above): the manual
mapping-form fallback for unresolved athlete names.

**Before turning the schedule on:** the dedup read path's shape is now
confirmed (see "Dedup / idempotency"), but the pipeline as a whole —
including the `eventimport` write path — has never been run against the
real Vitruve/Teamworks APIs end to end. Trigger one `workflow_dispatch` run
by hand first, then run it again shortly after and confirm the second
run's log shows the first run's units as already-existing (not
re-written). Per the field notes, a wrong assumption caught early is
cheap; the same mistake repeated every 30 minutes into a live AMS instance
is not.

## Observability

- Log per-run summary counts (pulled / matched / unmatched / written /
  skipped-duplicate / Teamworks-write-failed) — never log athlete
  names/emails, only IDs and counts. This isn't just an internal
  convention: the repo is public, so anything printed here lands in a
  publicly-readable GitHub Actions log. `vitruve_user_not_found` cases log
  the Vitruve `userId` (UUID) and unit id per occurrence (see `main.py`) so
  a recurring miss can be told apart from a one-off, without ever printing
  a name.
- Treat every `eventimport` response's `state` field as the real success
  signal, not the HTTP status (200 is returned even on failure).
- Alert on unmatched-athlete rate, unrecognized metric names, Teamworks
  write failures, Vitruve 401/500s. Slack is the existing internal-comms
  tool for this.
- Pull window should overlap (e.g. daily run on `last-7days`, not
  `this-week`) so a missed run doesn't silently drop data — dedup logic
  handles the rest.

## Reference implementation to reuse patterns from

`AMS_EVENTIMPORT_NOTES.md` points to `mocap_report_gui/smartabase_client.py`
in the `usss-mocap` repo as a working reference for v1 auth, paginated user
lookup, and event submission (`_headers`, `_fetch_all_athletes`,
`get_athlete_id`, `_post_event`). `get_athlete_id` is worth reviewing once we
have access — it solves the same "match a name to a Teamworks user" problem,
though its cascade (last name → first initial → full first name) is a looser
match than what we've settled on here (exact full-name-tuple match) — reuse
its auth/pagination plumbing regardless, but don't assume its matching logic
should be copied as-is. This session's GitHub access is scoped only to
`usss-vitruve`, so that file hasn't been pulled in directly — worth asking
the user to copy the relevant functions over (or grant access) once we start
writing the Teamworks client, rather than re-deriving the same
already-solved auth/pagination code.

## Org tooling context

Teamworks AMS is not part of USSS's already-provisioned stack (M365/Slack,
Smartsheet/Tableau, FIS DB + MS-SQL mirror + Dartfish, Salesforce x3, Docusign,
Zendesk, Adobe CC) — this integration fills a real gap rather than duplicating
an existing tool.
