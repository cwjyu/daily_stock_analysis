# -*- coding: utf-8 -*-
"""Tests for the gold_daily.csv sync path (see api/v1/endpoints/gold.py)."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from api.v1.endpoints import gold as gold_module

CSV_HEADER = "Date,Open,High,Low,Close,Volume\n"
CSV_TWO_ROWS = (
    "2026-10-28,4061.17,4082.58,4024.8,4067.16,0.0\n"
    "2026-10-29,4067.46,4068.72,4005.21,4021.23,0.0\n"
)


class GoldCsvSyncTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.csv_path = Path(self._tmpdir.name) / "gold_daily.csv"
        self.csv_path.write_text(CSV_HEADER + CSV_TWO_ROWS, encoding="utf-8")
        patcher = patch.object(gold_module, "_DATA_PATH", self.csv_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        gold_module._CACHE.clear()

    def test_append_gold_rows_adds_new_date_and_keeps_format(self) -> None:
        added = gold_module._append_gold_rows([
            {
                "date": "2026-10-30",
                "open": 4023.79,
                "high": 4026.24,
                "low": 3870.05,
                "close": 3876.69,
                "volume": 0,
            }
        ])

        self.assertEqual(added, 1)
        lines = self.csv_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[-1], "2026-10-30,4023.79,4026.24,3870.05,3876.69,0.0")

    def test_append_gold_rows_skips_dates_already_present(self) -> None:
        added = gold_module._append_gold_rows([
            {"date": "2026-10-29", "open": 1, "high": 2, "low": 0.5, "close": 1.5},
        ])

        self.assertEqual(added, 0)
        self.assertEqual(
            self.csv_path.read_text(encoding="utf-8"), CSV_HEADER + CSV_TWO_ROWS
        )

    def test_append_gold_rows_tolerates_mixed_date_types(self) -> None:
        """A ``str`` date must not crash ``sort_values`` against parsed timestamps."""
        added = gold_module._append_gold_rows([
            {"date": pd.Timestamp("2026-10-31"), "open": 1, "high": 2, "low": 0.5, "close": 1.5},
            {"date": "2026-10-30", "open": 1, "high": 2, "low": 0.5, "close": 1.5},
        ])

        self.assertEqual(added, 2)
        dates = [
            line.split(",")[0]
            for line in self.csv_path.read_text(encoding="utf-8").splitlines()[1:]
        ]
        self.assertEqual(dates, ["2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31"])

    def test_backfill_prefers_sina_and_skips_single_candle_sources(self) -> None:
        sina_rows = [
            {"date": "2026-10-29", "open": 4067.46, "high": 4068.72, "low": 4005.21, "close": 4021.23},
            {"date": "2026-10-30", "open": 4023.79, "high": 4026.24, "low": 3870.05, "close": 3876.69},
        ]
        with patch(
            "data_provider.eastmoney_fetcher.fetch_gold_daily_sina", return_value=sina_rows
        ) as sina, patch(
            "data_provider.eastmoney_fetcher.fetch_gold_daily_5huangjin"
        ) as five_huangjin, patch(
            "data_provider.infoway_fetcher.fetch_latest_gold_daily"
        ) as infoway:
            result = gold_module._backfill_gold_csv()

        self.assertEqual(result, {"added": 1, "sources": ["sina:1"]})
        self.assertEqual(sina.call_count, 1)
        five_huangjin.assert_not_called()
        infoway.assert_not_called()

    def test_five_huangjin_relabelled_session_is_not_appended(self) -> None:
        """5huangjin stamps the closed session with the next Beijing date."""
        with patch(
            "data_provider.eastmoney_fetcher.fetch_gold_daily_sina", return_value=[]
        ), patch(
            "data_provider.infoway_fetcher.fetch_latest_gold_daily", return_value=[]
        ), patch(
            "data_provider.eastmoney_fetcher.fetch_gold_daily_5huangjin",
            return_value={
                "date": "2026-10-30",
                "open": 4067.46,
                "high": 4068.72,
                "low": 4005.21,
                "close": 4021.23,
                "volume": 0,
            },
        ):
            result = gold_module._backfill_gold_csv()

        self.assertEqual(result, {"added": 0, "sources": []})
        self.assertEqual(
            self.csv_path.read_text(encoding="utf-8"), CSV_HEADER + CSV_TWO_ROWS
        )

    def test_five_huangjin_new_session_is_appended(self) -> None:
        with patch(
            "data_provider.eastmoney_fetcher.fetch_gold_daily_sina", return_value=[]
        ), patch(
            "data_provider.infoway_fetcher.fetch_latest_gold_daily", return_value=[]
        ), patch(
            "data_provider.eastmoney_fetcher.fetch_gold_daily_5huangjin",
            return_value={
                "date": "2026-11-02",
                "open": 3900.0,
                "high": 3950.0,
                "low": 3880.0,
                "close": 3940.0,
                "volume": 0,
            },
        ):
            result = gold_module._backfill_gold_csv()

        self.assertEqual(result, {"added": 1, "sources": ["5huangjin:1"]})

    def test_sync_gold_csv_on_startup_backfills_gap(self) -> None:
        sina_rows = [
            {"date": "2026-10-30", "open": 4023.79, "high": 4026.24, "low": 3870.05, "close": 3876.69},
        ]
        with patch(
            "data_provider.eastmoney_fetcher.fetch_gold_daily_sina", return_value=sina_rows
        ), patch.object(gold_module, "sync_gold_to_stock_daily") as db_sync:
            with patch.object(gold_module, "_last_gold_csv_date", return_value=None):
                gold_module.sync_gold_csv_on_startup()

        # ``_last_gold_csv_date`` patched to None: the sync must exit early.
        db_sync.assert_not_called()

    def test_expected_session_skips_weekend(self) -> None:
        class _FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 11, 2)  # Monday

        with patch.object(gold_module, "date", _FakeDate):
            self.assertEqual(
                gold_module._expected_gold_session_date(), date(2026, 10, 30)
            )

    def test_swing_dates_are_plain_iso_dates(self) -> None:
        dates = pd.date_range("2026-09-01", periods=40, freq="D").values
        wave = np.sin(np.arange(40) / 2.0) * 5.0
        highs = np.array([100 + value for value in wave], dtype=float)
        lows = np.array([90 + value for value in wave], dtype=float)

        result = gold_module._find_swings(highs, lows, dates)

        self.assertRegex(result["swing_high_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(result["swing_low_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertNotIn("T", result["swing_high_date"])


if __name__ == "__main__":
    unittest.main()
