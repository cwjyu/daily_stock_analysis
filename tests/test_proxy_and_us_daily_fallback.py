# -*- coding: utf-8 -*-
"""代理桥接与免密钥美股日线兜底的单元测试。"""

import os
import unittest
from unittest.mock import patch

import pandas as pd

from data_provider import proxy_support, us_daily_fallback


class TestProxyNormalization(unittest.TestCase):
    def test_normalize_host_port(self):
        self.assertEqual(
            proxy_support.normalize_proxy_url("127.0.0.1:7897"),
            "http://127.0.0.1:7897",
        )

    def test_normalize_keeps_scheme(self):
        self.assertEqual(
            proxy_support.normalize_proxy_url("http://127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )

    def test_normalize_wininet_list(self):
        self.assertEqual(
            proxy_support.normalize_proxy_url("http=10.0.0.1:80;https=10.0.0.1:443"),
            "http://10.0.0.1:443",
        )

    def test_normalize_rejects_blank(self):
        self.assertIsNone(proxy_support.normalize_proxy_url("   "))


class TestProxyResolution(unittest.TestCase):
    def setUp(self):
        proxy_support.reset_proxy_state()

    def tearDown(self):
        proxy_support.reset_proxy_state()

    def test_env_proxy_wins(self):
        with patch.dict(
            "os.environ",
            {"YFINANCE_PROXY": "http://127.0.0.1:7897", "HTTPS_PROXY": "http://10.0.0.1:1"},
            clear=False,
        ):
            self.assertEqual(proxy_support.resolve_proxy(), "http://127.0.0.1:7897")

    def test_dead_system_proxy_is_ignored(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(
            proxy_support, "_read_windows_proxy", return_value="http://127.0.0.1:65000"
        ), patch.object(proxy_support, "_endpoint_is_reachable", return_value=False):
            self.assertIsNone(proxy_support.resolve_proxy())

    def test_live_system_proxy_is_used(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(
            proxy_support, "_read_windows_proxy", return_value="http://127.0.0.1:7897"
        ), patch.object(proxy_support, "_endpoint_is_reachable", return_value=True):
            self.assertEqual(proxy_support.resolve_proxy(), "http://127.0.0.1:7897")

    def test_autodetect_can_be_disabled(self):
        with patch.dict(
            "os.environ", {"DISABLE_SYSTEM_PROXY_AUTODETECT": "true"}, clear=True
        ), patch.object(
            proxy_support, "_read_windows_proxy", return_value="http://127.0.0.1:7897"
        ):
            self.assertIsNone(proxy_support.resolve_proxy())

    def test_apply_exports_proxy_env_and_is_idempotent(self):
        with patch.dict("os.environ", {"YFINANCE_PROXY": "http://127.0.0.1:7897"}, clear=True):
            proxy_support.apply_yfinance_proxy()
            proxy_support.apply_yfinance_proxy()
            self.assertEqual(os.environ.get("HTTPS_PROXY"), "http://127.0.0.1:7897")
            self.assertEqual(os.environ.get("HTTP_PROXY"), "http://127.0.0.1:7897")

    def test_no_proxy_leaves_environment_untouched(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(
            proxy_support, "_read_windows_proxy", return_value=None
        ):
            proxy_support.apply_yfinance_proxy()
            self.assertNotIn("HTTPS_PROXY", os.environ)


class TestUsDailyFallbackParsers(unittest.TestCase):
    def test_eastmoney_kline_parsing(self):
        payload = (
            '{"rc":0,"data":{"code":"INTC","klines":['
            '"2026-10-29,92.965,92.070,93.500,90.838,134168717,11476083456.000,2.79",'
            '"2026-10-30,68.360,65.760,68.770,63.200,455599424,29818662912.000,6.05"]}}'
        )
        with patch.object(us_daily_fallback, "_http_get", return_value=payload):
            frame = us_daily_fallback.fetch_us_daily_eastmoney("INTC", days=30)
        self.assertIsNotNone(frame)
        self.assertEqual(list(frame.columns)[:8], us_daily_fallback._STANDARD_COLUMNS)
        last = frame.iloc[-1]
        self.assertEqual(str(last["date"])[:10], "2026-10-30")
        self.assertAlmostEqual(last["open"], 68.36)
        self.assertAlmostEqual(last["close"], 65.76)
        self.assertAlmostEqual(last["high"], 68.77)
        self.assertAlmostEqual(last["low"], 63.20)
        self.assertAlmostEqual(last["volume"], 455599424.0)
        self.assertAlmostEqual(last["pct_chg"], (65.76 / 92.07 - 1) * 100, places=5)

    def test_tencent_kline_parsing(self):
        payload = (
            '{"code":0,"data":{"usINTC":{"day":['
            '["2026-10-29","92.97","92.07","93.50","90.84","134168717",{}],'
            '["2026-10-30","68.36","65.76","68.77","63.20","455599430",{}]]}}}'
        )
        with patch.object(us_daily_fallback, "_http_get", return_value=payload):
            frame = us_daily_fallback.fetch_us_daily_tencent("INTC", days=30)
        self.assertIsNotNone(frame)
        self.assertEqual(len(frame), 2)
        self.assertAlmostEqual(frame.iloc[-1]["close"], 65.76)

    def test_sina_kline_parsing(self):
        payload = (
            'var _INTC=([{"d":"2026-10-29","o":"92.97","h":"93.50","l":"90.84",'
            '"c":"92.07","v":"134168717","a":"11476083456"},'
            '{"d":"2026-10-30","o":"68.36","h":"68.77","l":"63.20","c":"65.76",'
            '"v":"455599233","a":"29814800000"}])'
        )
        with patch.object(us_daily_fallback, "_http_get", return_value=payload):
            frame = us_daily_fallback.fetch_us_daily_sina("INTC", days=30)
        self.assertIsNotNone(frame)
        self.assertEqual(len(frame), 2)
        last = frame.iloc[-1]
        self.assertAlmostEqual(last["close"], 65.76)
        self.assertAlmostEqual(last["amount"], 29814800000.0)

    def test_invalid_symbol_returns_none(self):
        self.assertIsNone(us_daily_fallback.fetch_us_daily_sina("600519.SS", days=30))
        self.assertIsNone(us_daily_fallback.fetch_us_daily_eastmoney("", days=30))


class TestUsDailyFallbackOrder(unittest.TestCase):
    def _frame(self, rows, close=1.0):
        return pd.DataFrame(
            {
                "date": pd.date_range("2026-10-01", periods=rows, freq="D"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1.0,
                "amount": 1.0,
                "pct_chg": 0.0,
                "code": "INTC",
            }
        )

    def test_sina_preferred_and_short_frames_skipped(self):
        calls = []

        def fake_sina(symbol, days=120):
            calls.append("sina")
            return self._frame(30)

        def fake_eastmoney(symbol, days=120):
            calls.append("eastmoney")
            return self._frame(30)

        with patch.dict(
            us_daily_fallback._FETCHERS,
            {"sina": fake_sina, "eastmoney": fake_eastmoney, "tencent": fake_eastmoney},
            clear=False,
        ):
            frame, source = us_daily_fallback.fetch_us_daily("INTC", days=30)
        self.assertEqual(source, "us_daily_sina")
        self.assertEqual(calls, ["sina"])

    def test_shallow_frame_is_last_resort(self):
        with patch.dict(
            us_daily_fallback._FETCHERS,
            {
                "sina": lambda symbol, days=120: None,
                "eastmoney": lambda symbol, days=120: None,
                "tencent": lambda symbol, days=120: self._frame(2),
            },
            clear=False,
        ):
            frame, source = us_daily_fallback.fetch_us_daily("INTC", days=30)
        self.assertEqual(source, "us_daily_tencent")
        self.assertEqual(len(frame), 2)


if __name__ == "__main__":
    unittest.main()
