"""Flatten a Vitruve (workout, exercise) unit into one eventimport payload.

One eventimport call per (workout, exercise); one table row per repetition,
concentric and eccentric alike, tagged by Type - see CLAUDE.md "Transform"
section for the full reasoning.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from vitruve_sync.config import (
    FIELD_EXERCISE_NAME,
    FIELD_SET,
    FIELD_TYPE,
    FIELD_VITRUVE_ID,
    LOCAL_TIMEZONE,
    TEAMWORKS_FORM_NAME,
)

# Exact AMS form field keys, f"{metric} ({unit})" using Vitruve's own
# metric/unit strings. Confirmed 22-metric vocabulary shared by concentric
# and eccentric reps. The 13 concentric-only/jump-only metrics (1RM,
# Fatigue *, Jump *) are intentionally excluded until those columns exist
# on the form - see CLAUDE.md for the full excluded list. Never emit a pair
# for a metric that isn't in this set; log it as unrecognized instead.
KNOWN_METRIC_FIELDS = frozenset(
    {
        "Peak Velocity (m/s)",
        "Mean Velocity (m/s)",
        "Peak Acceleration (m/s2)",
        "Mean Acceleration (m/s2)",
        "Time to Peak Velocity (ms)",
        "Weight (kg)",
        "Acceleration Index (m/s2)",
        "Mean Force (N)",
        "Peak Force (N)",
        "Mean Force / Body Weight (N/kg)",
        "Mean Power [MPV] (W)",
        "Mean Power [MV] (W)",
        "Peak Force / Body Weight (N/kg)",
        "Peak Power (W)",
        "Peak Velocity / Body Weight (m/s/kg)",
        "Weight / Body Weight (%)",
        "Mean Power / Body Weight [MPV] (w/kg)",
        "Mean Power / Body Weight [MV] (w/kg)",
        "ROM (Range of Motion) (m)",
        "Mean Propulsive Velocity (m/s)",
        "Repetition Duration (ms)",
        "Peak Power / Body Weight (w/kg)",
        # Uncomment once these 13 columns exist on the AMS form:
        # "1RM (kg)",
        # "1RM / Body Weight (%)",
        # "Fatigue (PV) (%)",
        # "Fatigue [MPV] (%)",
        # "Fatigue [MV] (%)",
        # "Jump Contact Time (ms)",
        # "Jump Contraction Time (ms)",
        # "Jump Flight Time (ms)",
        # "Jump Height (m)",
        # "Jump Modified RSI (m/s)",
        # "Jump Net Impulse (N)",
        # "Jump Positive Impulse (N)",
        # "Jump RSI (m/s)",
    }
)


class UnschedulableExercise(Exception):
    """Raised when an exercise has no series with a completedAt timestamp."""


def compute_unit_id(workout, exercise):
    return f"{workout['id']}:{exercise['id']}"


def iter_exercise_units(workouts):
    for workout in workouts:
        for exercise in workout.get("exercises", []):
            yield workout, exercise


def derive_session_datetime(exercise):
    """Local start time for the event, plus whether this exercise's series
    span more than one calendar day (a data-quality anomaly to alert on,
    not silently resolve - see CLAUDE.md finding 6).
    """
    completed_ats = sorted(
        datetime.fromisoformat(series["completedAt"].replace("Z", "+00:00"))
        for series in exercise.get("series", [])
        if series.get("completedAt")
    )
    if not completed_ats:
        raise UnschedulableExercise(f"exercise {exercise.get('id')} has no dated series")

    tz = ZoneInfo(LOCAL_TIMEZONE)
    earliest_local = completed_ats[0].astimezone(tz)
    latest_local = completed_ats[-1].astimezone(tz)
    spans_multiple_days = (latest_local.date() - earliest_local.date()).days >= 1
    return earliest_local, spans_multiple_days


def assign_set_numbers(exercise):
    """series id -> ascending Set number (1, 2, 3...), ordered by completedAt
    rather than trusting raw API array order.
    """
    ordered = sorted(exercise.get("series", []), key=lambda s: s.get("completedAt") or "")
    return {series["id"]: index + 1 for index, series in enumerate(ordered)}


def format_metric_value(value):
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def format_time(local_dt):
    # Teamworks wants "9:00 AM", not "09:00 AM".
    return local_dt.strftime("%I:%M %p").lstrip("0") or "12:00 AM"


def build_event_payload(workout, exercise, teamworks_user_id):
    """Returns (payload, row_count, unknown_metric_keys, spans_multiple_days).

    Always a create (never sends existingEventId) - dedup is a Teamworks
    eventsearch existence check done by the caller before this is called at
    all, see teamworks_client.find_existing_unit_ids and CLAUDE.md.
    row_count excludes row 0 (event-level fields); returned for logging only.
    """
    session_dt, spans_multiple_days = derive_session_datetime(exercise)
    date_str = session_dt.strftime("%d/%m/%Y")
    time_str = format_time(session_dt)

    set_numbers = assign_set_numbers(exercise)

    rows = [
        {
            "row": 0,
            "pairs": [
                {"key": FIELD_EXERCISE_NAME, "value": exercise.get("name", "")},
                {"key": FIELD_VITRUVE_ID, "value": compute_unit_id(workout, exercise)},
            ],
        }
    ]

    unknown_metrics = set()
    row_index = 1
    for series in sorted(exercise.get("series", []), key=lambda s: s.get("completedAt") or ""):
        set_number = set_numbers[series["id"]]
        for rep in series.get("repetitions", []):
            pairs = [
                {"key": FIELD_TYPE, "value": rep.get("type", "")},
                {"key": FIELD_SET, "value": str(set_number)},
            ]
            for metric_value in rep.get("metricValues", []):
                key = f"{metric_value['metric']} ({metric_value['unit']})"
                if key in KNOWN_METRIC_FIELDS:
                    pairs.append({"key": key, "value": format_metric_value(metric_value["value"])})
                else:
                    unknown_metrics.add(key)
            rows.append({"row": row_index, "pairs": pairs})
            row_index += 1

    payload = {
        "formName": TEAMWORKS_FORM_NAME,
        "startDate": date_str,
        "finishDate": date_str,
        "startTime": time_str,
        "userId": {"userId": teamworks_user_id},
        "rows": rows,
    }
    row_count = len(rows) - 1
    return payload, row_count, unknown_metrics, spans_multiple_days
