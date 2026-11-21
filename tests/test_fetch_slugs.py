"""Tests for the pull/build pipeline in scripts/updown/."""

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "fetch-slugs.py"
sys.path.insert(0, str(SCRIPTS))

from updown import api, build as build_layer, pull as pull_layer, rawstore  # noqa: E402
from updown.slugutil import parse_slug_metadata  # noqa: E402
from updown.view import CSV_COLUMNS, compile_rows, extract_row  # noqa: E402

LEGACY_COLUMNS = [
    "slug", "asset", "question", "endDate", "eventStartTime", "priceToBeat",
    "winningOutcome", "outcomePrices", "lastTradePrice", "volume",
    "openInterest", "oneHourPriceChange", "spread", "umaResolutionStatus",
    "conditionId", "resolutionSource",
]


def market(slug, *, price_to_beat=None, final_price=None, closed_time=None,
           open_interest=None, winning="Up", **extra):
    payload = {
        "slug": slug,
        "question": f"{slug} question",
        "endDate": "2026-07-04T16:10:00Z",
        "closedTime": closed_time,
        "outcomePrices": '["1","0"]' if winning == "Up" else '["0","1"]',
        "outcomes": '["Up","Down"]',
        "conditionId": f"cond-{slug}",
        "events": [
            {
                "openInterest": open_interest,
                "eventMetadata": {
                    "priceToBeat": price_to_beat,
                    "finalPrice": final_price,
                },
            }
        ],
    }
    payload.update(extra)
    return payload


def sighting(market_payload, fetched_at="2026-07-04T17:00:00Z"):
    cleaned = rawstore.strip_payload(market_payload)
    return {
        "fetched_at": fetched_at,
        "hash": rawstore.payload_hash(cleaned),
        "market": cleaned,
    }


class SlugParsingTest(unittest.TestCase):
    def test_parse_slug_metadata_returns_interval_asset_and_utc_start_time(self):
        self.assertEqual(
            parse_slug_metadata("btc-updown-5m-1783181100"),
            ("BTC", "5m", "2026-07-04T16:05:00Z"),
        )
        self.assertEqual(
            parse_slug_metadata("sol-updown-15m-1783179900"),
            ("SOL", "15m", "2026-07-04T15:45:00Z"),
        )
        self.assertIsNone(parse_slug_metadata("bitcoin-up-or-down-in-q2"))


class ApiConfigTest(unittest.TestCase):
    def test_end_date_min_prefers_explicit_env(self):
        with patch.dict(os.environ, {"FETCH_END_DATE_MIN": "2026-07-01T00:00:00Z"}):
            self.assertEqual(api.fetch_end_date_min(), "2026-07-01T00:00:00Z")

    def test_end_date_min_uses_lookback_days_env(self):
        env = {"FETCH_LOOKBACK_DAYS": "5"}
        env.pop("FETCH_END_DATE_MIN", None)
        with patch.dict(os.environ, env, clear=True):
            expected = (
                datetime.now(timezone.utc).date() - timedelta(days=5)
            ).isoformat()
            self.assertEqual(api.fetch_end_date_min(), f"{expected}T00:00:00Z")


class PullTest(unittest.TestCase):
    def _pages(self, changed_market=None, include_canary_market=False):
        btc = market(
            "btc-updown-5m-1783181100",
            price_to_beat=100.0,
            final_price=101.5,
            closed_time="2026-07-04T16:10:03Z",
            open_interest=1234.0,
        )
        if changed_market == "btc":
            btc["events"][0]["openInterest"] = 999.0
        sol = market("sol-updown-15m-1783179900", price_to_beat=7.5)
        canary = market("doge-updown-5m-1783181100", price_to_beat=1.0)
        if include_canary_market:
            canary["brandNewField"] = {"nested": True}
        page_one = [btc, {"slug": "not-a-updown-slug", "question": "?"}]
        if include_canary_market:
            page_one.append(canary)
        page_two = [sol]
        return {
            None: (page_one, "cursor-2"),
            "cursor-2": (page_two, None),
        }

    def _pull(self, data_dir, **kwargs):
        pages = kwargs.pop("pages")
        calls = []

        def fake_fetcher(after_cursor=None, end_date_min=None):
            calls.append(after_cursor)
            return pages[after_cursor]

        stats = pull_layer.pull(data_dir, fetcher=fake_fetcher, **kwargs)
        return stats, calls

    def test_pull_writes_raw_partitions_and_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats, calls = self._pull(tmp, pages=self._pages())
            self.assertEqual(calls, [None, "cursor-2"])
            self.assertEqual(stats["markets"], 3)
            self.assertEqual(stats["new_observations"], 2)
            self.assertEqual(stats["by_date"]["2026-07-04"], 2)
            self.assertEqual(stats["quarantined"], 1)

            records = rawstore.read_sightings(rawstore.partition_path(tmp, "2026-07-04"))
            self.assertEqual([r["market"]["slug"] for r in records],
                             ["btc-updown-5m-1783181100", "sol-updown-15m-1783179900"])
            # presentation fields stripped, data fields intact
            self.assertNotIn("description", records[0]["market"])
            self.assertEqual(records[0]["market"]["closedTime"], "2026-07-04T16:10:03Z")

            quarantine = rawstore.quarantine_path(tmp).read_text().strip().splitlines()
            self.assertEqual(len(quarantine), 1)
            self.assertEqual(json.loads(quarantine[0])["slug"], "not-a-updown-slug")

    def test_pull_is_idempotent_and_re_sights_changed_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = self._pages()
            self._pull(tmp, pages=pages)
            stats, _ = self._pull(tmp, pages=pages)
            self.assertEqual(stats["new_observations"], 0)

            stats, _ = self._pull(tmp, pages=self._pages(changed_market="btc"))
            self.assertEqual(stats["new_observations"], 1)
            records = rawstore.read_sightings(rawstore.partition_path(tmp, "2026-07-04"))
            btc_sightings = [r for r in records
                             if r["market"]["slug"] == "btc-updown-5m-1783181100"]
            self.assertEqual(len(btc_sightings), 2)

    def test_pull_quarantine_is_deduplicated_across_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = self._pages()
            self._pull(tmp, pages=pages)
            self._pull(tmp, pages=pages)
            lines = rawstore.quarantine_path(tmp).read_text().strip().splitlines()
            self.assertEqual(len(lines), 1)

    def test_pull_records_never_seen_api_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._pull(tmp, pages=self._pages(include_canary_market=True))
            self.assertIn("market.brandNewField", rawstore.canary_fields(tmp))


class ViewTest(unittest.TestCase):
    def test_extract_row_projects_nested_and_new_fields(self):
        row = extract_row(market(
            "btc-updown-5m-1783181100",
            price_to_beat=100.0,
            final_price=101.5,
            closed_time="2026-07-04T16:10:03Z",
            open_interest=1234.0,
        ))
        self.assertEqual(row["asset"], "BTC")
        self.assertEqual(row["eventStartTime"], "2026-07-04T16:05:00Z")
        self.assertEqual(row["priceToBeat"], 100.0)
        self.assertEqual(row["finalPrice"], 101.5)
        self.assertEqual(row["closedTime"], "2026-07-04T16:10:03Z")
        self.assertEqual(row["winningOutcome"], "Up")
        self.assertEqual(row["openInterest"], 1234.0)
        self.assertEqual(set(row) - {"interval"}, set(CSV_COLUMNS))

    def test_extract_row_normalizes_missing_values_to_empty_strings(self):
        row = extract_row({"slug": "btc-updown-5m-1783181100", "lastTradePrice": None})
        self.assertEqual(row["lastTradePrice"], "")
        self.assertEqual(row["finalPrice"], "")

    def test_compile_rows_uses_last_sighting_per_slug(self):
        stale = sighting(market("btc-updown-5m-1783181100"), fetched_at="t1")
        fresh_market = market("btc-updown-5m-1783181100", price_to_beat=100.0)
        fresh = sighting(fresh_market, fetched_at="t2")
        rows = compile_rows([stale, fresh])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["priceToBeat"], 100.0)
        self.assertEqual(rows[0]["interval"], "5m")


class BuildTest(unittest.TestCase):
    def _write_legacy_partition(self, data_dir):
        legacy_dir = Path(data_dir) / "2026-07-04"
        legacy_dir.mkdir(parents=True)
        with (legacy_dir / "5m.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LEGACY_COLUMNS)
            writer.writeheader()
            writer.writerow({
                "slug": "btc-updown-5m-1783181100",
                "asset": "BTC",
                "question": "legacy question",
                "endDate": "2026-07-04T16:10:00Z",
                "eventStartTime": "2026-07-04T16:05:00Z",
                "priceToBeat": "100.0",
                "winningOutcome": "Up",
                "outcomePrices": '["1","0"]',
                "conditionId": "cond-btc",
            })
        with (legacy_dir / "15m.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LEGACY_COLUMNS)
            writer.writeheader()
            writer.writerow({
                "slug": "sol-updown-15m-1783179900",
                "asset": "SOL",
                "question": "legacy sol",
                "endDate": "2026-07-04T16:00:00Z",
                "eventStartTime": "2026-07-04T15:45:00Z",
                "priceToBeat": "7.5",
                "winningOutcome": "Down",
                "outcomePrices": '["0","1"]',
            })

    def test_build_seeds_legacy_partitions_and_compiles_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_legacy_partition(tmp)
            rawstore.append_sightings(
                rawstore.partition_path(tmp, "2026-07-04"),
                [sighting(market(
                    "eth-updown-5m-1783181100",
                    price_to_beat=2000.0,
                    final_price=2001.0,
                    closed_time="2026-07-04T16:10:02Z",
                ))],
            )

            stats = build_layer.build(tmp)
            self.assertEqual(stats["partitions_rebuilt"], ["2026-07-04"])
            self.assertEqual(stats["totals"], {"5m": 2, "15m": 1})
            self.assertEqual(stats["seeded_rows_total"], 2)

            with (Path(tmp) / "2026-07-04" / "5m.csv").open(newline="") as f:
                rows = {r["slug"]: r for r in csv.DictReader(f)}
            self.assertEqual(set(rows), {"btc-updown-5m-1783181100",
                                         "eth-updown-5m-1783181100"})
            # seeded row keeps legacy values; never-captured fields stay empty
            self.assertEqual(rows["btc-updown-5m-1783181100"]["priceToBeat"], "100.0")
            self.assertEqual(rows["btc-updown-5m-1783181100"]["winningOutcome"], "Up")
            self.assertEqual(rows["btc-updown-5m-1783181100"]["finalPrice"], "")
            # raw-backed row carries the full schema
            self.assertEqual(rows["eth-updown-5m-1783181100"]["finalPrice"], "2001.0")
            self.assertEqual(rows["eth-updown-5m-1783181100"]["closedTime"],
                             "2026-07-04T16:10:02Z")

            with (Path(tmp) / "2026-07-04" / "15m.csv").open(newline="") as f:
                rows_15 = {r["slug"]: r for r in csv.DictReader(f)}
            self.assertEqual(rows_15["sol-updown-15m-1783179900"]["winningOutcome"], "Down")

            summary = json.loads((Path(tmp) / "_summary.json").read_text())
            self.assertEqual(summary, {"2026-07-04": {"5m": 2, "15m": 1}})

            pipeline = json.loads((Path(tmp) / "_pipeline.json").read_text())
            self.assertEqual(pipeline["rows"], {"5m": 2, "15m": 1})
            self.assertEqual(pipeline["seeded_rows_total"], 2)

            # seeds were written back into the raw store
            records = rawstore.read_sightings(rawstore.partition_path(tmp, "2026-07-04"))
            self.assertEqual(len(records), 3)

    def test_build_is_incremental_after_first_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_legacy_partition(tmp)
            build_layer.build(tmp)
            second = build_layer.build(tmp)
            self.assertEqual(second["partitions_rebuilt"], [])

    def test_build_force_rebuilds_every_partition(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_legacy_partition(tmp)
            build_layer.build(tmp)
            forced = build_layer.build(tmp, force=True)
            self.assertEqual(forced["partitions_rebuilt"], ["2026-07-04"])
            # no duplicate seeds on force rebuild
            self.assertEqual(forced["seeded_rows_total"], 2)


class CliTest(unittest.TestCase):
    def test_help_exits_zero(self):
        for argv in (["--help"], ["pull", "--help"], ["build", "--help"]):
            result = subprocess.run(
                [sys.executable, str(SCRIPT), *argv],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
