# -*- coding: utf-8 -*-
"""ETF 实时行情优先走新浪单只接口，失败时回退东财全量接口。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote


class _DummyCircuitBreaker:
    def is_available(self, source: str) -> bool:
        return True

    def record_success(self, source: str) -> None:
        pass

    def record_failure(self, source: str, error=None) -> None:
        pass


def _sina_quote(stock_code: str) -> UnifiedRealtimeQuote:
    return UnifiedRealtimeQuote(
        code=stock_code,
        name="科创半导体ETF华夏",
        source=RealtimeSource.AKSHARE_SINA,
        price=0.586,
        pre_close=0.628,
        open_price=0.601,
        high=0.625,
        low=0.585,
    )


class TestEtfRealtimeRouting(unittest.TestCase):
    def test_etf_prefers_sina_single_symbol_quote(self) -> None:
        fetcher = AkshareFetcher()
        with patch(
            "data_provider.akshare_fetcher.get_realtime_circuit_breaker",
            return_value=_DummyCircuitBreaker(),
        ), patch.object(
            fetcher,
            "_get_stock_realtime_quote_sina",
            return_value=_sina_quote("588170"),
        ), patch.object(fetcher, "_get_etf_realtime_quote") as em_mock:
            quote = fetcher.get_realtime_quote("588170")

        self.assertIsNotNone(quote)
        self.assertAlmostEqual(quote.price, 0.586)
        self.assertEqual(quote.source, RealtimeSource.AKSHARE_SINA)
        em_mock.assert_not_called()

    def test_etf_falls_back_to_em_when_sina_unavailable(self) -> None:
        fetcher = AkshareFetcher()
        em_quote = UnifiedRealtimeQuote(
            code="588170",
            name="科创半导体ETF华夏",
            source=RealtimeSource.AKSHARE_EM,
            price=0.6,
        )
        with patch(
            "data_provider.akshare_fetcher.get_realtime_circuit_breaker",
            return_value=_DummyCircuitBreaker(),
        ), patch.object(
            fetcher,
            "_get_stock_realtime_quote_sina",
            return_value=None,
        ), patch.object(fetcher, "_get_etf_realtime_quote", return_value=em_quote) as em_mock:
            quote = fetcher.get_realtime_quote("588170")

        self.assertIs(quote, em_quote)
        em_mock.assert_called_once_with("588170")


if __name__ == "__main__":
    unittest.main()
