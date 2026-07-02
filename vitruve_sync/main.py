"""Entrypoint: pull Vitruve, match athletes, transform, dedup, load into
Teamworks AMS. Run every ~30 min via .github/workflows/vitruve_sync.yml.

Dedup has no local state: each run asks Teamworks itself (via synchronise)
which of this run's candidate units already have an event, and only writes
the ones that don't - see teamworks_client.find_existing_unit_ids and
CLAUDE.md "Dedup / idempotency".

Never logs athlete names/emails - only Vitruve/Teamworks IDs and counts,
per CLAUDE.md's observability section.
"""

import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from vitruve_sync.config import TEAMWORKS_SEARCH_LOOKBACK_DAYS, VITRUVE_DATE_RANGE
from vitruve_sync.matching import AMBIGUOUS, UNMATCHED, build_name_index, match_athlete
from vitruve_sync.teamworks_client import TeamworksClient, TeamworksError
from vitruve_sync.transform import (
    UnschedulableExercise,
    build_event_payload,
    compute_unit_id,
    iter_exercise_units,
)
from vitruve_sync.vitruve_client import VitruveClient, VitruveError


def log(message):
    print(message, flush=True)


def _search_window():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=TEAMWORKS_SEARCH_LOOKBACK_DAYS)
    return start.strftime("%d/%m/%Y")


def run():
    api_key = os.environ["API_KEY"]
    teamworks_username = os.environ["A360_USER"]
    teamworks_password = os.environ["A360_PASSWORD"]

    vitruve = VitruveClient(api_key)
    teamworks = TeamworksClient(teamworks_username, teamworks_password)

    counts = Counter()

    log("Pulling Vitruve users and roster from Teamworks...")
    vitruve_users_by_id = {u["id"]: u for u in vitruve.get_users()}
    teamworks_users = teamworks.get_all_users()
    name_index = build_name_index(teamworks_users)
    log(f"  Vitruve users: {len(vitruve_users_by_id)}, Teamworks users: {len(teamworks_users)}")

    log(f"Pulling Vitruve workouts (date={VITRUVE_DATE_RANGE})...")
    workouts = vitruve.get_workouts(VITRUVE_DATE_RANGE)

    # Pass 1: resolve athlete matches, compute this run's candidate units.
    candidates = []
    for workout, exercise in iter_exercise_units(workouts):
        counts["exercises_seen"] += 1

        vitruve_user = vitruve_users_by_id.get(workout.get("userId"))
        if vitruve_user is None:
            counts["vitruve_user_not_found"] += 1
            continue

        teamworks_user_id, match_status = match_athlete(vitruve_user, name_index)
        if match_status == UNMATCHED:
            counts["athlete_unmatched"] += 1
            continue
        if match_status == AMBIGUOUS:
            counts["athlete_ambiguous_name"] += 1
            continue

        candidates.append(
            {
                "workout": workout,
                "exercise": exercise,
                "teamworks_user_id": teamworks_user_id,
                "unit_id": compute_unit_id(workout, exercise),
            }
        )

    # One standard call per run: ask Teamworks which of these units already
    # have a "Vitruve VBT" event, rather than trusting a local state file.
    start_date = _search_window()
    matched_user_ids = {c["teamworks_user_id"] for c in candidates}
    candidate_unit_ids = {c["unit_id"] for c in candidates}
    existing_unit_ids = teamworks.find_existing_unit_ids(start_date, matched_user_ids, candidate_unit_ids)
    log(f"  {len(existing_unit_ids)}/{len(candidate_unit_ids)} candidate units already in Teamworks")

    # Pass 2: build and write only the units that don't already exist.
    unrecognized_metrics = set()
    for candidate in candidates:
        unit_id = candidate["unit_id"]
        if unit_id in existing_unit_ids:
            counts["skipped_duplicate"] += 1
            continue

        try:
            payload, row_count, unknown_metrics, spans_multiple_days = build_event_payload(
                candidate["workout"], candidate["exercise"], candidate["teamworks_user_id"]
            )
        except UnschedulableExercise:
            counts["exercise_missing_dates"] += 1
            continue

        unrecognized_metrics |= unknown_metrics
        if spans_multiple_days:
            counts["multiday_anomaly"] += 1
            log(f"  ANOMALY: exercise for unit {unit_id} - series span multiple calendar days")

        try:
            success, event_id, response = teamworks.import_event(payload)
        except TeamworksError as e:
            counts["teamworks_write_failed"] += 1
            log(f"  WRITE FAILED (transport) for unit {unit_id}: {e}")
            continue

        if not success:
            counts["teamworks_write_failed"] += 1
            log(f"  WRITE FAILED (state={response.get('state')}) for unit {unit_id}: {response}")
            continue

        counts["written"] += 1
        log(f"  wrote unit {unit_id} -> event {event_id} ({row_count} rows)")

    if unrecognized_metrics:
        log(f"ALERT: unrecognized metric names encountered: {sorted(unrecognized_metrics)}")

    log("Run summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return counts


def main():
    try:
        run()
    except (VitruveError, TeamworksError) as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
