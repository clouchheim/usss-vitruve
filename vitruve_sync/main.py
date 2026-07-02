"""Entrypoint: pull Vitruve, match athletes, transform, dedup, load into
Teamworks AMS. Run every ~30 min via .github/workflows/vitruve_sync.yml.

Never logs athlete names/emails - only Vitruve/Teamworks IDs and counts,
per CLAUDE.md's observability section.
"""

import os
import sys
from collections import Counter

from vitruve_sync.config import STATE_PATH, VITRUVE_DATE_RANGE
from vitruve_sync.dedup import load_state, needs_write, record, save_state
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

    state = load_state(STATE_PATH)
    unrecognized_metrics = set()

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

        unit_id = compute_unit_id(workout, exercise)

        try:
            payload, row_count, unknown_metrics, spans_multiple_days = build_event_payload(
                workout, exercise, teamworks_user_id
            )
        except UnschedulableExercise:
            counts["exercise_missing_dates"] += 1
            continue

        unrecognized_metrics |= unknown_metrics
        if spans_multiple_days:
            counts["multiday_anomaly"] += 1
            log(f"  ANOMALY: exercise {exercise.get('id')} series span multiple calendar days")

        should_write, existing_event_id = needs_write(state, unit_id, row_count)
        if not should_write:
            counts["skipped_duplicate"] += 1
            continue

        if existing_event_id:
            payload["existingEventId"] = existing_event_id

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

        record(state, unit_id, event_id or existing_event_id, row_count, teamworks_user_id)
        counts["written"] += 1

    save_state(STATE_PATH, state)

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
