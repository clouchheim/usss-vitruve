"""Client for the Teamworks AMS v1 API (Basic Auth, informat/format=json).

usersynchronise's real response shape has now been confirmed against a live
call: users live under a top-level "users" key, with confirmed field names
userId/firstName/lastName/emailAddress/... (emailAddress exists on the
Teamworks side, but Vitruve's own /users has no email field, so it's still
not usable as the join key - see CLAUDE.md). The "users" key is used
directly, with the old shape-detection heuristic kept only as a fallback in
case a future API change moves fields around.

eventsearch's per-event shape is NOT confirmed - the OpenAPI excerpt only
documents the numeric "id" field, not how a custom form field like our
"Vitruve ID" appears in a returned event. Extraction here is deliberately
defensive (try a direct key, then a deep search for the exact value) and
needs verifying on the first live run - see find_existing_unit_ids.
"""

import base64
import json
import urllib.error
import urllib.request

from vitruve_sync.config import TEAMWORKS_APP_ID, TEAMWORKS_BASE_URL, TEAMWORKS_FORM_NAME, FIELD_VITRUVE_ID

ID_KEYS = ("userId", "user_id", "id")
FIRST_NAME_KEYS = ("firstName", "first_name", "forename", "givenName")
LAST_NAME_KEYS = ("lastName", "last_name", "surname", "familyName")


class TeamworksError(Exception):
    pass


def first_present(d, keys):
    for key in keys:
        value = d.get(key)
        if value not in (None, ""):
            return value
    return None


def extract_list_of_dicts(response):
    """Fallback: find the first list-of-dicts value in a response envelope."""
    for value in response.values():
        if isinstance(value, list) and (not value or isinstance(value[0], dict)):
            return value
    return []


def _walk_strings(node):
    """Yield every string leaf value in an arbitrarily nested dict/list."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _walk_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item)


def _find_candidate_unit_id(event, candidate_unit_ids):
    """Which (if any) of our candidate unit ids appears in this raw event.

    Tries the direct field-name key first (most likely shape for a
    single-value/event-level field), falling back to a deep scan for the
    literal string - robust to an unknown nesting shape since we already
    know the finite set of values we're looking for.
    """
    direct = event.get(FIELD_VITRUVE_ID)
    if direct in candidate_unit_ids:
        return direct
    for value in _walk_strings(event):
        if value in candidate_unit_ids:
            return value
    return None


class TeamworksClient:
    def __init__(self, username, password):
        credentials = f"{username}:{password}".encode()
        self._auth_header = "Basic " + base64.b64encode(credentials).decode()

    def _post(self, path, body):
        url = f"{TEAMWORKS_BASE_URL}{path}?informat=json&format=json"
        data = json.dumps(body).encode()
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "X-APP-ID": TEAMWORKS_APP_ID,
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            raise TeamworksError(
                f"Teamworks API error {e.code} at {path}: {raw.decode(errors='replace')}"
            ) from e
        return json.loads(raw)

    def get_all_users(self):
        """Full pull every run (lastSynchronisationTimeOnServer=0).

        Teamworks recommends delta sync via lastSynchronisationTimeOnServer,
        but that requires persisting + merging roster state across runs
        (handling mergedUsers/idsOfDeletedUsers) which adds real complexity
        we can't verify without live data. A full paginated pull every 30
        minutes is simple, correct, and cheap at this org's roster size -
        worth revisiting only if the roster grows large enough to matter.
        """
        users = []
        cursor = ""
        while True:
            response = self._post(
                "/api/v1/usersynchronise",
                {
                    "lastSynchronisationTimeOnServer": 0,
                    "paginate": "True",
                    "cursor": cursor,
                },
            )
            page_users = response["users"] if "users" in response else extract_list_of_dicts(response)
            users.extend(page_users)
            cursor = response.get("cursor")
            if not cursor:
                break
        return users

    def find_existing_unit_ids(self, start_date, finish_date, user_ids, candidate_unit_ids):
        """Which of candidate_unit_ids already have a "Vitruve VBT" event in
        Teamworks within [start_date, finish_date] for the given user_ids.

        This is the dedup source of truth: Teamworks itself, queried fresh
        every run via /api/v1/eventsearch, rather than a separately
        maintained state file. No result is returned per-event (no
        existingEventId) since this design never updates an existing event,
        only creates new ones for units not yet present - see CLAUDE.md.
        """
        candidate_set = set(candidate_unit_ids)
        if not candidate_set or not user_ids:
            return set()

        found = set()
        cursor = None
        base_body = {
            "formNames": [TEAMWORKS_FORM_NAME],
            "startDate": start_date,
            "finishDate": finish_date,
            "userIds": sorted(user_ids),
            "paginate": True,
        }
        while True:
            body = dict(base_body)
            if cursor:
                body["cursor"] = cursor
            response = self._post("/api/v1/eventsearch", body)
            for event in response.get("events", []):
                unit_id = _find_candidate_unit_id(event, candidate_set)
                if unit_id:
                    found.add(unit_id)
            # eventsearch's own docs disagree with themselves: the prose says
            # the response field is "next_cursor", the OpenAPI schema names
            # it "cursor" - accept either rather than betting on one.
            cursor = response.get("next_cursor") or response.get("cursor")
            if not cursor:
                break
        return found

    def import_event(self, payload):
        """Returns (success, event_id, raw_response).

        eventimport returns HTTP 200 even on failure - state ==
        "SUCCESSFULLY_IMPORTED" is the only real success signal.
        """
        response = self._post("/api/v1/eventimport", payload)
        success = response.get("state") == "SUCCESSFULLY_IMPORTED"
        ids = response.get("ids") or []
        event_id = ids[0] if ids else None
        return success, event_id, response
