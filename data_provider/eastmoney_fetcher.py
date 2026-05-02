# -*- coding: utf-8 -*-
"""
East Money (东方财富) scraper for real-time gold (XAUUSD) price.

API: https://push2.eastmoney.com/api/qt/stock/get
No API key required.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

import requests

logger = logging.getLogger(__name__)

EM_API = "https://push2delay.eastmoney.com/api/qt/stock/get"

_cache: Dict[str, dict] = {}
_cache_ts: float = 0

# Beijing timezone offset
_BJT = timezone(timedelta(hours=8))


def _cache_ttl() -> float:
    """Return cache TTL: 10s during market hours, 5min during off-hours."""
    now = datetime.now(_BJT)
    dow = now.weekday()
    hour = now.hour
    # Off: Sat 06:00 – Mon 06:00 Beijing time
    if dow == 5 and hour >= 6:
        return 300
    if dow == 6:
        return 300
    if dow == 0 and hour < 6:
        return 300
    return 10


def _fetch_gold_5huangjin() -> Optional[Dict]:
    """Fetch gold prices from 5huangjin.com data API (jin.js).

    The API returns JS variable assignments:
      hq_str_hf_XAU  = London gold spot (USD/oz)
      hq_str_gds_AUTD = Shanghai Gold Au(T+D) (RMB/g)
    """
    import re

    try:
        resp = requests.get(
            "https://www.5huangjin.com/data/jin.js",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
        # The JS file is GB2312 or UTF-8; try explicit encoding
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = "gb2312"
        text = resp.text
    except Exception as e:
        logger.debug(f"5huangjin jin.js fetch failed: {e}")
        return None

    def _parse_quote(var_name: str) -> Optional[list]:
        """Extract and split a hq_str_* variable from the JS text."""
        m = re.search(
            r'var\s+' + re.escape(var_name) + r'\s*=\s*"([^"]*)"',
            text,
        )
        if not m:
            return None
        return m.group(1).split(",")

    # --- International gold (USD/oz) ---
    xau = _parse_quote("hq_str_hf_XAU")
    if not xau or len(xau) < 9:
        logger.debug("5huangjin: hq_str_hf_XAU not found or too short")
        return None

    try:
        usd_price = float(xau[0])
        prev_close = float(xau[7]) if xau[7] else 0
        change = round(usd_price - prev_close, 2)
        change_pct = round(change / prev_close * 100, 3) if prev_close else 0
        rt_time = datetime.now(_BJT).strftime("%H:%M:%S")
    except (ValueError, IndexError, ZeroDivisionError):
        logger.debug("5huangjin: parse hq_str_hf_XAU failed", exc_info=True)
        return None

    if usd_price <= 0:
        return None

    logger.info(f"5huangjin XAU USD/oz = {usd_price}")

    result: Dict = {
        "price": usd_price,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "time": rt_time,
    }

    # --- Domestic gold (RMB/g) ---
    autd = _parse_quote("hq_str_gds_AUTD")
    if autd and len(autd) >= 1:
        try:
            rmb_gram = float(autd[0])
            if rmb_gram > 0:
                result["rmb_per_gram"] = round(rmb_gram, 1)
                logger.info(f"5huangjin AUTD RMB/g = {rmb_gram}")
        except (ValueError, IndexError):
            pass

    return result


def _fetch_gold_metals_live() -> Optional[Dict]:
    """Fetch gold spot price from api.metals.live (free, no key needed).

    Returns dict with keys: price, prev_close, change, change_pct, time
    or None on failure.
    """
    try:
        resp = requests.get(
            "https://api.metals.live/v1/spot/gold",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug(f"metals.live fetch failed: {e}")
        return None

    try:
        # Response is a list with one element: [{"timestamp":"...", "price":4376.50, ...}]
        if isinstance(data, list) and len(data) > 0:
            item = data[0]
        elif isinstance(data, dict):
            item = data
        else:
            return None

        price = float(item.get("price", 0))
        if price <= 0:
            return None

        # metals.live only gives current price, no prev_close/change
        # We'll compute change from previous cached value
        prev_close = 0.0
        change = 0.0
        change_pct = 0.0
        rt_time = datetime.now(_BJT).strftime("%H:%M:%S")

        return {
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "time": rt_time,
        }
    except (ValueError, TypeError, KeyError):
        logger.debug("metals.live parse failed", exc_info=True)
        return None


def _fetch_gold_eastmoney() -> Optional[Dict]:
    """Fallback: fetch gold price from East Money 122.XAU."""
    try:
        resp = requests.get(
            EM_API,
            params={
                "secid": "122.AU0",  # spot precious metals continuous
                "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f107,f116,f117,f162,f167,f168,f169,f170,f171",
            },
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.debug(f"East Money gold fetch failed: {e}")
        return None

    data = body.get("data")
    if not data:
        return None

    try:
        price = float(data.get("f43", 0)) / 100
        prev_close = float(data.get("f60", 0)) / 100 if data.get("f60") else None
        change = float(data.get("f169", 0)) / 100
        change_pct = float(data.get("f170", 0)) / 100
        rt_time = datetime.now(_BJT).strftime("%H:%M:%S")

        if price <= 0:
            return None

        return {
            "price": price,
            "prev_close": prev_close or 0,
            "change": change,
            "change_pct": change_pct,
            "time": rt_time,
        }
    except (ValueError, TypeError, ZeroDivisionError):
        logger.debug("East Money gold parse failed", exc_info=True)
        return None


def fetch_gold_realtime() -> Optional[Dict]:
    """
    Fetch latest gold spot price (XAUUSD).
    Primary: api.metals.live  |  Fallback: East Money

    Returns dict with keys: price, prev_close, change, change_pct, time
    """
    global _cache, _cache_ts

    now = time.time()
    ttl = _cache_ttl()

    if _cache and (now - _cache_ts) < ttl:
        cached = _cache.get("rt")
        if cached:
            return cached

    # During off-hours, keep returning stale cache
    if ttl > 10 and _cache:
        return _cache.get("rt")

    # Try 5huangjin first (has both USD/oz and RMB/g on separate pages)
    result = _fetch_gold_5huangjin()

    # Fallback to metals.live (accurate spot, but needs USDCNY for RMB/g)
    if result is None:
        result = _fetch_gold_metals_live()

    # Fallback to East Money (try 122.AU0 first, then 122.XAU)
    if result is None:
        result = _fetch_gold_eastmoney()
    if result is None:
        # Second fallback: original secid that is known to return data
        try:
            resp = requests.get(
                EM_API,
                params={
                    "secid": "122.XAU",
                    "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f107,f116,f117,f162,f167,f168,f169,f170,f171",
                },
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                proxies={"http": None, "https": None},
            )
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data") or {}
            price = float(data.get("f43", 0)) / 100
            if price > 0:
                result = {
                    "price": price,
                    "prev_close": float(data.get("f60", 0)) / 100 if data.get("f60") else 0,
                    "change": float(data.get("f169", 0)) / 100,
                    "change_pct": float(data.get("f170", 0)) / 100,
                    "time": datetime.now(_BJT).strftime("%H:%M:%S"),
                }
        except Exception:
            logger.debug("East Money 122.XAU fallback also failed", exc_info=True)

    if result is None:
        return None

    # Compute change from previous cached close if metals.live (no prev_close)
    if result["prev_close"] == 0:
        prev_cached = _cache.get("rt")
        if prev_cached and prev_cached.get("price"):
            last_price = prev_cached["price"]
            result["prev_close"] = last_price
            result["change"] = round(result["price"] - last_price, 2)
            if last_price > 0:
                result["change_pct"] = round((result["price"] - last_price) / last_price * 100, 2)

    _cache["rt"] = result
    _cache_ts = now
    return result


# Independent cache timestamp for USDCNY (24h TTL)
_usdcny_cache_ts: float = 0
_USDCNY_CACHE_TTL = 86400  # fetch once per day


def fetch_usdcny_rate() -> Optional[Dict]:
    """
    Fetch USD/CNY exchange rate from East Money (cached 24h).

    Returns dict with keys: rate, prev_close, change, change_pct, time
    """
    global _cache, _usdcny_cache_ts

    now = time.time()
    cache_key = "usdcny"

    # Use 24h TTL regardless of market hours
    if _cache and (now - _usdcny_cache_ts) < _USDCNY_CACHE_TTL:
        cached = _cache.get(cache_key)
        if cached:
            return cached

    try:
        resp = requests.get(
            EM_API,
            params={
                "secid": "133.USDCNY",
                "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f107,f116,f117,f162,f167,f168,f169,f170,f171",
            },
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.warning(f"East Money USDCNY fetch failed: {e}")
        return None

    data = body.get("data")
    if not data:
        return None

    try:
        rate = float(data.get("f43", 0)) / 10000
        prev_close = float(data.get("f60", 0)) / 10000
        change = float(data.get("f169", 0)) / 10000
        change_pct = float(data.get("f170", 0)) / 100
        rt_time = datetime.now(_BJT).strftime("%H:%M:%S")

        if rate <= 0:
            return None

        result = {
            "rate": rate,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "time": rt_time,
        }
        _cache[cache_key] = result
        _usdcny_cache_ts = now
        return result
    except (ValueError, TypeError, ZeroDivisionError):
        logger.debug("East Money USDCNY parse failed", exc_info=True)
        return None
