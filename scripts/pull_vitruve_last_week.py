#!/usr/bin/env python3
"""Read-only diagnostic: pull the last 7 days of Vitruve VBT workouts.

Standalone and side-effect-free against Teamworks - only talks to Vitruve.
Writes the raw paginated response to ./samples/ for inspection; that
directory is gitignored since it's real athlete performance data.

Usage:
    API_KEY=... python3 scripts/pull_vitruve_last_week.py
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://api-exports.vitruve.fit"
PAGE_LIMIT = 100


def fetch_page(api_key, offset):
    url = f"{BASE_URL}/vbt-workouts?date=last-7days&limit={PAGE_LIMIT}&offset={offset}"
    request = urllib.request.Request(url, headers={"x-api-key": api_key})
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"Vitruve API error {e.code} at offset {offset}: {body}", file=sys.stderr)
        raise


def fetch_all_workouts(api_key):
    workouts = []
    offset = 0
    while True:
        page = fetch_page(api_key, offset)
        batch = page.get("data", [])
        workouts.extend(batch)
        print(f"  fetched offset={offset}: {len(batch)} workouts")
        if len(batch) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    return workouts


def summarize(workouts):
    exercise_count = 0
    series_count = 0
    rep_count = 0
    metric_names = set()
    for workout in workouts:
        for exercise in workout.get("exercises", []):
            exercise_count += 1
            for series in exercise.get("series", []):
                series_count += 1
                for rep in series.get("repetitions", []):
                    rep_count += 1
                    for metric_value in rep.get("metricValues", []):
                        metric_names.add(metric_value.get("metric"))
    return {
        "workouts": len(workouts),
        "exercises": exercise_count,
        "series": series_count,
        "repetitions": rep_count,
        "distinct_metric_names": sorted(metric_names),
    }


def main():
    api_key = os.environ.get("API_KEY")
    if not api_key:
        print("Set the API_KEY environment variable (Vitruve x-api-key).", file=sys.stderr)
        sys.exit(1)

    print("Pulling last-7days workouts from Vitruve...")
    workouts = fetch_all_workouts(api_key)

    summary = summarize(workouts)
    print("\nSummary (no PII - workouts are keyed by userId UUID only):")
    print(json.dumps(summary, indent=2))

    os.makedirs("samples", exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join("samples", f"vbt-workouts-last-7days-{timestamp}.json")
    with open(out_path, "w") as f:
        json.dump(workouts, f, indent=2)
    print(f"\nWrote {len(workouts)} workouts to {out_path}")


if __name__ == "__main__":
    main()
