# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

from data_provider.yfinance_fetcher import YfinanceFetcher
from data_provider.realtime_types import RealtimeSource

try:
    import yfinance  # noqa: F401

    HAS_YFINANCE = True
except Exception:
    HAS_YFINANCE = False


class TestStooqFallback(unittest.TestCase):
    def setUp(self):
        self.fetcher = YfinanceFetcher()

    @patch('data_provider.yfinance_fetcher.urlopen')
    def test_stooq_success_logic(self, mock_urlopen):
        """测试 Stooq 正常抓取与解析逻辑"""
        # 模拟 Stooq 返回的 CSV 格式数据（实时 + 日线历史）
        mock_realtime_payload = (
            "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
            "AAPL.US,2026-03-10,22:00:00,180.50,185.20,179.80,184.45,50000000\n"
        )
        mock_history_payload = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-03-09,178.00,181.00,177.00,179.00,48000000\n"
            "2026-03-10,180.50,185.20,179.80,184.45,50000000\n"
        )

        mock_realtime_response = MagicMock()
        mock_realtime_response.read.return_value = mock_realtime_payload.encode('utf-8')
        mock_realtime_response.__enter__.return_value = mock_realtime_response

        mock_history_response = MagicMock()
        mock_history_response.read.return_value = mock_history_payload.encode('utf-8')
        mock_history_response.__enter__.return_value = mock_history_response

        mock_urlopen.side_effect = [mock_realtime_response, mock_history_response]

        quote = self.fetcher._get_us_stock_quote_from_stooq("AAPL")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.code, "AAPL")
        self.assertEqual(quote.price, 184.45)
        self.assertEqual(quote.open_price, 180.50)
        self.assertEqual(quote.high, 185.20)
        self.assertEqual(quote.low, 179.80)
        self.assertEqual(quote.volume, 50000000)
        self.assertEqual(quote.source, RealtimeSource.STOOQ)
        self.assertEqual(quote.pre_close, 179.00)
        self.assertAlmostEqual(quote.change_amount, 5.45, places=2)
        self.assertAlmostEqual(quote.change_pct, 3.04, places=2)
        self.assertAlmostEqual(quote.amplitude, 3.02, places=2)

    @unittest.skipUnless(HAS_YFINANCE, "yfinance is required for this test")
    @patch('yfinance.Ticker')
    def test_fetcher_integration_with_fallback(self, mock_ticker_class):
        """测试 yfinance 失败后自动触发免密钥兜底逻辑"""
        # 1. 模拟 yfinance 完全失效
        mock_ticker = MagicMock()
        # 模拟 fast_info 属性访问抛出异常
        type(mock_ticker).fast_info = PropertyMock(side_effect=Exception("API Error"))
        # 模拟 history 返回空
        mock_ticker.history.return_value = MagicMock(empty=True)
        mock_ticker_class.return_value = mock_ticker

        # 2. 模拟免密钥兜底源成功返回
        with patch.object(self.fetcher, '_get_us_stock_quote_from_free_sources') as mock_fallback:
            mock_fallback.return_value = MagicMock(code="NVDA", price=900.0)

            quote = self.fetcher.get_realtime_quote("NVDA")

            self.assertIsNotNone(quote)
            self.assertEqual(quote.price, 900.0)
            mock_fallback.assert_called_once_with("NVDA")


class TestSinaFallback(unittest.TestCase):
    def setUp(self):
        self.fetcher = YfinanceFetcher()

    @patch('data_provider.yfinance_fetcher.urlopen')
    def test_sina_success_logic(self, mock_urlopen):
        """测试新浪美股兜底行情的抓取与解析"""
        mock_payload = (
            'var hq_str_gb_intc="英特尔,65.7600,-28.58,2026-10-31 09:30:11,-26.3100,'
            '68.3600,68.7700,63.2001,142.3500,32.8900,455599430,99648740,347614281700,'
            '-0.62,--,0.00,0.00,0.00,0.00,5286105257,71,64.8817,-1.34,-0.88,'
            'Oct 30 07:59PM EDT,Oct 30 04:00PM EDT,92.0700,11344463,1,2026,'
            '29814845189.0000,92.0700,64.4000,741979501.2907,65.8400,92.0700";'
        )
        mock_response = MagicMock()
        mock_response.read.return_value = mock_payload.encode('gbk')
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        quote = self.fetcher._get_us_stock_quote_from_sina("INTC")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.code, "INTC")
        self.assertEqual(quote.name, "英特尔")
        self.assertEqual(quote.price, 65.76)
        self.assertEqual(quote.pre_close, 92.07)
        self.assertEqual(quote.open_price, 68.36)
        self.assertEqual(quote.high, 68.77)
        self.assertEqual(quote.low, 63.2001)
        self.assertEqual(quote.volume, 455599430)
        self.assertEqual(quote.source, RealtimeSource.SINA)
        self.assertEqual(quote.market, "us")
        self.assertEqual(quote.currency, "USD")
        self.assertAlmostEqual(quote.change_pct, -28.58, places=2)
        self.assertAlmostEqual(quote.total_mv, 347614281700.0)
        self.assertEqual(quote.high_52w, 142.35)
        self.assertEqual(quote.low_52w, 32.89)

    def test_sina_parse_rejects_invalid_payload(self):
        """非法返回（空数据/字段不足）时返回 None，由调用方继续降级"""
        self.assertIsNone(self.fetcher._parse_sina_us_quote("INTC", "var hq_str_gb_intc=\"\";"))
        self.assertIsNone(self.fetcher._parse_sina_us_quote("INTC", "no-quote-here"))

    @patch.object(YfinanceFetcher, '_get_us_stock_quote_from_sina')
    def test_free_sources_falls_back_to_stooq(self, mock_sina):
        """新浪失败时继续尝试 Stooq"""
        mock_sina.return_value = None
        with patch.object(self.fetcher, '_get_us_stock_quote_from_stooq') as mock_stooq:
            mock_stooq.return_value = MagicMock(code="INTC", price=1.0)
            quote = self.fetcher._get_us_stock_quote_from_free_sources("INTC")
            self.assertEqual(quote.price, 1.0)
            mock_stooq.assert_called_once_with("INTC")


if __name__ == '__main__':
    unittest.main()
