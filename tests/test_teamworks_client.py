from unittest import mock

from vitruve_sync.teamworks_client import TeamworksClient, _find_candidate_unit_id, _walk_strings


def test_walk_strings_finds_leaves_at_any_depth():
    node = {"a": "top", "b": {"c": ["nested", 42, None]}, "d": [{"e": "deep"}]}
    assert set(_walk_strings(node)) == {"top", "nested", "deep"}


def test_find_candidate_unit_id_direct_key():
    # Confirmed real shape: event-level fields live in rows[0]["pairs"].
    event = {
        "id": 5678,
        "rows": [{"row": 0, "pairs": [{"key": "Vitruve ID", "value": "wk-1:ex-1"}]}],
    }
    assert _find_candidate_unit_id(event, {"wk-1:ex-1", "wk-2:ex-2"}) == "wk-1:ex-1"


def test_find_candidate_unit_id_deep_fallback():
    # Simulates a future/unknown nesting shape that isn't row 0 - the deep
    # scan should still find the value as a safety net.
    event = {"id": 5678, "someOtherStructure": {"nested": [{"value": "wk-3:ex-9"}]}}
    assert _find_candidate_unit_id(event, {"wk-3:ex-9"}) == "wk-3:ex-9"


def test_find_candidate_unit_id_returns_none_when_absent():
    event = {
        "id": 5678,
        "rows": [{"row": 0, "pairs": [{"key": "Vitruve ID", "value": "wk-1:ex-1"}]}],
    }
    assert _find_candidate_unit_id(event, {"wk-9:ex-9"}) is None


def _event(unit_id):
    return {"rows": [{"row": 0, "pairs": [{"key": "Vitruve ID", "value": unit_id}]}]}


def test_find_existing_unit_ids_paginates_and_matches():
    client = TeamworksClient("user", "pass")

    pages = [
        {
            "export": {
                "events": [_event("wk-1:ex-1"), _event("wk-2:ex-2")],
                "cursor": "page-2",
            }
        },
        {"export": {"events": [_event("wk-3:ex-3")]}},
    ]

    with mock.patch.object(client, "_post", side_effect=pages) as mock_post:
        found = client.find_existing_unit_ids(
            "01/01/2026", {4001, 4002}, {"wk-1:ex-1", "wk-3:ex-3", "wk-9:ex-9"}
        )

    assert found == {"wk-1:ex-1", "wk-3:ex-3"}
    assert mock_post.call_count == 2

    first_call_body = mock_post.call_args_list[0].args[1]
    assert first_call_body["formName"] == "Vitruve VBT"
    assert first_call_body["startDate"] == "01/01/2026"
    assert first_call_body["userIds"] == [4001, 4002]
    assert "pagination" not in first_call_body

    second_call_body = mock_post.call_args_list[1].args[1]
    assert second_call_body["pagination"] == {"paginate": True, "cursor": "page-2"}


def test_find_existing_unit_ids_accepts_top_level_cursor_as_well_as_export_cursor():
    # Real pagination location isn't confirmed beyond a single-page example
    # - accept either the top-level or export-nested cursor.
    client = TeamworksClient("user", "pass")

    pages = [
        {"export": {"events": [_event("wk-1:ex-1")]}, "cursor": "page-2"},
        {"export": {"events": [_event("wk-2:ex-2")]}},
    ]

    with mock.patch.object(client, "_post", side_effect=pages) as mock_post:
        found = client.find_existing_unit_ids("01/01/2026", {4001}, {"wk-1:ex-1", "wk-2:ex-2"})

    assert found == {"wk-1:ex-1", "wk-2:ex-2"}
    assert mock_post.call_count == 2


def test_find_existing_unit_ids_short_circuits_when_nothing_to_check():
    client = TeamworksClient("user", "pass")
    with mock.patch.object(client, "_post") as mock_post:
        assert client.find_existing_unit_ids("01/01/2026", set(), set()) == set()
        assert client.find_existing_unit_ids("01/01/2026", {4001}, set()) == set()
    mock_post.assert_not_called()
