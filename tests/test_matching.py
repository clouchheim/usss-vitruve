from vitruve_sync.matching import AMBIGUOUS, UNMATCHED, build_name_index, match_athlete

from tests.fixtures import TEAMWORKS_USERS_VARIED_SHAPE, VITRUVE_USER


def test_build_name_index_resolves_varied_field_names():
    index = build_name_index(TEAMWORKS_USERS_VARIED_SHAPE)
    assert index[("jamie", "rivers")] == [4001]
    assert index[("alex", "nguyen")] == [4002]


def test_match_athlete_exact_match_case_insensitive():
    index = build_name_index(TEAMWORKS_USERS_VARIED_SHAPE)
    vitruve_user = {"name": "JAMIE", "surname": "rivers"}
    user_id, status = match_athlete(vitruve_user, index)
    assert status == "matched"
    assert user_id == 4001


def test_match_athlete_unmatched_when_no_candidate():
    index = build_name_index(TEAMWORKS_USERS_VARIED_SHAPE)
    vitruve_user = {"name": "Nobody", "surname": "Here"}
    user_id, status = match_athlete(vitruve_user, index)
    assert status == UNMATCHED
    assert user_id is None


def test_match_athlete_ambiguous_on_duplicate_full_name():
    teamworks_users = TEAMWORKS_USERS_VARIED_SHAPE + [
        {"userId": 4004, "firstName": "Jamie", "lastName": "Rivers"}
    ]
    index = build_name_index(teamworks_users)
    vitruve_user = {"name": "Jamie", "surname": "Rivers"}
    user_id, status = match_athlete(vitruve_user, index)
    assert status == AMBIGUOUS
    assert user_id is None


def test_fixture_vitruve_user_matches_expected_teamworks_user():
    index = build_name_index(TEAMWORKS_USERS_VARIED_SHAPE)
    user_id, status = match_athlete(VITRUVE_USER, index)
    assert status == "matched"
    assert user_id == 4001
