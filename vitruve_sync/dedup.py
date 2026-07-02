"""Committed-JSON dedup state: {unit_id: {existingEventId, rowCount}}.

Dedup signal is row count, not a full repetition-ID set - the AMS form has
no per-repetition identifier (only Type/Set/metrics), so "the same number of
rows we last wrote" is the practical signal available. If a run sees more
rows than last time for a unit, it rebuilds and resends the full row set via
existingEventId (eventimport replaces rather than merges - a partial resend
would drop previously-imported rows).

Known limitation: this is self-tracked, not reconciled against Teamworks
directly. Losing this file (or a failed commit) risks duplicate events on
the next run. /api/v1/eventsearch or /api/v1/synchronise could rebuild it
from Teamworks as a future self-healing enhancement, but their response
shape for custom form fields is undocumented and unverified against a real
call - not worth guessing blind. See CLAUDE.md.
"""

import json
import os


def load_state(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def needs_write(state, unit_id, row_count):
    prior = state.get(unit_id)
    if prior is None:
        return True, None
    if prior["rowCount"] != row_count:
        return True, prior["existingEventId"]
    return False, prior["existingEventId"]


def record(state, unit_id, existing_event_id, row_count, teamworks_user_id):
    state[unit_id] = {
        "existingEventId": existing_event_id,
        "rowCount": row_count,
        "teamworksUserId": teamworks_user_id,
    }
