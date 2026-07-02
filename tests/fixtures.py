"""Synthetic Vitruve/Teamworks fixtures for tests - no real athlete data."""


def make_metric(metric, value, unit):
    return {"metric": metric, "value": value, "unit": unit}


def make_rep(rep_id, rep_type, metrics):
    return {"id": rep_id, "type": rep_type, "metricValues": metrics}


def make_series(series_id, completed_at, repetitions):
    return {"id": series_id, "completedAt": completed_at, "repetitions": repetitions}


def clean_exercise():
    """One exercise, two series, same-day, one rep missing a metric other
    reps have, one unrecognized metric key mixed in.
    """
    ecc = make_rep(
        "rep-1",
        "eccentric",
        [
            make_metric("Mean Velocity", 0.70, "m/s"),
            make_metric("Peak Velocity", 1.09, "m/s"),
            make_metric("Mean Acceleration", 0.19, "m/s2"),
        ],
    )
    con = make_rep(
        "rep-2",
        "concentric",
        [
            make_metric("Mean Velocity", 0.95, "m/s"),
            make_metric("Peak Velocity", 1.34, "m/s"),
            # Mean Acceleration deliberately absent here (per-rep variability)
            make_metric("Not A Real Metric", 42, "xyz"),  # unrecognized key
        ],
    )
    series_1 = make_series("series-1", "2026-01-05T17:01:30.000Z", [ecc, con])

    con2 = make_rep(
        "rep-3",
        "concentric",
        [make_metric("Mean Velocity", 1.05, "m/s")],
    )
    series_2 = make_series("series-2", "2026-01-05T17:05:00.000Z", [con2])

    return {
        "id": "exercise-1",
        "name": "Test Jump",
        "series": [series_2, series_1],  # deliberately out of chronological order
    }


def multiday_exercise():
    series_1 = make_series(
        "series-a", "2026-01-01T12:00:00.000Z", [make_rep("rep-a", "concentric", [])]
    )
    series_2 = make_series(
        "series-b", "2026-01-04T12:00:00.000Z", [make_rep("rep-b", "concentric", [])]
    )
    return {"id": "exercise-2", "name": "Stale Exercise", "series": [series_1, series_2]}


def workout_with(*exercises, workout_id="workout-1", user_id="vitruve-user-1"):
    return {"id": workout_id, "userId": user_id, "exercises": list(exercises)}


VITRUVE_USER = {"id": "vitruve-user-1", "name": "Jamie", "surname": "Rivers"}

TEAMWORKS_USERS_VARIED_SHAPE = [
    {"userId": 4001, "firstName": "Jamie", "lastName": "Rivers"},
    {"user_id": 4002, "first_name": "Alex", "last_name": "Nguyen"},
    {"id": 4003, "forename": "Sam", "familyName": "Rivers"},
]
