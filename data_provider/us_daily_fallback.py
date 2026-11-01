# -*- coding: utf-8 -*-
"""Key-less fallbacks for US daily bars.

Yahoo Finance is the only daily-bar source for US stocks inside
``DataFetcherManager``. When the machine cannot reach Yahoo (rate limited IPs,
blocked network, a proxy that libcurl ignores) every US analysis degrades to
"数据受限" even though the public quote sites below are perfectly reachable.

The three endpoints used here need no API key and no account:

* Eastmoney  ``push2his.eastmoney.com`` ``kline/get`` with ``secid=105.INTC``
* Tencent    ``web.ifzq.gtimg.cn`` ``usfqkline/get`` with ``param=usINTC,day``
* Sina       ``US_MinKService.getDailyK`` JSONP (needs a finance.sina.com.cn referer)

All of them are requested through :mod:`urllib.request`, which honours the
``HTTP_PROXY`` / ``HTTPS_PROXY`` environment variables, so a local proxy that is
exported for the process is picked up automatically.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# Sina first: it returns the full history in one request, so the frame is
# usually deep enough for MA20/MA50. Eastmoney and Tencent are secondary.
US_DAILY_FALLBACK_SOURCES = ("sina", "eastmoney", "tencent")
_MIN_USEFUL_ROWS = 5
_DEFAULT_TIMEOUT_SECONDS = 12
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EASTMONEY_FIELDS1 = "f1,f2,f3,f4,f5,f6"
_EASTMONEY_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58"
# 105 = NASDAQ, 106 = NYSE, 107 = AMEX
_EASTMONEY_MARKET_PREFIXES = ("105", "106", "107")

_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get"
_SINA_KLINE_URL = (
    "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/"
    "var%20_{symbol}=/US_MinKService.getDailyK"
)

_STANDARD_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]


def _http_get(url: str, *, headers: Optional[dict] = None, timeout: int = _DEFAULT_TIMEOUT_SECONDS) -> str:
    request_headers = {"User-Agent": _USER_AGENT}
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "ignore")


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        text = str(value).strip()
        if not text or text in {"-", "None", "null"}:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _rows_to_frame(rows: Iterable[dict], stock_code: str) -> Optional[pd.DataFrame]:
    """Build the standard daily-bar frame from ``{date, open, high, low, close, volume, amount}``."""
    records = [row for row in rows if row.get("date")]
    if not records:
        return None
    frame = pd.DataFrame(records)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    if frame.empty:
        return None
    for column in ("open", "high", "low", "close", "volume", "amount"):
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    frame["pct_chg"] = frame["close"].pct_change().fillna(0.0) * 100
    frame["code"] = stock_code
    return frame.reset_index(drop=True)[_STANDARD_COLUMNS + ["code"]]


def _normalize_symbol(stock_code: str) -> str:
    return str(stock_code or "").strip().upper()


def fetch_us_daily_eastmoney(stock_code: str, days: int = 120) -> Optional[pd.DataFrame]:
    """Fetch US daily bars from Eastmoney (no key required)."""
    symbol = _normalize_symbol(stock_code)
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
        return None
    limit = max(int(days or 0), 5)
    for prefix in _EASTMONEY_MARKET_PREFIXES:
        url = (
            f"{_EASTMONEY_KLINE_URL}?secid={prefix}.{symbol}"
            f"&fields1={_EASTMONEY_FIELDS1}&fields2={_EASTMONEY_FIELDS2}"
            f"&klt=101&fqt=1&end=20500101&lmt={limit}"
        )
        try:
            payload = json.loads(
                _http_get(url, headers={"Referer": "https://quote.eastmoney.com/"})
            )
        except Exception as exc:  # 网络异常/限流/断开都应继续尝试下一个市场前缀
            logger.debug("Eastmoney US daily bars failed for %s (%s): %s", symbol, prefix, exc)
            continue
        klines = ((payload or {}).get("data") or {}).get("klines") or []
        rows: List[dict] = []
        for item in klines:
            parts = str(item).split(",")
            if len(parts) < 7:
                continue
            rows.append(
                {
                    "date": parts[0],
                    "open": _to_float(parts[1]),
                    "close": _to_float(parts[2]),
                    "high": _to_float(parts[3]),
                    "low": _to_float(parts[4]),
                    "volume": _to_float(parts[5]),
                    "amount": _to_float(parts[6]),
                }
            )
        frame = _rows_to_frame(rows, symbol)
        if frame is not None:
            return frame.tail(limit).reset_index(drop=True)
    return None


def fetch_us_daily_tencent(stock_code: str, days: int = 120) -> Optional[pd.DataFrame]:
    """Fetch US daily bars from Tencent (no key required)."""
    symbol = _normalize_symbol(stock_code)
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
        return None
    limit = max(int(days or 0), 5)
    url = f"{_TENCENT_KLINE_URL}?param=us{symbol},day,,,{limit},qfq"
    try:
        payload = json.loads(_http_get(url))
    except Exception as exc:
        logger.debug("Tencent US daily bars failed for %s: %s", symbol, exc)
        return None

    node = ((payload or {}).get("data") or {}).get(f"us{symbol}") or {}
    raw_rows = node.get("day") or node.get("qfqday") or []
    rows: List[dict] = []
    for item in raw_rows:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            continue
        rows.append(
            {
                "date": item[0],
                "open": _to_float(item[1]),
                "close": _to_float(item[2]),
                "high": _to_float(item[3]),
                "low": _to_float(item[4]),
                "volume": _to_float(item[5]),
            }
        )
    frame = _rows_to_frame(rows, symbol)
    if frame is None:
        return None
    return frame.tail(limit).reset_index(drop=True)


def fetch_us_daily_sina(stock_code: str, days: int = 120) -> Optional[pd.DataFrame]:
    """Fetch US daily bars from Sina's JSONP endpoint (no key required)."""
    symbol = _normalize_symbol(stock_code)
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
        return None
    limit = max(int(days or 0), 5)
    url = f"{_SINA_KLINE_URL.format(symbol=symbol)}?symbol={symbol}&___qn=3"
    try:
        text = _http_get(url, headers={"Referer": "https://finance.sina.com.cn"})
    except Exception as exc:
        logger.debug("Sina US daily bars failed for %s: %s", symbol, exc)
        return None

    start = text.find("([")
    end = text.rfind(")")
    if start < 0 or end < 0:
        return None
    try:
        payload = json.loads(text[start + 1:end])
    except json.JSONDecodeError:
        logger.debug("Sina US daily bars returned an unparsable payload for %s", symbol)
        return None

    rows: List[dict] = []
    for item in payload or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "date": item.get("d"),
                "open": _to_float(item.get("o")),
                "high": _to_float(item.get("h")),
                "low": _to_float(item.get("l")),
                "close": _to_float(item.get("c")),
                "volume": _to_float(item.get("v")),
                "amount": _to_float(item.get("a")),
            }
        )
    frame = _rows_to_frame(rows, symbol)
    if frame is None:
        return None
    return frame.tail(limit).reset_index(drop=True)


_FETCHERS = {
    "eastmoney": fetch_us_daily_eastmoney,
    "tencent": fetch_us_daily_tencent,
    "sina": fetch_us_daily_sina,
}


def fetch_us_daily(
    stock_code: str,
    days: int = 120,
    sources: Optional[Sequence[str]] = None,
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Try the key-less US daily sources in order.

    Returns:
        ``(dataframe, source_name)`` on success, ``(None, None)`` when every
        source failed.
    """
    best_frame: Optional[pd.DataFrame] = None
    best_source: Optional[str] = None
    for name in sources or US_DAILY_FALLBACK_SOURCES:
        fetcher = _FETCHERS.get(name)
        if fetcher is None:
            continue
        try:
            frame = fetcher(stock_code, days=days)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("US daily fallback source %s raised for %s: %s", name, stock_code, exc)
            continue
        if frame is None or frame.empty:
            continue
        source_name = f"us_daily_{name}"
        if len(frame) >= _MIN_USEFUL_ROWS:
            logger.info(
                "美股 %s 日线使用免密钥兜底源 %s: rows=%s", stock_code, name, len(frame)
            )
            return frame, source_name
        # Too shallow for trend work, but still better than nothing.
        if best_frame is None or len(frame) > len(best_frame):
            best_frame, best_source = frame, source_name
    if best_frame is not None:
        logger.info(
            "美股 %s 日线使用免密钥兜底源 %s（行数偏少）: rows=%s",
            stock_code,
            best_source,
            len(best_frame),
        )
    return best_frame, best_source
