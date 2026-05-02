# -*- coding: utf-8 -*-
"""
Gold market endpoints — historical K-line with technical indicators.
"""

import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, or_, select as sa_select

from api.v1.schemas.gold import GoldKLinePoint, GoldKLineResponse, GoldAnalysisResponse, GoldNewsItem, GoldNewsResponse
from api.v1.schemas.common import ErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_DATA_PATH = Path(__file__).resolve().parent.parent.parent.parent / "strategies" / "data" / "gold_daily.csv"
_CACHE: dict = {}
_CACHE_TTL = 300  # refresh live data every 5 minutes


def sync_gold_csv_on_startup() -> None:
    """
    Check CSV for missing days and fill gaps from Infoway on startup.

    Compares the last date in gold_daily.csv to yesterday (today's candle
    may not be complete yet) and fetches any missing rows.
    """
    if not _DATA_PATH.exists():
        logger.warning("Gold CSV not found, skipping startup sync")
        return

    try:
        from data_provider.infoway_fetcher import fetch_latest_gold_daily
    except Exception:
        logger.debug("Infoway not available, skipping startup sync")
        return

    try:
        df = pd.read_csv(_DATA_PATH, parse_dates=["Date"])
        df["Date"] = pd.to_datetime(df["Date"])
        last_date = df["Date"].max().date()
        yesterday = date.today() - timedelta(days=1)
        gap_days = (yesterday - last_date).days

        if gap_days <= 0:
            logger.info(f"Gold CSV is up to date (last: {last_date})")
            return

        logger.info(f"Gold CSV has {gap_days} missing day(s) (last: {last_date}, expected: {yesterday})")

        # Fetch enough days to cover gap + buffer
        live = fetch_latest_gold_daily(days=max(gap_days + 5, 10))
        if not live:
            logger.warning("Startup sync: Infoway returned no data")
            return

        existing_dates = set(df["Date"].dt.date)
        new_rows = []
        for row in live:
            dt = datetime.strptime(row["date"], "%Y-%m-%d").date()
            if dt not in existing_dates and dt > last_date:
                new_rows.append({
                    "Date": row["date"],
                    "Open": row["open"],
                    "High": row["high"],
                    "Low": row["low"],
                    "Close": row["close"],
                    "Volume": row.get("volume", 0),
                })

        if not new_rows:
            logger.info("Startup sync: no new data to add")
            return

        df_new = pd.DataFrame(new_rows)
        df_new = df_new.sort_values("Date")
        df_merged = pd.concat([df, df_new], ignore_index=True)
        df_merged = df_merged.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
        df_merged.to_csv(_DATA_PATH, index=False)
        _CACHE.clear()

        dates_str = [r["Date"] for r in new_rows]
        logger.info(f"Startup sync: added {len(new_rows)} rows to CSV: {dates_str}")
    except Exception as e:
        logger.warning(f"Startup gold CSV sync failed: {e}")


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators on the dataframe (in-place)."""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # MAs
    df["ma5"] = close.rolling(5).mean()
    df["ma10"] = close.rolling(10).mean()
    df["ma20"] = close.rolling(20).mean()
    df["ma50"] = close.rolling(50).mean()
    df["ma200"] = close.rolling(200).mean()

    # Bollinger Bands (20,2)
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_middle"] = bb_mid
    df["bb_lower"] = bb_mid - 2 * bb_std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"] * 100

    # ADX (14)
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    up = high.diff()
    dn = (-low).diff()
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up > dn) & (up > 0)] = up
    minus_dm[(dn > up) & (dn > 0)] = dn
    atr = tr.ewm(span=27, adjust=False).mean()
    di_p = 100 * (plus_dm.ewm(span=27, adjust=False).mean() / atr)
    di_m = 100 * (minus_dm.ewm(span=27, adjust=False).mean() / atr)
    dx = (abs(di_p - di_m) / (di_p + di_m)) * 100
    df["adx"] = dx.ewm(span=27, adjust=False).mean()
    df["plus_di"] = di_p
    df["minus_di"] = di_m

    # Change %
    df["change_pct"] = (close - close.shift(1)) / close.shift(1) * 100

    return df


def _load_gold_data() -> pd.DataFrame:
    """Load gold daily data from CSV, merge with Infoway real-time data, with TTL cache."""
    cache_key = "gold_daily"
    mtime = _DATA_PATH.stat().st_mtime if _DATA_PATH.exists() else 0
    now = time.time()

    if cache_key in _CACHE:
        cached_mtime, cached_time, cached_df = _CACHE[cache_key]
        if cached_mtime == mtime and (now - cached_time) < _CACHE_TTL:
            return cached_df

    if not _DATA_PATH.exists():
        raise FileNotFoundError(f"Gold data file not found: {_DATA_PATH}")

    df = pd.read_csv(_DATA_PATH, parse_dates=["Date"])
    df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                             "Low": "low", "Close": "close", "Volume": "volume"})
    df = df.sort_values("date").reset_index(drop=True)

    # Try to merge latest data from Infoway
    try:
        from data_provider.infoway_fetcher import fetch_latest_gold_daily
        live = fetch_latest_gold_daily(days=30)
        if live:
            df_live = pd.DataFrame(live)
            df_live["date"] = pd.to_datetime(df_live["date"])
            # Upsert: remove CSV rows that overlap with live, then concat
            live_dates = set(df_live["date"].dt.date)
            df = df[~df["date"].dt.date.isin(live_dates)]
            df = pd.concat([df, df_live], ignore_index=True)
            df = df.sort_values("date").reset_index(drop=True)
            logger.info(f"Merged {len(df_live)} Infoway rows, total {len(df)} gold rows")
    except Exception as e:
        logger.debug(f"Infoway merge skipped: {e}")

    _compute_indicators(df)
    _CACHE[cache_key] = (mtime, time.time(), df)
    logger.info(f"Gold data refreshed: {len(df)} rows, cache TTL={_CACHE_TTL}s")
    return df


def _df_to_points(df: pd.DataFrame) -> list[GoldKLinePoint]:
    """Convert DataFrame rows to GoldKLinePoint list, dropping NaN fields."""
    points: list[GoldKLinePoint] = []
    for _, row in df.iterrows():
        p = GoldKLinePoint(
            date=row["date"].strftime("%Y-%m-%d"),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]) if pd.notna(row.get("volume")) else None,
            change_pct=round(float(row["change_pct"]), 2) if pd.notna(row.get("change_pct")) else None,
            ma5=round(float(row["ma5"]), 2) if pd.notna(row.get("ma5")) else None,
            ma10=round(float(row["ma10"]), 2) if pd.notna(row.get("ma10")) else None,
            ma20=round(float(row["ma20"]), 2) if pd.notna(row.get("ma20")) else None,
            ma50=round(float(row["ma50"]), 2) if pd.notna(row.get("ma50")) else None,
            ma200=round(float(row["ma200"]), 2) if pd.notna(row.get("ma200")) else None,
            bb_upper=round(float(row["bb_upper"]), 2) if pd.notna(row.get("bb_upper")) else None,
            bb_middle=round(float(row["bb_middle"]), 2) if pd.notna(row.get("bb_middle")) else None,
            bb_lower=round(float(row["bb_lower"]), 2) if pd.notna(row.get("bb_lower")) else None,
            bb_width=round(float(row["bb_width"]), 2) if pd.notna(row.get("bb_width")) else None,
            adx=round(float(row["adx"]), 2) if pd.notna(row.get("adx")) else None,
            plus_di=round(float(row["plus_di"]), 2) if pd.notna(row.get("plus_di")) else None,
            minus_di=round(float(row["minus_di"]), 2) if pd.notna(row.get("minus_di")) else None,
        )
        points.append(p)
    return points


@router.get(
    "/history",
    response_model=GoldKLineResponse,
    responses={
        200: {"description": "黄金K线历史数据"},
        404: {"description": "数据文件未找到", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="获取黄金K线历史数据",
    description="返回伦敦金 (XAUUSD) 日线 K 线及技术指标 (MA/布林带/ADX)。",
)
def get_gold_history(
    days: int = Query(365, ge=30, le=5000, description="返回最近多少天的数据"),
    symbol: str = Query("XAUUSD=X", description="品种代码"),
):
    try:
        df = _load_gold_data()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "黄金数据文件未找到"})

    df = df.tail(days).copy()
    points = _df_to_points(df)

    return GoldKLineResponse(
        symbol=symbol,
        name="伦敦金 (XAU/USD)",
        data=points,
        total=len(points),
        period="1d",
    )


# ---------------------------------------------------------------------------
#  Commodity real-time prices (East Money)
# ---------------------------------------------------------------------------

_OZT_TO_GRAM = 31.1035  # 1 troy ounce = 31.1035 grams


@router.get(
    "/commodities",
    response_model=dict,
    summary="获取黄金实时报价",
    description="返回伦敦金 (XAUUSD) 实时价格与涨跌幅，含人民币/克换算。数据来源：东方财富。",
)
def get_commodity_prices():
    """Return latest gold price via East Money scraper, with RMB/g conversion."""
    result = None
    try:
        from data_provider.eastmoney_fetcher import fetch_gold_realtime
        result = fetch_gold_realtime()
    except Exception as e:
        logger.debug(f"East Money realtime skipped: {e}")

    if result is None:
        return {"success": False, "data": [], "count": 0,
                "error": "暂无可用的实时数据（休市或网络异常）"}

    # Use rmb_per_gram from fetcher if available (ip138 provides it directly)
    rmb_per_gram = result.get("rmb_per_gram")
    usdcny_rate = 0.0
    if rmb_per_gram is None:
        # Fallback: compute from USDCNY rate
        usdcny_rate = 7.25
        try:
            from data_provider.eastmoney_fetcher import fetch_usdcny_rate
            rate_data = fetch_usdcny_rate()
            if rate_data and rate_data.get("rate", 0) > 0:
                usdcny_rate = rate_data["rate"]
        except Exception:
            logger.debug("USDCNY rate fetch skipped")
        rmb_per_gram = round(result["price"] * usdcny_rate / _OZT_TO_GRAM, 1)
    else:
        # Derive approximate USDCNY rate for display
        if result["price"] > 0:
            usdcny_rate = round(rmb_per_gram * _OZT_TO_GRAM / result["price"], 2)

    return {"success": True, "data": [{
        "code": "GOLD",
        "name": "黄金",
        "price": round(result["price"], 1),
        "prev_close": round(result["prev_close"], 1),
        "change": result["change"],
        "change_pct": result["change_pct"],
        "update_time": result["time"],
        "rmb_per_gram": float(rmb_per_gram),
        "usdcny_rate": round(usdcny_rate, 4),
    }], "count": 1}


# ---------------------------------------------------------------------------
#  Daily CSV update
# ---------------------------------------------------------------------------

@router.post(
    "/update-daily",
    response_model=dict,
    summary="更新黄金日K CSV",
    description="拉取 Infoway 最新日K线数据，追加到 gold_daily.csv。建议每日午夜调用。",
)
def update_gold_daily_csv():
    """Fetch latest daily candle from Infoway and append to CSV if new."""
    try:
        from data_provider.infoway_fetcher import fetch_latest_gold_daily
        live = fetch_latest_gold_daily(days=3)
        if not live:
            return {"success": False, "message": "Infoway 未返回数据，检查 API Key"}

        import pandas as pd
        df_existing = pd.read_csv(_DATA_PATH, parse_dates=["Date"])
        existing_dates = set(pd.to_datetime(df_existing["Date"]).dt.date)

        new_rows = []
        for row in live:
            dt = pd.to_datetime(row["date"]).date()
            if dt not in existing_dates:
                new_rows.append({
                    "Date": row["date"],
                    "Open": row["open"],
                    "High": row["high"],
                    "Low": row["low"],
                    "Close": row["close"],
                    "Volume": row.get("volume", 0),
                })

        if not new_rows:
            return {"success": True, "message": "已是最新，无需更新", "added": 0}

        df_new = pd.DataFrame(new_rows)
        df_new = df_new.sort_values("Date")
        df_merged = pd.concat([df_existing, df_new], ignore_index=True)
        df_merged = df_merged.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
        df_merged.to_csv(_DATA_PATH, index=False)

        # Clear cache so next request picks up new data
        _CACHE.clear()

        dates_added = [r["Date"] for r in new_rows]
        logger.info(f"Gold CSV updated: added {len(new_rows)} rows: {dates_added}")
        return {"success": True, "message": f"已添加 {len(new_rows)} 条", "added": len(new_rows), "dates": dates_added}
    except FileNotFoundError:
        return {"success": False, "message": "CSV 文件未找到"}
    except Exception as e:
        logger.warning(f"Gold CSV update failed: {e}")
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
#  Technical analysis
# ---------------------------------------------------------------------------

def _find_swings(high: np.ndarray, low: np.ndarray, dates: np.ndarray, window: int = 4) -> dict:
    """Find recent swing highs and lows within the last ~60 bars."""
    n = len(high)
    if n < window * 2:
        return {}
    lookback = min(n, 60)
    recent_high = high[-lookback:]
    recent_low = low[-lookback:]
    recent_dates = dates[-lookback:]

    swing_highs = []
    swing_lows = []
    for i in range(window, len(recent_high) - window):
        if recent_high[i] == max(recent_high[i - window:i + window + 1]):
            swing_highs.append((recent_dates[i], float(recent_high[i])))
        if recent_low[i] == min(recent_low[i - window:i + window + 1]):
            swing_lows.append((recent_dates[i], float(recent_low[i])))

    result = {}
    if swing_highs:
        result["swing_high"] = swing_highs[-1][1]
        result["swing_high_date"] = str(swing_highs[-1][0])
    if swing_lows:
        result["swing_low"] = swing_lows[-1][1]
        result["swing_low_date"] = str(swing_lows[-1][0])
    return result


def _analyze_gold(df: pd.DataFrame) -> dict:
    """Compute technical analysis summary from indicator dataframe."""
    if len(df) < 60:
        return {
            "trend": "数据不足",
            "trend_strength": "--",
            "ma_alignment": "--",
            "adx": 0, "plus_di": 0, "minus_di": 0,
            "bb_position": "--",
            "action": "观望",
            "action_reason": "历史数据不足，无法分析",
        }

    latest = df.iloc[-1]
    close = float(latest["close"])
    ma5 = float(latest.get("ma5") or 0)
    ma10 = float(latest.get("ma10") or 0)
    ma20 = float(latest.get("ma20") or 0)
    adx = float(latest.get("adx") or 0)
    pdi = float(latest.get("plus_di") or 0)
    mdi = float(latest.get("minus_di") or 0)
    bb_upper = float(latest.get("bb_upper") or 0)
    bb_lower = float(latest.get("bb_lower") or 0)
    bb_middle = float(latest.get("bb_middle") or 0)

    # --- trend direction ---
    if adx >= 25:
        strength = "强"
        if pdi > mdi:
            trend = "上升趋势"
        else:
            trend = "下降趋势"
    elif adx >= 20:
        strength = "中"
        if pdi > mdi:
            trend = "上升趋势"
        else:
            trend = "下降趋势"
    else:
        strength = "弱"
        trend = "震荡"

    # --- MA alignment ---
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            ma_align = "多头排列"
        elif ma5 < ma10 < ma20:
            ma_align = "空头排列"
        else:
            ma_align = "交叉震荡"
    else:
        ma_align = "--"

    # --- BB position ---
    if bb_upper and bb_lower and bb_middle:
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            pos = (close - bb_lower) / bb_range
            if pos > 0.8:
                bb_pos = "上轨附近"
            elif pos < 0.2:
                bb_pos = "下轨附近"
            else:
                bb_pos = "中轨附近"
        else:
            bb_pos = "--"
    else:
        bb_pos = "--"

    # --- swing points ---
    high_arr = df["high"].values
    low_arr = df["low"].values
    dates_arr = df["date"].values
    swings = _find_swings(high_arr, low_arr, dates_arr)

    swing_high = swings.get("swing_high")
    swing_low = swings.get("swing_low")

    # --- support / resistance ---
    support = max(ma20, swing_low or 0) if swing_low and ma20 else (ma20 or swing_low)
    resistance = min(swing_high or float("inf"), bb_upper or float("inf"))
    if resistance == float("inf"):
        resistance = None

    # --- action ---
    if trend == "震荡":
        action = "观望"
        reason = "ADX偏低，趋势不明确，建议等待方向选择"
    elif trend == "上升趋势":
        # Check if near support (good entry) or near resistance (wait)
        dist_to_support = (close - (support or close)) / close * 100 if support else 0
        dist_to_resist = ((resistance or close) - close) / close * 100 if resistance else 999
        if dist_to_support < 2:
            action = "加仓"
            reason = f"回踩支撑 {support:.1f}，上升趋势中回调买入"
        elif dist_to_resist < 2:
            action = "观望"
            reason = f"接近压力 {resistance:.1f}，等待突破或回调后再入场"
        else:
            action = "观望"
            reason = "上升趋势但离支撑较远，等回调到均线附近再加仓"
    else:  # 下降趋势
        dist_to_resist = ((resistance or close) - close) / close * 100 if resistance else 0
        if dist_to_resist < 2:
            action = "减仓"
            reason = f"反弹至压力 {resistance:.1f}，下降趋势中逢高减仓"
        else:
            action = "观望"
            reason = "下降趋势未到压力位，等待反弹减仓或企稳信号"

    result = {
        "current_price": round(close, 1),
        "trend": trend,
        "trend_strength": strength,
        "ma_alignment": ma_align,
        "adx": round(adx, 1),
        "plus_di": round(pdi, 1),
        "minus_di": round(mdi, 1),
        "bb_position": bb_pos,
        "swing_high": round(swing_high, 1) if swing_high else None,
        "swing_high_date": swings.get("swing_high_date"),
        "swing_low": round(swing_low, 1) if swing_low else None,
        "swing_low_date": swings.get("swing_low_date"),
        "support": round(support, 1) if support else None,
        "resistance": round(resistance, 1) if resistance else None,
        "action": action,
        "action_reason": reason,
    }
    return result


@router.get(
    "/analysis",
    response_model=GoldAnalysisResponse,
    responses={
        200: {"description": "黄金技术分析"},
        404: {"description": "数据文件未找到", "model": ErrorResponse},
    },
    summary="获取黄金技术分析",
    description="基于均线/布林带/ADX 的综合趋势判断与操作建议。",
)
def get_gold_analysis():
    try:
        df = _load_gold_data()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "黄金数据文件未找到"})

    analysis = _analyze_gold(df)
    return GoldAnalysisResponse(**analysis)


# ---------------------------------------------------------------------------
#  News (cached from DB, falls back to live search)
# ---------------------------------------------------------------------------

_GOLD_NEWS_CODES = ["XAUUSD", "GOLD", "XAUUSD=X", "XAU", "XAU/USD", "伦敦金", "黄金"]


def _query_gold_news_from_db(limit: int = 10) -> list[GoldNewsItem]:
    """Query recent gold news from the database cache."""
    try:
        from src.storage import get_db, NewsIntel
        db = get_db()
        cutoff = datetime.now() - timedelta(days=14)

        code_conditions = [NewsIntel.code.like(f"%{c}%") for c in _GOLD_NEWS_CODES[:4]]
        code_conditions.append(NewsIntel.name.like("%黄金%"))
        code_conditions.append(NewsIntel.name.like("%gold%"))
        code_conditions.append(NewsIntel.name.like("%XAU%"))

        with db.get_session() as session:
            stmt = (
                sa_select(NewsIntel)
                .where(
                    and_(
                        or_(*code_conditions),
                        NewsIntel.fetched_at >= cutoff,
                    )
                )
                .order_by(NewsIntel.fetched_at.desc())
                .limit(limit * 2)
            )
            results = session.execute(stmt).scalars().all()

            items: list[GoldNewsItem] = []
            seen_urls = set()
            for row in results:
                url = (row.url or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                pd_str = row.published_date.strftime("%Y-%m-%d") if row.published_date else None
                items.append(GoldNewsItem(
                    title=row.title,
                    snippet=row.snippet,
                    url=url,
                    source=row.source,
                    published_date=pd_str,
                ))
                if len(items) >= limit:
                    break
            return items
    except Exception:
        logger.debug("Gold news DB query failed", exc_info=True)
        return []


@router.get(
    "/news",
    response_model=GoldNewsResponse,
    responses={
        200: {"description": "黄金相关新闻"},
    },
    summary="获取黄金相关新闻",
    description="优先从数据库缓存返回近期黄金新闻，缓存无数据时尝试在线搜索。",
)
def get_gold_news(
    limit: int = Query(10, ge=1, le=30, description="返回数量限制"),
    refresh: bool = Query(False, description="强制刷新（触发在线搜索）"),
):
    # Return cached news unless refresh requested
    if not refresh:
        cached = _query_gold_news_from_db(limit)
        if cached:
            return GoldNewsResponse(success=True, items=cached, total=len(cached), source="cache")

    # Try live search
    try:
        from src.search_service import get_search_service
        service = get_search_service()
        if service.is_available:
            from src.search_service import SearchResponse
            response = service.search_stock_news("XAUUSD", "伦敦金", max_results=limit,
                                                  focus_keywords=["gold", "黄金", "金价"])
            if response and response.success and response.results:
                items: list[GoldNewsItem] = []
                for r in response.results:
                    items.append(GoldNewsItem(
                        title=r.title,
                        snippet=r.snippet,
                        url=r.url or "",
                        source=r.source,
                        published_date=r.published_date,
                    ))
                # Persist to cache
                try:
                    from src.storage import get_db
                    get_db().save_news_intel(
                        code="XAUUSD",
                        name="伦敦金",
                        dimension="latest_news",
                        response=response,
                    )
                except Exception:
                    logger.debug("Failed to cache gold news", exc_info=True)
                return GoldNewsResponse(success=True, items=items, total=len(items), source="api")
    except Exception:
        logger.debug("Gold news live search failed", exc_info=True)

    return GoldNewsResponse(success=True, items=[], total=0, source="none")
