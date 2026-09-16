"""Gamma API access for closed UPDOWN markets."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

GAMMA_API = "https://gamma-api.polymarket.com"
TAG_ID = 102127  # "Up or Down" tag
PAGE_LIMIT = 100


def fetch_lookback_days() -> int:
    return int(os.getenv("FETCH_LOOKBACK_DAYS", "2"))


def fetch_end_date_min() -> str:
    """Return the UTC lower bound for recent closed-market polling."""
    configured = os.getenv("FETCH_END_DATE_MIN")
    if configured:
        return configured
    start_date = datetime.now(timezone.utc).date() - timedelta(days=fetch_lookback_days())
    return f"{start_date.isoformat()}T00:00:00Z"


def fetch_markets(after_cursor=None, end_date_min=None):
    """Fetch one cursor-paginated page of closed markets with the Up or Down tag."""
    params = {
        "limit": str(PAGE_LIMIT),
        "tag_id": str(TAG_ID),
        "closed": "true",
        "order": "endDate",
        "ascending": "false",
    }
    if end_date_min:
        params["end_date_min"] = end_date_min
    if after_cursor:
        params["after_cursor"] = after_cursor
    url = f"{GAMMA_API}/markets/keyset?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-slug-fetcher/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  [WARN] API error at cursor={after_cursor}: {e}", flush=True)
        return [], None

    if not isinstance(data, dict):
        return [], None

    markets = data.get("markets", [])
    if not isinstance(markets, list):
        return [], None

    return [m for m in markets if isinstance(m, dict)], data.get("next_cursor")
