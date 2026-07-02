# usss-vitruve — Vitruve → Teamworks AMS Integration

Custom integration pulling VBT (velocity-based training) data from Vitruve and
loading it into US Ski & Snowboard's Teamworks AMS instance, against a
dedicated Vitruve VBT form.

## Status

Conceptual plan agreed. Awaiting from user:
- Vitruve API Docs (a version already in repo: `vitruve_api_docs.pdf`, may be superseded)
- Teamworks AMS API Docs
- Notes on Teamworks API Implementation

Nothing has been implemented yet — no source code exists beyond this file and
the Vitruve API PDF.

## Vitruve Exports API (from `vitruve_api_docs.pdf`)

- Base: `https://api-exports.vitruve.fit`, auth via `x-api-key` header, org-scoped key.
- `GET /vbt-workouts` — completed workouts for the org. Filtered by a coarse
  date-range enum (`this-week`, `last-7days`, `last-30days`, `last-12months`,
  `all`, etc.) — **no arbitrary `since=timestamp` cursor**. Paginated via
  `offset`/`limit` (max 100). Deeply nested: workout → exercises[] → series[]
  → repetitions[] → metricValues[] (`{metric, value, unit}` triples). The
  metric set is not fixed per rep — concentric vs eccentric reps can carry
  different metrics, and metric names are free-text display labels (e.g.
  `"Mean Power per Body Weight (MPV)"`), not stable keys.
- `GET /users` — org roster: `id` (UUID), `name`, `surname`, `email`.
  Candidate join key for athlete matching.
- `GET /legacy-ids` — maps legacy string IDs to new UUIDs; only relevant if
  USSS has pre-migration Vitruve data.
- Because there's no delta cursor, **dedup must happen on our side** — every
  scheduled run re-pulls a window that overlaps previously-imported data.

## Proposed architecture

```
[Scheduled trigger] → [Extract: Vitruve] → [Match: athlete resolution]
  → [Transform: flatten] → [Dedup filter] → [Load: Teamworks AMS] → [State commit]
```

- **Runner**: GitHub Actions on a cron schedule. `API_KEY` (Vitruve) already
  lives in repo secrets; Teamworks credentials will go there too, scoped to
  read/write only the Vitruve VBT form.
- **State persistence**: no external DB — plan is to commit a small JSON
  state file (processed workout IDs / high-water `completedAt`) back to the
  repo after each successful run, unless the Teamworks API turns out to
  support querying existing form entries for dedup instead.

## Athlete matching — two approaches, not yet decided

| Approach | How | Tradeoff |
|---|---|---|
| A. Live match via `/users` | Match Vitruve `/users` to Teamworks roster by email each run | Self-maintaining, depends on email consistency between systems |
| B. Hardcoded mapping form | Separate Teamworks form: `vitruveUserId → teamworksAthleteId`, maintained manually | No fuzzy-matching risk, needs manual upkeep |

Leaning **B as source of truth, A as a reconciliation check** (periodic
report of Vitruve users with no mapping row) — final call pending real data
comparison between the two systems.

## Transform — open design decision

Row granularity for the Teamworks VBT form is undecided pending the actual
form field layout:
- Per-repetition (most granular, highest volume)
- Per-series/set (aggregated)
- Per-exercise (coarsest)

And whether the form has fixed columns per known metric name (requires
enumerating Vitruve's metric vocabulary and mapping each to a field) or a
repeating/key-value subform (no fixed schema needed). Any metric name
encountered that isn't in the mapping should be logged/alerted on, not
silently dropped — Vitruve's metric labels are display strings, not stable
keys.

## Load into Teamworks AMS — pending API docs

Need to confirm once Teamworks docs are provided:
- Whether Teamworks supports an upsert/idempotency key per record (removes
  need for our own dedup state) or only pure create (dedup state mandatory).
- Bulk/batch write support vs one-record-at-a-time, and rate limits.
- Auth mechanism and its scoping (must be limited to the Vitruve VBT form
  only, per the integration's access model).

## Observability

- Log per-run summary counts (pulled / matched / unmatched / written /
  skipped-duplicate) — never log athlete names/emails/PII, only IDs and
  counts.
- Alert on unmatched-athlete rate, unrecognized metric names, Teamworks
  write failures, Vitruve 401/500s. Slack is the existing internal-comms
  tool for this.
- Pull window should overlap (e.g. daily run on `last-7days`, not
  `this-week`) so a missed run doesn't silently drop data — dedup logic
  handles the rest.

## Org tooling context

Teamworks AMS is not part of USSS's already-provisioned stack (M365/Slack,
Smartsheet/Tableau, FIS DB + MS-SQL mirror + Dartfish, Salesforce x3, Docusign,
Zendesk, Adobe CC) — this integration fills a real gap rather than duplicating
an existing tool.
