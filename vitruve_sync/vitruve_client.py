"""Read-only client for the Vitruve Exports API."""

import json
import urllib.error
import urllib.parse
import urllib.request

from vitruve_sync.config import VITRUVE_BASE_URL

PAGE_LIMIT = 100


class VitruveError(Exception):
    pass


class VitruveClient:
    def __init__(self, api_key):
        self._api_key = api_key

    def _get(self, path, params=None):
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        url = f"{VITRUVE_BASE_URL}{path}{query}"
        request = urllib.request.Request(url, headers={"x-api-key": self._api_key})
        try:
            with urllib.request.urlopen(request) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise VitruveError(f"Vitruve API error {e.code} at {path}: {body}") from e

    def get_users(self):
        """[{id, name, surname}] - no email field observed in this org's data."""
        return self._get("/users").get("data", [])

    def get_workouts(self, date_range):
        workouts = []
        offset = 0
        while True:
            page = self._get(
                "/vbt-workouts",
                {"date": date_range, "limit": PAGE_LIMIT, "offset": offset},
            )
            batch = page.get("data", [])
            workouts.extend(batch)
            if len(batch) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT
        return workouts
