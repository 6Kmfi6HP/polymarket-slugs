"""Pull layer: Gamma API -> append-only raw observations.

Responsibilities (and nothing else):

  * page through the closed-market API
  * validate slugs; park failures in ``_quarantine.jsonl`` instead of
    silently dropping them
  * strip presentation boilerplate, hash payloads, and append every
    *changed* observation to the day-partitioned raw store
  * run the schema-drift canary over fresh payloads
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import api, rawstore
from .canary import diff_keys
from .slugutil import parse_slug_metadata


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_end_date_min(lookback_days=None, end_date_min=None) -> str:
    """CLI args override the environment; environment overrides the default."""
    if end_date_min:
        return end_date_min
    if lookback_days is not None:
        start = datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
        return f"{start.isoformat()}T00:00:00Z"
    return api.fetch_end_date_min()


def pull(data_dir, lookback_days=None, end_date_min=None, fetcher=None) -> dict:
    """Fetch closed markets and append changed observations to raw storage."""
    data_dir = Path(data_dir)
    fetcher = fetcher or api.fetch_markets
    end_date_min = resolve_end_date_min(lookback_days, end_date_min)

    stats = {
        "end_date_min": end_date_min,
        "pages": 0,
        "markets": 0,
        "new_observations": 0,
        "by_date": {},
        "quarantined": 0,
        "canary": {},
    }

    candidates: dict = {}
    seen_in_batch: dict = {}
    quarantine_records: list = []
    fetched_markets: list = []
    after_cursor = None

    while True:
        markets, next_cursor = fetcher(after_cursor, end_date_min)
        if not markets:
            break
        stats["pages"] += 1
        for market in markets:
            stats["markets"] += 1
            fetched_markets.append(market)
            slug = market.get("slug", "")
            meta = parse_slug_metadata(slug)
            if not meta:
                quarantine_records.append(
                    {
                        "fetched_at": _now_iso(),
                        "slug": slug,
                        "reason": "slug_does_not_match_updown_pattern",
                    }
                )
                continue
            cleaned = rawstore.strip_payload(market)
            digest = rawstore.payload_hash(cleaned)
            if seen_in_batch.get(slug) == digest:
                continue
            seen_in_batch[slug] = digest
            date_key = meta[2][:10]
            candidates.setdefault(date_key, []).append(
                {"fetched_at": _now_iso(), "hash": digest, "market": cleaned}
            )
        if not next_cursor:
            break
        after_cursor = next_cursor

    for date_key in sorted(candidates):
        path = rawstore.partition_path(data_dir, date_key)
        latest = rawstore.latest_state(path)
        fresh = [
            rec
            for rec in candidates[date_key]
            if latest.get(rec["market"]["slug"]) != rec["hash"]
        ]
        appended = rawstore.append_sightings(path, fresh)
        stats["by_date"][date_key] = appended
        stats["new_observations"] += appended

    rawstore.append_quarantine(data_dir, quarantine_records)
    stats["quarantined"] = len(quarantine_records)

    report = diff_keys([rawstore.strip_payload(m) for m in fetched_markets])
    rawstore.append_canary(data_dir, report)
    stats["canary"] = {level: sorted(fields) for level, fields in report.items()}
    return stats
