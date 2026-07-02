#!/usr/bin/env python3
"""Read-only diagnostic: pull real events back from Teamworks via eventsearch.

This exercises the exact same TeamworksClient code path (auth, pagination,
cursor-key handling) that vitruve_sync/main.py uses for dedup, so a real run
here is the closest thing to verifying CLAUDE.md's flagged "biggest
unverified assumption": whether our "Vitruve ID" field is actually
extractable from a real eventsearch response, and in what shape.

Recommended flow:
  1. Trigger one real workflow_dispatch run of the actual sync (writes real
     events), and note the "wrote unit <workoutId>:<exerciseId> -> event ..."
     lines it logs.
  2. Run this script, passing those unit ids via CANDIDATE_UNIT_IDS, to
     confirm find_existing_unit_ids' extraction logic actually locates them.
  3. Trigger workflow_dispatch again and confirm its log shows those same
     units as "already in Teamworks" (skipped), not re-created.

Usage:
    A360_USER=... A360_PASSWORD=... python3 scripts/pull_teamworks_events.py

Optional env vars:
    FORM_NAME          - defaults to "Vitruve VBT" (vitruve_sync.config value)
    START_DATE         - dd/mm/yyyy, defaults to the same lookback window
                         main.py uses (today - 8 days)
    FINISH_DATE        - dd/mm/yyyy, defaults to today + 1 day
    USER_IDS           - comma-separated Teamworks userIds to scope the
                         search; omit to search the form across every user
                         the account can see
    CANDIDATE_UNIT_IDS - comma-separated "workoutId:exerciseId" strings you
                         already know were imported - the script reports
                         whether our extraction logic actually finds each one

Writes the full raw eventsearch response to ./samples/ for inspection;
that directory is gitignored since it may contain real athlete data.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone

from vitruve_sync.config import TEAMWORKS_FORM_NAME
from vitruve_sync.main import _search_window
from vitruve_sync.teamworks_client import TeamworksClient, TeamworksError, _find_candidate_unit_id


def fetch_all_events(client, form_name, start_date, finish_date, user_ids):
    events = []
    cursor = None
    base_body = {
        "formNames": [form_name],
        "startDate": start_date,
        "finishDate": finish_date,
        "paginate": True,
    }
    if user_ids:
        base_body["userIds"] = user_ids
    while True:
        body = dict(base_body)
        if cursor:
            body["cursor"] = cursor
        response = client._post("/api/v1/eventsearch", body)
        page_events = response.get("events", [])
        events.extend(page_events)
        print(f"  fetched page: {len(page_events)} event(s)")
        # eventsearch's own docs disagree with themselves on this field's
        # name - accept either, same as find_existing_unit_ids does.
        cursor = response.get("next_cursor") or response.get("cursor")
        if not cursor:
            break
    return events


def main():
    username = os.environ.get("A360_USER")
    password = os.environ.get("A360_PASSWORD")
    if not username or not password:
        print("Set A360_USER and A360_PASSWORD environment variables.", file=sys.stderr)
        sys.exit(1)

    form_name = os.environ.get("FORM_NAME", TEAMWORKS_FORM_NAME)
    default_start, default_finish = _search_window()
    start_date = os.environ.get("START_DATE", default_start)
    finish_date = os.environ.get("FINISH_DATE", default_finish)

    user_ids_env = os.environ.get("USER_IDS", "").strip()
    user_ids = [int(x) for x in user_ids_env.split(",") if x.strip()] if user_ids_env else None

    candidate_env = os.environ.get("CANDIDATE_UNIT_IDS", "").strip()
    candidate_unit_ids = {x.strip() for x in candidate_env.split(",") if x.strip()}

    client = TeamworksClient(username, password)

    print(
        f"Searching form={form_name!r} from {start_date} to {finish_date}"
        + (f" for userIds={user_ids}" if user_ids else " (all users the account can see)")
    )
    try:
        events = fetch_all_events(client, form_name, start_date, finish_date, user_ids)
    except TeamworksError as e:
        print(f"Teamworks API error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nFound {len(events)} event(s).")

    if candidate_unit_ids:
        print(f"\nChecking {len(candidate_unit_ids)} candidate unit id(s) against the real extraction logic:")
        found = set()
        for event in events:
            unit_id = _find_candidate_unit_id(event, candidate_unit_ids)
            if unit_id:
                found.add(unit_id)
        for unit_id in sorted(candidate_unit_ids):
            print(f"  {unit_id}: {'FOUND' if unit_id in found else 'NOT FOUND'}")
    else:
        print("\nNo CANDIDATE_UNIT_IDS given - inspect the raw dump below/on disk by eye instead.")

    os.makedirs("samples", exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join("samples", f"teamworks-events-{timestamp}.json")
    with open(out_path, "w") as f:
        json.dump(events, f, indent=2)
    print(f"\nWrote {len(events)} raw event(s) to {out_path} for inspection.")

    if events:
        print("\nFirst event (pretty-printed) for a quick look at the shape:")
        print(json.dumps(events[0], indent=2))


if __name__ == "__main__":
    main()
