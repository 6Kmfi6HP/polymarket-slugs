"""CSV projection compiled from the raw observation layer.

The daily ``{date}/{interval}.csv`` files are a *view*: every field here can
be re-derived from ``data/raw/`` at any time, and adding a column is a
view-only change (no re-fetching, no backfill problem).
"""

import json

from .slugutil import parse_slug_metadata

CSV_COLUMNS = [
    "slug",
    "asset",
    "question",
    "endDate",
    "eventStartTime",
    "priceToBeat",
    "winningOutcome",
    "outcomePrices",
    "lastTradePrice",
    "volume",
    "openInterest",
    "oneHourPriceChange",
    "spread",
    "umaResolutionStatus",
    "conditionId",
    "resolutionSource",
    "finalPrice",
    "closedTime",
]

INTERVALS = ("5m", "15m")


def _json_parse(val):
    """Safely parse a JSON string field (outcomes/outcomePrices)."""
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def extract_row(market: dict) -> dict:
    """Project one raw market payload onto the flat CSV schema."""
    slug = market.get("slug", "")
    question = market.get("question", "")

    outcomes = _json_parse(market.get("outcomes"))
    outcome_prices = _json_parse(market.get("outcomePrices"))
    winning_outcome = ""
    if len(outcome_prices) == 2 and len(outcomes) == 2:
        if outcome_prices[0] == "1":
            winning_outcome = outcomes[0]
        elif outcome_prices[1] == "1":
            winning_outcome = outcomes[1]

    events = market.get("events")
    event = events[0] if isinstance(events, list) and len(events) > 0 else {}
    event_meta = event.get("eventMetadata") or {}
    if not isinstance(event_meta, dict):
        event_meta = {}

    slug_meta = parse_slug_metadata(slug)
    if slug_meta:
        asset, _, event_start_time = slug_meta
    else:
        # eventStartTime is the UPDOWN interval start; startDate is creation time.
        event_start_time = market.get("eventStartTime") or event.get("startTime") or ""
        series_slug = event.get("seriesSlug") or ""
        if series_slug:
            asset = (
                series_slug
                .replace("-up-or-down-5m", "")
                .replace("-up-or-down-15m", "")
                .upper()
            )
        else:
            asset = question.split(" Up or")[0] if " Up or" in question else ""

    row = {
        "slug": slug,
        "asset": asset,
        "question": question,
        "endDate": market.get("endDate", ""),
        "eventStartTime": event_start_time,
        "priceToBeat": event_meta.get("priceToBeat"),
        "winningOutcome": winning_outcome,
        "outcomePrices": market.get("outcomePrices", ""),
        "lastTradePrice": market.get("lastTradePrice"),
        "volume": market.get("volumeNum") or market.get("volume"),
        "openInterest": event.get("openInterest"),
        "oneHourPriceChange": market.get("oneHourPriceChange"),
        "spread": market.get("spread"),
        "umaResolutionStatus": market.get("umaResolutionStatus", ""),
        "conditionId": market.get("conditionId", ""),
        "resolutionSource": market.get("resolutionSource", ""),
        "finalPrice": event_meta.get("finalPrice"),
        "closedTime": market.get("closedTime", ""),
    }
    return {k: "" if v is None else v for k, v in row.items()}


def compile_rows(sightings: list) -> list:
    """Deduplicate sightings (last one wins) and project to sorted CSV rows.

    Sightings are append-ordered raw records; for each slug the most recent
    observation (the settled final state) is used.  Rows carry an extra
    ``interval`` key used by the build step for partitioning.
    """
    latest = {}
    for rec in sightings:
        market = rec.get("market") or {}
        slug = market.get("slug", "")
        if slug:
            latest[slug] = market

    rows = []
    for slug, market in latest.items():
        row = extract_row(market)
        meta = parse_slug_metadata(slug)
        row["interval"] = meta[1] if meta else ""
        rows.append(row)

    rows.sort(
        key=lambda r: (r.get("eventStartTime", ""), r.get("asset", ""), r.get("slug", ""))
    )
    return rows
