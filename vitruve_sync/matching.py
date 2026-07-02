"""Athlete matching: (first name, last name) is the ground-truth join key
between Vitruve /users and Teamworks usersynchronise - see CLAUDE.md.
"""

from vitruve_sync.teamworks_client import FIRST_NAME_KEYS, ID_KEYS, LAST_NAME_KEYS, first_present

UNMATCHED = "unmatched"
AMBIGUOUS = "ambiguous"


def normalize(name):
    return (name or "").strip().casefold()


def build_name_index(teamworks_users):
    """(first, last) -> [teamworks_user_id, ...]. More than one entry means
    a genuine duplicate name in the org - surfaced as AMBIGUOUS, not guessed.
    """
    index = {}
    for user in teamworks_users:
        user_id = first_present(user, ID_KEYS)
        first = first_present(user, FIRST_NAME_KEYS)
        last = first_present(user, LAST_NAME_KEYS)
        if user_id is None or first is None or last is None:
            continue
        key = (normalize(first), normalize(last))
        index.setdefault(key, []).append(user_id)
    return index


def match_athlete(vitruve_user, name_index):
    """Returns (teamworks_user_id_or_None, status)."""
    key = (normalize(vitruve_user.get("name")), normalize(vitruve_user.get("surname")))
    candidates = name_index.get(key, [])
    if len(candidates) == 1:
        return candidates[0], "matched"
    if len(candidates) == 0:
        return None, UNMATCHED
    return None, AMBIGUOUS
