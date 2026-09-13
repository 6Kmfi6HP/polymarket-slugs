"""Build layer: raw observations -> daily CSV partitions + summaries.

Pure projection over ``data/raw/``.  The build keeps a state file
(``data/_build_state.json``) with per-file signatures and per-partition row
counts so that only partitions with new observations are rewritten.  The
first build over a legacy date partition seeds the raw store from the old
CSV layer (records marked ``"seeded": true``) so that raw becomes the single
source of truth for every partition, then rewrites the CSVs in the extended
schema.
"""

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .rawstore import (
    canary_fields,
    partition_path,
    payload_hash,
    quarantine_count,
    raw_dir,
    read_sightings,
    append_sightings,
)
from .slugutil import parse_slug_metadata
from .view import CSV_COLUMNS, INTERVALS, compile_rows, _json_parse

STATE_FILE = "_build_state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_path(data_dir) -> Path:
    return Path(data_dir) / STATE_FILE


def load_state(data_dir) -> dict:
    path = state_path(data_dir)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {"raw_files": {}, "partitions": {}, "seeded_rows_total": 0}


def save_state(data_dir, state) -> None:
    path = state_path(data_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def legacy_seed_payload(row: dict) -> dict:
    """Reconstruct a minimal API-shaped payload from a legacy CSV row.

    Values are placed at the exact source paths the view extracts from, so a
    seeded row round-trips to the same CSV values.  Fields the legacy layer
    never captured stay absent and therefore empty forever.
    """
    market = {
        "slug": row.get("slug", ""),
        "question": row.get("question", ""),
        "endDate": row.get("endDate", ""),
        "outcomePrices": row.get("outcomePrices", ""),
        "lastTradePrice": row.get("lastTradePrice", ""),
        "volume": row.get("volume", ""),
        "oneHourPriceChange": row.get("oneHourPriceChange", ""),
        "spread": row.get("spread", ""),
        "umaResolutionStatus": row.get("umaResolutionStatus", ""),
        "conditionId": row.get("conditionId", ""),
        "resolutionSource": row.get("resolutionSource", ""),
        "closedTime": row.get("closedTime", ""),
    }
    event = {}
    event_meta = {}
    if row.get("priceToBeat"):
        event_meta["priceToBeat"] = row["priceToBeat"]
    if row.get("finalPrice"):
        event_meta["finalPrice"] = row["finalPrice"]
    if row.get("openInterest"):
        event["openInterest"] = row["openInterest"]
    if event_meta:
        event["eventMetadata"] = event_meta
    winning = row.get("winningOutcome", "")
    if winning in ("Up", "Down"):
        other = "Down" if winning == "Up" else "Up"
        prices = _json_parse(row.get("outcomePrices"))
        if len(prices) == 2 and prices[1] == "1":
            market["outcomes"] = [other, winning]
        else:
            market["outcomes"] = [winning, other]
    if event:
        market["events"] = [event]
    return market


def _legacy_rows(data_dir, date_key):
    rows = []
    for interval in INTERVALS:
        path = Path(data_dir) / date_key / f"{interval}.csv"
        if not path.exists():
            continue
        with open(path, newline="") as f:
            rows.extend(csv.DictReader(f))
    return rows


def _write_interval_csv(data_dir, date_key, interval, rows) -> int:
    out_dir = Path(data_dir) / date_key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{interval}.csv"
    tmp = out_dir / f".{interval}.csv.tmp{os.getpid()}"
    count = 0
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {c: ("" if row.get(c) is None else row.get(c, "")) for c in CSV_COLUMNS}
            )
            count += 1
    os.replace(tmp, out_file)
    return count


def rebuild_partition(data_dir, date_key, state) -> dict:
    """Recompile one date partition from raw (seeding from legacy if needed)."""
    data_dir = Path(data_dir)
    path = partition_path(data_dir, date_key)
    sightings = read_sightings(path)
    raw_slugs = {rec.get("market", {}).get("slug", "") for rec in sightings}

    seed_records = []
    for row in _legacy_rows(data_dir, date_key):
        slug = row.get("slug", "")
        if slug and slug not in raw_slugs:
            payload = legacy_seed_payload(row)
            seed_records.append(
                {
                    "fetched_at": None,
                    "seeded": True,
                    "hash": payload_hash(payload),
                    "market": payload,
                }
            )
    if seed_records:
        append_sightings(path, seed_records)
        state["seeded_rows_total"] = state.get("seeded_rows_total", 0) + len(seed_records)
        sightings = read_sightings(path)

    rows = compile_rows(sightings)
    counts = {}
    for interval in INTERVALS:
        subset = [r for r in rows if r.get("interval") == interval]
        counts[interval] = len(subset)
        if subset:
            _write_interval_csv(data_dir, date_key, interval, subset)
    state["partitions"][date_key] = counts
    return counts


def _file_signature(path: Path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def _discover_work(data_dir, state, force: bool) -> set:
    """Dates needing a rebuild: changed raw files + unbacked legacy partitions."""
    data_dir = Path(data_dir)
    work = set()

    rdir = raw_dir(data_dir)
    if rdir.exists():
        for path in sorted(rdir.glob("*.jsonl.xz")):
            date_key = path.stem.replace(".jsonl", "")
            if force or state["raw_files"].get(path.name) != _file_signature(path):
                work.add(date_key)

    if data_dir.exists():
        for child in sorted(data_dir.iterdir()):
            if not child.is_dir() or len(child.name) != 10 or child.name[4] != "-":
                continue
            if not partition_path(data_dir, child.name).exists():
                if any(child.glob("*.csv")):
                    work.add(child.name)
    return work


def build(data_dir, force: bool = False) -> dict:
    """Rebuild changed partitions and refresh the summary files."""
    data_dir = Path(data_dir)
    state = load_state(data_dir)
    work = _discover_work(data_dir, state, force)

    for date_key in sorted(work):
        rebuild_partition(data_dir, date_key, state)
        path = partition_path(data_dir, date_key)
        if path.exists():
            state["raw_files"][path.name] = _file_signature(path)

    save_state(data_dir, state)
    write_summary(data_dir, state)
    write_pipeline_json(data_dir, state)

    totals = {
        interval: sum(c.get(interval, 0) for c in state["partitions"].values())
        for interval in INTERVALS
    }
    return {
        "partitions_rebuilt": sorted(work),
        "rows_written": sum(totals.values()),
        "totals": totals,
        "seeded_rows_total": state.get("seeded_rows_total", 0),
    }


def write_summary(data_dir, state) -> None:
    """Regenerate data/_summary.json (same shape as the legacy writer)."""
    summary = {}
    for date_key in sorted(state["partitions"]):
        counts = state["partitions"][date_key]
        entry = {interval: counts[interval] for interval in INTERVALS if counts.get(interval)}
        if entry:
            summary[date_key] = entry
    (Path(data_dir) / "_summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def write_pipeline_json(data_dir, state) -> None:
    totals = {
        interval: sum(c.get(interval, 0) for c in state["partitions"].values())
        for interval in INTERVALS
    }
    payload = {
        "generated_at": _now_iso(),
        "rows": totals,
        "partitions": sum(
            1 for c in state["partitions"].values() if any(c.get(i) for i in INTERVALS)
        ),
        "quarantine_total": quarantine_count(data_dir),
        "seeded_rows_total": state.get("seeded_rows_total", 0),
        "canary_fields": canary_fields(data_dir),
    }
    (Path(data_dir) / "_pipeline.json").write_text(json.dumps(payload, indent=2) + "\n")
