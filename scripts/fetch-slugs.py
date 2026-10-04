#!/usr/bin/env python3
"""Polymarket UPDOWN Market Data Pipeline

Fetches closed 5m/15m UPDOWN markets from the Gamma API, stores lossless
raw observations, and compiles them into daily CSV files.

Layers:
  pull    Gamma API -> data/raw/YYYY-MM-DD.jsonl.xz
          Lossless append-only observations (minus presentation boilerplate),
          slug quarantine, and schema-drift canary.
  build   raw observations -> data/{YYYY-MM-DD}/{interval}.csv
          Pure projection with finalPrice/closedTime columns; only partitions
          with new observations are rewritten.  Legacy date partitions are
          seeded from the old CSV layer on first build.
  all     pull + build (default, used by the GitHub Action)

Designed to run as a GitHub Action on a schedule.  Examples:

  python scripts/fetch-slugs.py                     # pull + build
  python scripts/fetch-slugs.py pull --lookback-days 7
  python scripts/fetch-slugs.py build --force       # rebuild all partitions
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from updown import build as build_layer
from updown import pull as pull_layer
from updown import default_data_dir


def _print_pull_stats(stats: dict) -> None:
    print(f"  pages={stats['pages']}  markets={stats['markets']}  "
          f"new_observations={stats['new_observations']}", flush=True)
    for date_key, added in sorted(stats["by_date"].items()):
        if added:
            print(f"    {date_key}: +{added}", flush=True)
    if stats["quarantined"]:
        print(f"  [WARN] quarantined {stats['quarantined']} observations "
              f"(see data/raw/_quarantine.jsonl)", flush=True)
    for level, fields in stats["canary"].items():
        for field in fields:
            print(f"  [WARN] new API field ({level}): {field}", flush=True)


def cmd_pull(args, data_dir) -> dict:
    print("[1/2] Pulling closed markets from Gamma API...", flush=True)
    stats = pull_layer.pull(
        data_dir,
        lookback_days=args.lookback_days,
        end_date_min=args.end_date_min,
    )
    print(f"  end_date_min={stats['end_date_min']}", flush=True)
    _print_pull_stats(stats)
    return stats


def cmd_build(args, data_dir) -> dict:
    print("[2/2] Building CSV partitions from raw observations...", flush=True)
    stats = build_layer.build(data_dir, force=args.force)
    rebuilt = stats["partitions_rebuilt"]
    print(f"  partitions rebuilt: {len(rebuilt)}" + (" (force)" if args.force else ""),
          flush=True)
    for date_key in rebuilt[:10]:
        counts = build_layer.load_state(data_dir)["partitions"].get(date_key, {})
        print(f"    {date_key}: 5m={counts.get('5m', 0)} 15m={counts.get('15m', 0)}",
              flush=True)
    if len(rebuilt) > 10:
        print(f"    ... and {len(rebuilt) - 10} more", flush=True)
    totals = stats["totals"]
    print(f"  Totals: {totals['5m']} x 5m, {totals['15m']} x 15m", flush=True)
    if stats["seeded_rows_total"]:
        print(f"  Seeded from legacy CSV layer: {stats['seeded_rows_total']} rows",
              flush=True)
    return stats


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] not in {"pull", "build", "all", "-h", "--help"}:
        argv = ["all"] + argv

    parser = argparse.ArgumentParser(description="Polymarket UPDOWN data pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_pull = sub.add_parser("pull", help="Gamma API -> raw observations")
    p_pull.add_argument("--lookback-days", type=int, default=None)
    p_pull.add_argument("--end-date-min", default=None)

    p_build = sub.add_parser("build", help="raw observations -> CSV partitions")
    p_build.add_argument("--force", action="store_true",
                         help="rebuild every partition from raw")

    p_all = sub.add_parser("all", help="pull + build")
    p_all.add_argument("--lookback-days", type=int, default=None)
    p_all.add_argument("--end-date-min", default=None)
    p_all.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    data_dir = default_data_dir()

    print("=" * 60, flush=True)
    print("  Polymarket UPDOWN Data Pipeline", flush=True)
    print("=" * 60, flush=True)

    if args.command == "pull":
        cmd_pull(args, data_dir)
    elif args.command == "build":
        cmd_build(args, data_dir)
    else:
        pull_stats = cmd_pull(args, data_dir)
        cmd_build(args, data_dir)
        if pull_stats["new_observations"] == 0:
            print("\n  No new observations; build was a no-op.", flush=True)

    print("\nDone.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
