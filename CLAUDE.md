# usss-vitruve — Vitruve → Teamworks AMS Integration

Custom integration pulling VBT (velocity-based training) data from Vitruve and
loading it into US Ski & Snowboard's Teamworks AMS instance, against a
dedicated Vitruve VBT form.

## Status

Conceptual plan agreed and restructured against real API docs for both sides
(see source docs below, all checked into repo root), then refined again
against a real `/vbt-workouts` sample pull cross-referenced with Vitruve's own
website CSV export for one athlete's morning sessions. That cross-reference
surfaced several structural findings (see "Learnings from first real sample"
below) that changed the row-granularity, dedup-key, and athlete-matching
recommendations from the first pass. Still waiting on the actual Teamworks VBT
form field layout — the user is designing that form based on the
recommendations in this file plus the real shape of a live Vitruve pull, so
the transform section below is a recommendation to validate against the form
once built, not a final spec.

Nothing has been implemented yet beyond a diagnostic script
(`scripts/pull_vitruve_last_week.py`, read-only, Vitruve side only) used to
capture a real sample response for testing.

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

Two v1 endpoints matter for this integration; both use **HTTP Basic Auth**
(base64 `username:password` in the `Authorization` header — no session/token
for v1) plus an optional `X-APP-ID` header to identify our integration to
Teamworks support.

**`POST /api/v1/usersynchronise`** — roster lookup, used for athlete matching
and to resolve the `userId` needed on every `eventimport` call.
- Cursor-paginated (100/page): `{"paginate": "True", "cursor": ""}`, follow
  `cursor` until null/empty.
- Supports delta sync via `lastSynchronisationTimeOnServer` (pass `0` for a
  full pull, persist the value returned and pass it back next time to get
  only changed/new users) — this is the recommended pattern per Teamworks'
  own docs, cheaper than a full pull every run.
- **Caveat (from field notes, not the official docs):** the user-list is
  wrapped under an implementation-specific key that isn't precisely
  documented — detect it by shape (first list-of-dicts value in the
  response) rather than hardcoding a key name. Field names on individual user
  objects also vary by instance (`userId`/`user_id`/`id`,
  `firstName`/`first_name`, etc.) — resolve with a small helper that tries
  candidate keys, don't assume one.
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

## Proposed architecture

```
[Scheduled trigger] → [Extract: Vitruve] → [Match: athlete resolution]
  → [Transform: flatten] → [Dedup filter] → [Load: Teamworks AMS] → [State commit]
```

- **Runner**: GitHub Actions on a cron schedule. `API_KEY` (Vitruve) already
  lives in repo secrets; Teamworks Basic Auth credentials will go there too
  (e.g. `TEAMWORKS_USERNAME` / `TEAMWORKS_PASSWORD`), scoped to read/write
  only the Vitruve VBT form plus roster-read (see caveat above).
- **State persistence**: no external DB — plan is to commit a small JSON
  state file back to the repo after each successful run, keyed by (athlete,
  workout, exercise) unit and storing all imported repetition IDs (both
  concentric and eccentric, per the finalized form) plus the
  `existingEventId` Teamworks returned, to support the skip-if-unchanged /
  resend-full-state-if-new-reps-appear logic — see Dedup section (updated
  after the first real sample pull surfaced a case where a workout's
  contents can't be assumed complete/final at first sight).

## Athlete matching — settled on name-cascade, given a real `/users` pull

| Approach | How | Tradeoff |
|---|---|---|
| A. Live match via `/users` | Match Vitruve `/users` to Teamworks roster (`usersynchronise`) by email each run | Self-maintaining, but depends on email consistency between systems, and Teamworks' user-object field names/shape aren't documented until we test live |
| B. Hardcoded mapping form | Separate Teamworks form: `vitruveUserId → teamworksAthleteId`, maintained manually | No fuzzy-matching risk, needs manual upkeep |
| C. Name-cascade | Match Vitruve `/users` `name`/`surname` to Teamworks `usersynchronise` first/last name | Same fragility class the prior integration already hit and documented a fix for (see below) |

**Updated after pulling the real `/users` endpoint:** contrary to the docs'
example response, the actual org's `/users` records only carry `id`, `name`,
`surname` — **no `email` field is present.** That kills approach A as primary
(there's nothing reliable to join on) and the org confirmed we'll match on
name instead. This is exactly the scenario `AMS_EVENTIMPORT_NOTES.md` already
covers from the prior AMS integration, so reuse its cascade rather than
re-deriving it: exact last name (case-insensitive) → narrow by first initial
→ narrow by full first name. Know its limits going in — no fuzzy matching, no
accent/unicode normalization, no handling of hyphens/middle names/suffixes,
and a genuine duplicate name in the org produces an ambiguous "no unique
match," not a wrong-but-silent one. **Approach B (manual mapping form) is the
fallback for anything the cascade can't resolve uniquely** — surface those
cases for a human to resolve rather than guessing. If a Teamworks user object
ever does turn out to expose an email field once we test `usersynchronise`
live, prefer it as a first-pass opportunistic match before falling through to
the name cascade, but don't design around it being there.

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
- **Table fields:** `Type` (concentric/eccentric), `Set.` (see below), plus
  22 metric columns, each named exactly `f"{metric} ({unit})"` using
  Vitruve's own metric/unit strings — e.g. `Mean Propulsive Velocity (m/s)`,
  `Mean Power [MPV] (W)`. This is the full 22-metric vocabulary shared by
  both concentric and eccentric reps (confirmed against the 27/22 lists
  above) — **currently excludes 13 concentric-only/jump-only metrics**:
  `1RM`, `1RM / Body Weight`, the three `Fatigue` metrics, and all 8
  `Jump *` metrics. These aren't hypothetical gaps — the sampled squat data
  already had real `1RM`/`1RM / Body Weight` values on some concentric reps
  that would silently vanish with no error, since a missing column and a
  blank cell look identical from the API's perspective. Given the stated
  design principle ("no harm in extra fields, harm in missing ones"), worth
  adding these 13 now while the form is still being built, rather than
  discovering the gap later from a loaded lift or a jump-mat session.

**Finalized shape: one `eventimport` call per exercise entry, one table row
per repetition (concentric AND eccentric, not concentric-only as originally
recommended).** Tagging each row with `Type` sidesteps the eccentric/
concentric pairing ambiguity from finding 2 entirely — rows are a straight
1:1 flatten of the `repetitions[]` array, no pairing logic needed.

**`Set.` numbering:** raw `series` UUIDs aren't meaningful to a coach, so
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

- **Updated: track dedup state at the repetition level, not (workoutId,
  exerciseId).** The multi-day-workout finding above means a given
  `(workoutId, exerciseId)` pair isn't guaranteed to have a stable, complete
  set of repetitions the first time it's seen — content could still be
  accumulating under that same workout ID. Tracking at the individual
  repetition-ID level (globally unique, immutable once it exists) is safe
  regardless of how upstream workout/series grouping behaves.
- Per (athlete, workout, exercise) unit: before building the event, check
  whether every repetition ID for that unit (concentric and eccentric — the
  form now includes both) is already in the committed state file.
  - All already seen → skip entirely, nothing changed.
  - Any new → rebuild the **full** current row set (previously-imported reps
    + new ones) and submit via `existingEventId` if we have one on file for
    this unit, since `eventimport` update replaces rather than merges (see
    Teamworks API notes above) — a partial resend would drop the
    previously-imported rows.
- This also naturally handles the ordinary (non-anomalous) case of an
  athlete doing the same exercise twice in one day under two different
  workout records, without extra logic.
- `existingEventId` returned from each successful `eventimport` call needs
  to be persisted in the state file per (athlete, workout, exercise) unit to
  support this update path.

## Credentials

- Vitruve: `API_KEY` (already in repo secrets), `x-api-key` header.
- Teamworks: Basic Auth username/password (new repo secrets, names TBD e.g.
  `TEAMWORKS_USERNAME` / `TEAMWORKS_PASSWORD`), plus a fixed `X-APP-ID`
  string.
- Access-model ask for whoever provisions the Teamworks account: write access
  limited to the Vitruve VBT form, **and** read access to the full user
  roster via `usersynchronise` for athlete-matching — confirm this doesn't
  get inadvertently blocked by the same scoping that restricts form access,
  per the Coach-account caveat above.

## Observability

- Log per-run summary counts (pulled / matched / unmatched / written /
  skipped-duplicate / Teamworks-write-failed) — never log athlete
  names/emails, only IDs and counts.
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
`get_athlete_id`, `_post_event`). `get_athlete_id` specifically is now
directly relevant, not just a pattern to imitate — it already implements the
same last-name → first-initial → full-first-name cascade we just settled on
for athlete matching. This session's GitHub access is scoped only to
`usss-vitruve`, so that file hasn't been pulled in directly — worth asking
the user to copy the relevant functions over (or grant access) once we start
writing the Teamworks client, rather than re-deriving the same
already-solved auth/pagination/matching code.

## Org tooling context

Teamworks AMS is not part of USSS's already-provisioned stack (M365/Slack,
Smartsheet/Tableau, FIS DB + MS-SQL mirror + Dartfish, Salesforce x3, Docusign,
Zendesk, Adobe CC) — this integration fills a real gap rather than duplicating
an existing tool.
