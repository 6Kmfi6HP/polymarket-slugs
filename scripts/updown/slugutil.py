"""Slug parsing for Polymarket UPDOWN markets."""

import re
from datetime import datetime, timezone

UPDOWN_SLUG_RE = re.compile(
    r"^(?P<asset>[a-z0-9]+)-updown-(?P<interval>5m|15m)-(?P<ts>\d+)$"
)


def parse_slug_metadata(slug):
    """Return (asset, interval, UTC start time ISO) encoded in a UPDOWN slug."""
    match = UPDOWN_SLUG_RE.match(slug)
    if not match:
        return None

    start_time = (
        datetime.fromtimestamp(int(match.group("ts")), timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return match.group("asset").upper(), match.group("interval"), start_time
