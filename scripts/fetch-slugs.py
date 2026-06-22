#!/usr/bin/env python3
"""
Polymarket UPDOWN Slug Fetcher

Fetches recent 5m/15m UPDOWN market slugs from the Gamma API,
groups them by day, and stores in data/{YYYY-MM-DD}/{interval}.txt.

Designed to run as a GitHub Action on a schedule.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


GAMMA_API = "https://gamma-api.polymarket.com"
TAG_ID = 102127  # "Up or Down" tag

# Relative to repo root
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def fetch_markets(offset: int = 0) -> list[dict]:
    """Fetch a page of closed markets with the Up or Down tag."""
    url = (
        f"{GAMMA_API}/markets"
        f"?limit=200&offset={offset}"
        f"&tag_id={TAG_ID}&closed=true"
        f"&order=endDate&ascending=false"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-slug-fetcher/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  [WARN] API error at offset={offset}: {e}", flush=True)
        return []

    if not isinstance(data, list):
        return []
    return [m for m in data if isinstance(m, dict)]


def collect_slugs() -> dict[str, dict[str, set[str]]]:
    """
    Offset-paginate through the Gamma API to collect all available
    5m/15m UPDOWN slugs. Returns nested dict:
        {date_str: {"5m": {slug, ...}, "15m": {slug, ...}}}
    """
    groups: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"5m": set(), "15m": set()}
    )
    offset = 0
    page = 0

    while True:
        markets = fetch_markets(offset)
        if not markets:
            break

        for m in markets:
            slug: str = m.get("slug", "")
            interval = None
            if "updown-5m" in slug:
                interval = "5m"
            elif "updown-15m" in slug:
                interval = "15m"
            else:
                continue

            end_date = m.get("endDate", "")
            if end_date:
                date_key = end_date[:10]
                groups[date_key][interval].add(slug)

        offset += 200
        page += 1
        if page % 10 == 0:
            total = sum(len(v[i]) for v in groups.values() for i in ("5m", "15m"))
            print(f"  page={page} offset={offset} total_slugs={total}", flush=True)

    return groups


def merge_with_existing(groups: dict) -> dict:
    """Merge newly fetched slugs with existing files on disk."""
    data_dir = DATA_DIR
    if not data_dir.exists():
        return groups

    for date_dir in sorted(data_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        date_key = date_dir.name
        if date_key not in groups:
            groups[date_key] = {"5m": set(), "15m": set()}
        for interval in ("5m", "15m"):
            interval_file = date_dir / f"{interval}.txt"
            if interval_file.exists():
                existing = set(
                    line.strip()
                    for line in interval_file.read_text().strip().splitlines()
                    if line.strip()
                )
                groups[date_key][interval].update(existing)
    return groups


def write_slugs(groups: dict) -> None:
    """Write slugs to data/{date}/{interval}.txt files."""
    total_written = 0
    for date_key in sorted(groups):
        for interval in ("5m", "15m"):
            slugs = groups[date_key].get(interval, set())
            if not slugs:
                continue

            out_dir = DATA_DIR / date_key
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"{interval}.txt"

            sorted_slugs = sorted(slugs)
            out_file.write_text("\n".join(sorted_slugs) + "\n")
            total_written += len(sorted_slugs)

    print(f"\n  Wrote {total_written} slugs across {len(groups)} days", flush=True)


def generate_summary(groups: dict) -> None:
    """Generate a summary JSON file for easy reference."""
    summary = {}
    for date_key in sorted(groups):
        summary[date_key] = {}
        for interval in ("5m", "15m"):
            count = len(groups[date_key].get(interval, set()))
            if count:
                summary[date_key][interval] = count

    summary_file = DATA_DIR / "_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"  Summary written to {summary_file}", flush=True)

    # Print a quick overview
    total_5m = sum(v.get("5m", 0) for v in summary.values())
    total_15m = sum(v.get("15m", 0) for v in summary.values())
    print(f"  Total: {total_5m} x 5m, {total_15m} x 15m", flush=True)


def main():
    print("=" * 60, flush=True)
    print(f"  Polymarket Slug Fetcher — {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S UTC}", flush=True)
    print("=" * 60, flush=True)

    print("\n[1/3] Fetching slugs from Gamma API...", flush=True)
    groups = collect_slugs()
    total = sum(len(v[i]) for v in groups.values() for i in ("5m", "15m"))
    print(f"  Fetched {total} new slugs", flush=True)

    print("\n[2/3] Merging with existing data...", flush=True)
    groups = merge_with_existing(groups)
    total = sum(len(v[i]) for v in groups.values() for i in ("5m", "15m"))
    print(f"  Total after merge: {total} unique slugs", flush=True)

    print("\n[3/3] Writing files...", flush=True)
    write_slugs(groups)
    generate_summary(groups)

    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()
