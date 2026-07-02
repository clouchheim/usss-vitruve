# Teamworks AMS v1 — event read endpoints

Pasted directly from `docs.ams.teamworksapp.com` by the user (not part of
the original `teamworks_api_docs.docx` upload). Kept here for reference
alongside that file and `AMS_EVENTIMPORT_NOTES.md`.

**Update — corrected after a real tested call:** this doc originally chose
`/api/v1/eventsearch` for the dedup check, reasoning from Teamworks' own
docs about local-cache vs. server-side-filtering patterns (below). That
reasoning didn't hold up against a real request/response pair: **`/api/v1/
synchronise` is the endpoint that actually works** for this, with
`formName` (singular) + `startDate` + `userIds` as the request and events
returned under a top-level `"export"` key — none of which matches either
endpoint's documented shape below exactly. `/api/v1/eventsearch` was never
confirmed against a live call and is no longer used anywhere in
`vitruve_sync/` — its excerpt below is kept for history only. See
`CLAUDE.md` "Dedup / idempotency" for the current, corrected design and the
confirmed per-event field shape (`rows[0]["pairs"]`, not a flat key or
`additionalProperties` sibling of `id`).

## (v1) Synchronise Event Data

Returns a list of events for a specified form.

**This is the preferred read pattern.** Cache results on first call, then
use `lastSynchronisationTimeOnServer` from the previous response to fetch
only changed/new events. Only fetch the full dataset on first sync.

Supports cursor pagination (100 events per page).

**Pagination:** pass pagination parameters as a nested object in the
request body:

```json
{
  "pagination": {
    "paginate": true,
    "cursor": "{{cursor}}"
  }
}
```

Omit `cursor` (or pass `null`) on the first page. Use the cursor value from
each response to fetch the next page.

**`lastSynchronisationTimeOnServer`:**

1. **Persisted** after every call.
2. **Passed back** as the lower time bound on the next call to retrieve only
   changed records.
3. **Never hardcoded to `0` or a fixed timestamp** — doing so forces a full
   history scan on every run, which causes server-level memory exhaustion
   under concurrent load.

**Error semantics:**
- `HTTP 500` on this endpoint typically means the request payload is too
  large — usually caused by requesting too many users without pagination,
  or `lastSynchronisationTimeOnServer: 0` on a large dataset.
- `HTTP 403` means the authenticated account does not have access to the
  specified form.

Endpoint: `POST /api/v1/synchronise`. Request requires `formName` (string)
and `lastSynchronisationTimeOnServer` (number, `0` for a full first pull).
Optional `userIds` (array of ints), `pagination` object as above. Response:
`lastSynchronisationTimeOnServer` (persist for next call) and `cursor`
(present when more pages remain, `null` on the final page).

## (v1) Search Event Data

Searches for events matching date range and user criteria.

**Note:** For most use cases, `POST /api/v1/synchronise` with local caching
and filtering is preferred. Use this endpoint when you specifically need
server-side date filtering without maintaining a local cache.

**Pagination:** pass pagination parameters at the top level of the request
body:

```json
{
  "paginate": true,
  "cursor": "{{cursor}}"
}
```

Omit `cursor` on the first page. The response includes `next_cursor` —
absent on the final page. Note this is a different field name than
`synchronise`'s response `cursor`.

Endpoint: `POST /api/v1/eventsearch`. Request:
- `formNames` (array of strings, required) — exact form name(s).
- `startDate` / `finishDate` (`dd/mm/yyyy`) — omit for no bound on that side.
- `startTime` / `finishTime` (`h:mm AM/PM`) — optional time-of-day filter.
- `resultsPerUser` (int) — cap results per user.
- `userIds` (array of ints).
- `paginate` (bool) / `cursor` (string, omit on first page).

Response: `{"events": [...], "cursor": "..."}` per the OpenAPI schema
excerpt, though the endpoint description text says the field is
`next_cursor` — **this inconsistency between the prose and the schema is
in the source docs themselves**, not introduced here; code should treat
whichever key is actually present at runtime as authoritative once
confirmed against a live call. Each event object in `events[]` has a
documented `id` (integer, the Teamworks event record ID) and
`additionalProperties: true` — meaning further fields exist (presumably the
form's own data) but their shape is not specified anywhere in this excerpt.
