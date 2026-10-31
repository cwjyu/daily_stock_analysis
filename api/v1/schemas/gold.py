# -*- coding: utf-8 -*-
"""
Gold market data schemas — K-line with technical indicators.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class GoldKLinePoint(BaseModel):
    """Gold K-line with pre-computed indicators."""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: Optional[float] = Field(None, description="成交量")
    change_pct: Optional[float] = Field(None, description="涨跌幅 (%)")

    # MA lines
    ma5: Optional[float] = Field(None)
    ma10: Optional[float] = Field(None)
    ma20: Optional[float] = Field(None)
    ma50: Optional[float] = Field(None)
    ma200: Optional[float] = Field(None)

    # Bollinger Bands (20,2)
    bb_upper: Optional[float] = Field(None)
    bb_middle: Optional[float] = Field(None)
    bb_lower: Optional[float] = Field(None)
    bb_width: Optional[float] = Field(None)

    # ADX (14)
    adx: Optional[float] = Field(None)
    plus_di: Optional[float] = Field(None)
    minus_di: Optional[float] = Field(None)


class GoldKLineResponse(BaseModel):
    """Gold K-line history response."""
    symbol: str = Field(..., description="品种代码")
    name: str = Field(default="伦敦金", description="品种名称")
    data: List[GoldKLinePoint] = Field(default_factory=list, description="K线数据")
    total: int = Field(0, description="数据条数")
    period: Optional[str] = Field(None, description="周期 (1d etc.)")


class GoldAnalysisResponse(BaseModel):
    """Technical analysis summary."""
    current_price: float = Field(..., description="当前价格")
    trend: str = Field(..., description="趋势方向: 上升趋势/下降趋势/震荡")
    trend_strength: str = Field(..., description="趋势强度: 强/中/弱")
    ma_alignment: str = Field(..., description="均线排列: 多头排列/空头排列/交叉震荡")
    adx: float = Field(..., description="ADX 值")
    plus_di: float = Field(..., description="+DI")
    minus_di: float = Field(..., description="-DI")
    bb_position: str = Field(..., description="布林带位置: 上轨附近/中轨附近/下轨附近")
    swing_high: Optional[float] = Field(None, description="近期波段最高价")
    swing_high_date: Optional[str] = Field(None, description="波段最高价日期")
    swing_low: Optional[float] = Field(None, description="近期波段最低价")
    swing_low_date: Optional[str] = Field(None, description="波段最低价日期")
    support: Optional[float] = Field(None, description="最近支撑位")
    resistance: Optional[float] = Field(None, description="最近压力位")
    action: str = Field(..., description="操作建议: 加仓/减仓/观望")
    action_reason: str = Field(..., description="建议理由")


class GoldNewsItem(BaseModel):
    """Gold-related news item."""
    title: str = Field(..., description="新闻标题")
    snippet: Optional[str] = Field(None, description="新闻摘要")
    url: str = Field(..., description="新闻链接")
    source: Optional[str] = Field(None, description="来源")
    published_date: Optional[str] = Field(None, description="发布日期")


class GoldNewsResponse(BaseModel):
    """Gold news list response."""
    success: bool = Field(True)
    items: List[GoldNewsItem] = Field(default_factory=list, description="新闻列表")
    total: int = Field(0, description="总数")
    source: str = Field("cache", description="数据来源: cache / api")
