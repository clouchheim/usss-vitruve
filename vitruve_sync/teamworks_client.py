"""Client for the Teamworks AMS v1 API (Basic Auth, informat/format=json).

usersynchronise's response shape isn't fully documented (the OpenAPI excerpt
covers the sync envelope but not the per-user object), so user-list
extraction and field resolution here are deliberately defensive, per
AMS_EVENTIMPORT_NOTES.md: detect the user list by shape rather than a
hardcoded key, and try several candidate field names per user.
"""

import base64
import json
import urllib.error
import urllib.request

from vitruve_sync.config import TEAMWORKS_APP_ID, TEAMWORKS_BASE_URL

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
    """Find the first list-of-dicts value in a sync response envelope."""
    for value in response.values():
        if isinstance(value, list) and (not value or isinstance(value[0], dict)):
            return value
    return []


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
            users.extend(extract_list_of_dicts(response))
            cursor = response.get("cursor")
            if not cursor:
                break
        return users

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
