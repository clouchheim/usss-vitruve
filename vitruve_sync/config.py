"""Constants for the Vitruve -> Teamworks AMS sync.

Credentials are read from the environment (GitHub Actions secrets), never
hardcoded here: API_KEY (Vitruve), A360_USER / A360_PASSWORD (Teamworks).
"""

VITRUVE_BASE_URL = "https://api-exports.vitruve.fit"
VITRUVE_DATE_RANGE = "last-7days"  # overlapping window; dedup handles re-pulls

TEAMWORKS_BASE_URL = "https://usopc.smartabase.com/athlete360-usss"
TEAMWORKS_APP_ID = "usss.vitruve-integration.v1"
TEAMWORKS_FORM_NAME = "Vitruve VBT"

# eventsearch date window for the dedup existence check - wider than
# VITRUVE_DATE_RANGE on both ends as a buffer against timezone slop, per
# the user's "could do 8 to be safe with time changes" call.
TEAMWORKS_SEARCH_LOOKBACK_DAYS = 8
TEAMWORKS_SEARCH_LOOKAHEAD_DAYS = 1

# Session date/time isn't in the Vitruve payload as a timezone-aware local
# value, only UTC. Assumed local time zone for USSS's Teamworks instance.
# Confirm against a real eventimport smoke test before relying on this.
LOCAL_TIMEZONE = "America/Denver"

# AMS form field names (Vitruve VBT form). "Vitruve ID" (not "id") to avoid
# colliding with Teamworks' own numeric event id returned by read endpoints
# like eventsearch/synchronise.
FIELD_EXERCISE_NAME = "Exercise Name"
FIELD_VITRUVE_ID = "Vitruve ID"
FIELD_TYPE = "Type"
FIELD_SET = "Set"
