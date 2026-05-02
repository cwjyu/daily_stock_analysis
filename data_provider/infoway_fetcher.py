# -*- coding: utf-8 -*-
"""
Infoway API fetcher for gold (XAUUSD) real-time and historical K-line data.

Docs: https://docs.infoway.io/rest-api/http-endpoints/get-candles

Env vars:
    INFOWAY_API_KEY — API key from https://www.infoway.io
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import requests

logger = logging.getLogger(__name__)

INFOWAY_BASE = "https://data.infoway.io/common"
INFOWAY_API_KEY = os.getenv("INFOWAY_API_KEY", "")

# K-line type mapping
KL_TYPE_MAP = {
    "1m": 1, "5m": 2, "15m": 3, "30m": 4,
    "1h": 5, "2h": 6, "4h": 7,
    "1d": 8, "1w": 9, "1M": 10,
}


def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "apiKey": INFOWAY_API_KEY,
    }


def fetch_gold_candles(
    kline_type: str = "1d",
    count: int = 100,
    timestamp: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch XAUUSD K-line data from Infoway.

    Args:
        kline_type: K-line period — '1m','5m','15m','30m','1h','2h','4h','1d','1w','1M'
        count: Number of candles (max 500 for single symbol)
        timestamp: Unix seconds — if set, returns candles before this time (for historical)

    Returns:
        List of candle dicts with keys: t, o, h, l, c, v, pc, pca, or None on failure
    """
    if not INFOWAY_API_KEY:
        logger.warning("INFOWAY_API_KEY not set, skipping Infoway fetch")
        return None

    ktype = KL_TYPE_MAP.get(kline_type, 8)
    url = f"{INFOWAY_BASE}/batch_kline/{ktype}/{count}/XAUUSD"

    params: Dict[str, Any] = {}
    if timestamp is not None:
        params["timestamp"] = timestamp

    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.warning(f"Infoway fetch failed: {e}")
        return None

    if body.get("ret") != 200:
        logger.warning(f"Infoway returned ret={body.get('ret')}, msg={body.get('msg')}")
        return None

    data = body.get("data", [])
    if not data:
        return None

    # Return the first symbol's candle list
    symbol_data = data[0]
    candles = symbol_data.get("respList", [])
    return candles


def fetch_latest_gold_daily(days: int = 10) -> Optional[List[Dict[str, Any]]]:
    """
    Convenience: fetch the most recent daily XAUUSD candles.

    Returns list of dicts with normalized keys:
        date, open, high, low, close, volume, change_pct
    """
    raw = fetch_gold_candles(kline_type="1d", count=days)
    if raw is None:
        return None

    result = []
    for c in raw:
        try:
            ts = int(c.get("t", 0))
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            result.append({
                "date": dt,
                "open": float(c.get("o", 0)),
                "high": float(c.get("h", 0)),
                "low": float(c.get("l", 0)),
                "close": float(c.get("c", 0)),
                "volume": float(c.get("v", 0)),
                "change_pct": float(str(c.get("pc", "0%")).replace("%", "")) if c.get("pc") else None,
            })
        except (ValueError, TypeError):
            logger.debug(f"Skipping malformed Infoway candle: {c}")
            continue

    return result


def fetch_gold_realtime() -> Optional[Dict[str, Any]]:
    """
    Fetch the latest 1-minute XAUUSD candle from Infoway for real-time price.

    Returns dict with keys: price, prev_close, change, change_pct, time
    """
    raw = fetch_gold_candles(kline_type="1m", count=2)
    if not raw:
        return None

    try:
        latest = raw[-1]
        price = float(latest.get("c", 0))
        ts = int(latest.get("t", 0))
        rt_time = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")
        pc = float(str(latest.get("pc", "0%")).replace("%", "")) if latest.get("pc") else 0.0
        prev_close = round(price / (1 + pc / 100), 2) if pc else price
        return {
            "price": price,
            "prev_close": prev_close,
            "change": round(price - prev_close, 2),
            "change_pct": round(pc, 2),
            "time": rt_time,
        }
    except (ValueError, TypeError, IndexError, ZeroDivisionError):
        logger.debug("Failed to parse Infoway 1m candle", exc_info=True)
        return None
