"""Append-only raw storage for API payloads.

Layout (this layer is the source of truth for the build step):

  data/raw/YYYY-MM-DD.jsonl.xz   one JSON object per line, partitioned by the
                                 market window's UTC start date:
                                   {"fetched_at": iso, "hash": sha256,
                                    "market": {...}}
                                 optionally ``"seeded": true`` for records
                                 reconstructed from the legacy CSV layer
  data/raw/_quarantine.jsonl     observations whose slug failed validation

Payloads are stored verbatim except RAW_DENYLIST fields: description/icon/
image are presentation-only boilerplate that would otherwise dominate the
repository size (measured 5.6 KB vs 4.0 KB per market; xz brings a day to
~1.3 MB).  Everything else is lossless.
"""

import hashlib
import json
import lzma
import os
from datetime import datetime, timezone
from pathlib import Path

RAW_DENYLIST = {"description", "icon", "image"}


def raw_dir(data_dir) -> Path:
    return Path(data_dir) / "raw"


def partition_path(data_dir, date_key: str) -> Path:
    return raw_dir(data_dir) / f"{date_key}.jsonl.xz"


def quarantine_path(data_dir) -> Path:
    return raw_dir(data_dir) / "_quarantine.jsonl"


def canary_path(data_dir) -> Path:
    return raw_dir(data_dir) / "_canary.jsonl"


def strip_payload(market: dict) -> dict:
    """Return the payload minus presentation-only boilerplate fields."""
    cleaned = {k: v for k, v in market.items() if k not in RAW_DENYLIST}
    events = cleaned.get("events")
    if isinstance(events, list):
        cleaned["events"] = [
            {k: v for k, v in e.items() if k not in RAW_DENYLIST}
            if isinstance(e, dict)
            else e
            for e in events
        ]
    return cleaned


def payload_hash(market: dict) -> str:
    canonical = json.dumps(market, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _write_atomic(path: Path, lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    blob = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    with lzma.open(tmp, "wt", encoding="utf-8", preset=9) as f:
        f.write(blob)
    os.replace(tmp, path)


def read_sightings(path: Path) -> list[dict]:
    """Return every observation record stored in a partition file."""
    if not path.exists():
        return []
    records = []
    with lzma.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn tail line must not break the pipeline
    return records


def latest_state(path: Path) -> dict[str, str]:
    """Return {slug: payload_hash} of the latest sighting per slug."""
    state = {}
    for rec in read_sightings(path):
        slug = rec.get("market", {}).get("slug", "")
        if slug and rec.get("hash"):
            state[slug] = rec["hash"]
    return state


def append_sightings(path: Path, records: list[dict]) -> int:
    """Append observation records to a partition file (atomic rewrite)."""
    if not records:
        return 0
    existing_lines = []
    if path.exists():
        with lzma.open(path, "rt", encoding="utf-8") as f:
            existing_lines = f.read().splitlines()
    new_lines = [json.dumps(rec, separators=(",", ":")) for rec in records]
    _write_atomic(path, existing_lines + new_lines)
    return len(new_lines)


def quarantine_slugs(data_dir) -> set:
    """Slugs already recorded in the quarantine log."""
    path = quarantine_path(data_dir)
    if not path.exists():
        return set()
    seen = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line).get("slug"))
            except json.JSONDecodeError:
                continue
    return seen


def append_quarantine(data_dir, records: list[dict]) -> None:
    """Append quarantine records once per slug (log must stay bounded)."""
    if not records:
        return
    already = quarantine_slugs(data_dir)
    fresh = [rec for rec in records if rec.get("slug") not in already]
    if not fresh:
        return
    path = quarantine_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in fresh:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def append_canary(data_dir, fields_by_level: dict[str, set]) -> None:
    """Persist never-seen-before API fields for the schema-drift audit."""
    if not any(fields_by_level.values()):
        return
    path = canary_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat()
    known = set(canary_fields(data_dir))
    with open(path, "a", encoding="utf-8") as f:
        for level, fields in fields_by_level.items():
            for field in sorted(fields):
                if f"{level}.{field}" in known:
                    continue
                f.write(
                    json.dumps(
                        {"fetched_at": fetched_at, "level": level, "field": field},
                        separators=(",", ":"),
                    )
                    + "\n"
                )


def quarantine_count(data_dir) -> int:
    path = quarantine_path(data_dir)
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def canary_fields(data_dir) -> list[str]:
    """Return the distinct never-seen-before fields recorded so far."""
    path = canary_path(data_dir)
    if not path.exists():
        return []
    seen = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add(f"{rec.get('level')}.{rec.get('field')}")
    return sorted(seen)
