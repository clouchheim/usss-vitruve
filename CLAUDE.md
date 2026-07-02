# usss-vitruve — Vitruve → Teamworks AMS Integration

Custom integration pulling VBT (velocity-based training) data from Vitruve and
loading it into US Ski & Snowboard's Teamworks AMS instance, against a
dedicated Vitruve VBT form.

## Status

Conceptual plan agreed and restructured against real API docs for both sides
(see source docs below, all checked into repo root). Still waiting on the
actual Teamworks VBT form field layout — the user is designing that form based
on the recommendations in this file plus the real shape of a live Vitruve
pull, so the transform section below is a recommendation to validate against
the form once built, not a final spec.

Nothing has been implemented yet beyond a diagnostic script
(`scripts/pull_vitruve_last_week.py`, read-only, Vitruve side only) used to
capture a real sample response for testing.

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
  state file (processed `(workoutId, exerciseId)` pairs) back to the repo
  after each successful run. Vitruve workouts are "completed" records, so
  treating them as immutable once seen (skip rather than update) is the
  simplest correct behavior for v1 — see Dedup section.

## Athlete matching — leaning B as source of truth, A as reconciliation

| Approach | How | Tradeoff |
|---|---|---|
| A. Live match via `/users` | Match Vitruve `/users` to Teamworks roster (`usersynchronise`) by email each run | Self-maintaining, but depends on email consistency between systems, and Teamworks' user-object field names/shape aren't documented until we test live |
| B. Hardcoded mapping form | Separate Teamworks form: `vitruveUserId → teamworksAthleteId`, maintained manually | No fuzzy-matching risk, needs manual upkeep |

Still leaning **B as source of truth, A as a periodic reconciliation report**
(Vitruve users with no mapping row) — reinforced by the field notes' general
guidance to prefer a stable ID over name/email matching wherever the source
data supports it. Final call still pending real data from both systems.

## Transform — recommendation to validate against the real form

Now that we know `eventimport`'s actual shape (single-value fields in row 0 +
one repeating table), that structure maps cleanly onto the Vitruve nesting if
we pick the right unit of "one event":

**Recommendation: one `eventimport` call per (athlete, workout, exercise).**
- Event-level fields (row 0): Vitruve workout ID, exercise name, session
  date/time (from `completedAt`/`startedAt`) — these are genuinely
  single-valued per exercise performed in a session.
- Repeating table: **one row per repetition** — columns for series/set
  number, rep number, rep type (concentric/eccentric), plus one column per
  known Vitruve metric name (fixed mapping, e.g. `"Mean Propulsive Velocity"`
  → an AMS field). Rep-level preserves full fidelity and AMS tables handle
  arbitrary row counts fine, so there's no technical reason to pre-aggregate
  to set-level unless the form is intentionally designed coarser.
- This keeps event count bounded (one event per exercise actually performed,
  not per rep) while avoiding the "3,000+ stray events" failure mode from the
  prior integration, and it doesn't violate the single-value-field-per-row-0
  rule since exercise/date genuinely don't vary within one exercise's data.

Open until the form exists:
- Confirm this granularity against the actual field list the user designs —
  if the form is intentionally coarser (e.g. set-level aggregates only), the
  table-row mapping changes but the event-per-exercise boundary likely still
  holds.
- Exact metric-name → AMS-field-name mapping, sourced from the real form once
  built (`GET /api/v3/forms/{form_type}/{form_id}` can return authoritative
  field names later, but that's a v3/session-auth endpoint — useful as a
  build-time verification step, not required for v1).
- Any metric name encountered that isn't in the mapping should be
  logged/alerted on, not silently dropped.

## Load into Teamworks AMS

- `POST /api/v1/eventimport`, Basic Auth, `X-APP-ID` header identifying this
  integration (e.g. `usss.vitruve-integration.v1`).
- One call per (athlete, workout, exercise) unit per the transform section
  above — never split a single event's event-level fields and table across
  multiple calls, and never call twice against the same `existingEventId`
  expecting a merge.
- Always parse the response body; success is `state ==
  "SUCCESSFULLY_IMPORTED"` (allowlist, not blocklist). Any other state, or a
  transport-level error, counts as a write failure for observability
  purposes.
- Before wiring this into the scheduled pipeline: smoke-test a single
  real record end-to-end by hand, per the field notes — a wrong assumption
  at scale is expensive to clean up in a live AMS instance.

## Dedup / idempotency

- Vitruve workouts are immutable once completed (no evidence of an update
  path in their API), so v1 dedup is **skip if already imported**: maintain
  the committed state file of `(workoutId, exerciseId)` pairs already
  successfully written, and never re-submit them — no need to track
  `existingEventId` per unit for routine runs.
- `existingEventId`-based update is a possible future extension only if
  Vitruve is ever observed to mutate a "completed" workout's data
  retroactively — not needed for the initial build.

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
`get_athlete_id`, `_post_event`). This session's GitHub access is scoped only
to `usss-vitruve`, so that file hasn't been pulled in directly — worth asking
the user to copy the relevant functions over (or grant access) once we start
writing the Teamworks client, rather than re-deriving the same
already-solved auth/pagination code.

## Org tooling context

Teamworks AMS is not part of USSS's already-provisioned stack (M365/Slack,
Smartsheet/Tableau, FIS DB + MS-SQL mirror + Dartfish, Salesforce x3, Docusign,
Zendesk, Adobe CC) — this integration fills a real gap rather than duplicating
an existing tool.
