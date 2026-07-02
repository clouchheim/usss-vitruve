import os

from vitruve_sync.dedup import load_state, needs_write, record, save_state


def test_load_state_missing_file_returns_empty_dict(tmp_path):
    path = tmp_path / "nope" / "state.json"
    assert load_state(str(path)) == {}


def test_needs_write_true_for_new_unit():
    state = {}
    should_write, existing_event_id = needs_write(state, "unit-1", row_count=3)
    assert should_write is True
    assert existing_event_id is None


def test_needs_write_false_when_row_count_unchanged():
    state = {}
    record(state, "unit-1", existing_event_id=555, row_count=3, teamworks_user_id=4001)
    should_write, existing_event_id = needs_write(state, "unit-1", row_count=3)
    assert should_write is False
    assert existing_event_id == 555


def test_needs_write_true_when_row_count_grew():
    state = {}
    record(state, "unit-1", existing_event_id=555, row_count=3, teamworks_user_id=4001)
    should_write, existing_event_id = needs_write(state, "unit-1", row_count=5)
    assert should_write is True
    assert existing_event_id == 555  # reuse for update, not a fresh create


def test_save_and_load_round_trip(tmp_path):
    path = str(tmp_path / "state" / "dedup_state.json")
    state = {}
    record(state, "unit-1", 555, 3, 4001)
    save_state(path, state)

    assert os.path.exists(path)
    reloaded = load_state(path)
    assert reloaded == state
