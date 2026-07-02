from vitruve_sync.transform import (
    KNOWN_METRIC_FIELDS,
    UnschedulableExercise,
    assign_set_numbers,
    build_event_payload,
    compute_unit_id,
    derive_session_datetime,
)

from tests.fixtures import clean_exercise, make_metric, make_rep, make_series, multiday_exercise, workout_with


def test_compute_unit_id_combines_workout_and_exercise():
    workout = workout_with(clean_exercise(), workout_id="wk-9")
    exercise = workout["exercises"][0]
    assert compute_unit_id(workout, exercise) == "wk-9:exercise-1"


def test_assign_set_numbers_orders_by_completed_at_not_array_order():
    exercise = clean_exercise()  # series-2 listed first, but completes later
    numbers = assign_set_numbers(exercise)
    assert numbers["series-1"] == 1
    assert numbers["series-2"] == 2


def test_derive_session_datetime_same_day_no_anomaly():
    exercise = clean_exercise()
    session_dt, spans_multiple_days = derive_session_datetime(exercise)
    assert spans_multiple_days is False
    assert session_dt.tzinfo is not None


def test_derive_session_datetime_flags_multiday_span():
    exercise = multiday_exercise()
    _, spans_multiple_days = derive_session_datetime(exercise)
    assert spans_multiple_days is True


def test_derive_session_datetime_raises_when_no_dated_series():
    exercise = {"id": "ex-empty", "name": "Empty", "series": []}
    try:
        derive_session_datetime(exercise)
        assert False, "expected UnschedulableExercise"
    except UnschedulableExercise:
        pass


def test_build_event_payload_shape():
    workout = workout_with(clean_exercise())
    exercise = workout["exercises"][0]

    payload, row_count, unknown_metrics, spans_multiple_days = build_event_payload(
        workout, exercise, teamworks_user_id=4001
    )

    assert payload["formName"] == "Vitruve VBT"
    assert payload["userId"] == {"userId": 4001}
    assert spans_multiple_days is False
    assert "existingEventId" not in payload

    row0 = payload["rows"][0]
    assert row0["row"] == 0
    assert {"key": "Exercise Name", "value": "Test Jump"} in row0["pairs"]
    assert {"key": "Vitruve ID", "value": "workout-1:exercise-1"} in row0["pairs"]

    # 3 repetitions total across both series -> 3 table rows
    assert row_count == 3
    assert len(payload["rows"]) == 4  # + row 0

    row1 = payload["rows"][1]["pairs"]
    assert {"key": "Type", "value": "eccentric"} in row1
    assert {"key": "Set", "value": "1"} in row1
    assert {"key": "Mean Velocity (m/s)", "value": "0.70"} in row1

    row2 = payload["rows"][2]["pairs"]
    assert {"key": "Set", "value": "1"} in row2
    # unrecognized metric never becomes a pair
    assert all(p["key"] != "Not A Real Metric (xyz)" for p in row2)

    row3 = payload["rows"][3]["pairs"]
    assert {"key": "Set", "value": "2"} in row3

    assert unknown_metrics == {"Not A Real Metric (xyz)"}


def test_known_metric_fields_includes_1rm_and_fatigue_now_that_columns_exist():
    # Confirmed added to the real AMS form - see conversation history.
    assert "1RM (kg)" in KNOWN_METRIC_FIELDS
    assert "1RM / Body Weight (%)" in KNOWN_METRIC_FIELDS
    assert "Fatigue (PV) (%)" in KNOWN_METRIC_FIELDS
    assert "Fatigue [MPV] (%)" in KNOWN_METRIC_FIELDS
    assert "Fatigue [MV] (%)" in KNOWN_METRIC_FIELDS
    # Jump metrics remain excluded until those columns exist too.
    assert "Jump Height (m)" not in KNOWN_METRIC_FIELDS


def test_build_event_payload_includes_1rm_and_fatigue_metrics():
    rep = make_rep(
        "rep-1",
        "concentric",
        [
            make_metric("1RM", 0.16, "kg"),
            make_metric("1RM / Body Weight", 0.2105263157894737, "%"),
            make_metric("Fatigue [MPV]", 40, "%"),
            make_metric("Fatigue [MV]", 33.09, "%"),
            make_metric("Fatigue (PV)", 41.07, "%"),
        ],
    )
    series = make_series("series-1", "2026-01-05T17:01:30.000Z", [rep])
    exercise = {"id": "exercise-1", "name": "Back Squat", "series": [series]}
    workout = workout_with(exercise)

    payload, _, unknown_metrics, _ = build_event_payload(workout, exercise, teamworks_user_id=4001)

    assert unknown_metrics == set()
    row1 = payload["rows"][1]["pairs"]
    assert {"key": "1RM (kg)", "value": "0.16"} in row1
    assert {"key": "1RM / Body Weight (%)", "value": "0.21"} in row1
    assert {"key": "Fatigue [MPV] (%)", "value": "40"} in row1
    assert {"key": "Fatigue [MV] (%)", "value": "33.09"} in row1
    assert {"key": "Fatigue (PV) (%)", "value": "41.07"} in row1
