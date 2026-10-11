# -*- coding: utf-8 -*-
"""不可变数据模型：行情快照、日 K、指标、筛选条件。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Quote:
    """一次全市场快照中的单只股票（新浪排行 + 腾讯增强合并后）。"""

    symbol: str            # 带市场前缀：sh600519 / sz300308
    code: str              # 6 位代码
    name: str
    price: float
    prev_close: float
    open: float
    high: float
    low: float
    pct: float             # 涨跌幅 %
    volume: float          # 成交量（股）
    amount: float          # 成交额（元）
    turnover_rate: float   # 换手率 %
    pe: Optional[float] = None
    pb: Optional[float] = None
    circ_mv: Optional[float] = None   # 流通市值（元）
    volume_ratio: Optional[float] = None  # 量比（腾讯）
    amplitude: Optional[float] = None     # 振幅 %（腾讯）
    market_date: Optional[str] = None     # 行情所属交易日 YYYY-MM-DD（腾讯，节假日判定用）
    tick_time: str = ""                   # 交易所时间（HH:MM:SS 或完整时间串）


@dataclass(frozen=True)
class DailyBar:
    """一根日 K（前复权），volume 单位统一为「手」。"""

    symbol: str
    date: str              # YYYY-MM-DD
    open: float
    close: float
    high: float
    low: float
    volume: float          # 手


@dataclass(frozen=True)
class Indicators:
    """单只股票最新交易日的技术指标。"""

    symbol: str
    trade_date: str
    close: float
    dif: float
    dea: float
    hist: float            # MACD 柱 = 2*(DIF-DEA)
    vol_ma5: Optional[float]
    vol_ma20: Optional[float]
    history: tuple[tuple[float, float, float], ...] = field(default_factory=tuple)
    # history：最近若干根 (dif, dea, hist)，金叉回看用


@dataclass(frozen=True)
class Range:
    """数值区间条件，min/max 均可为空表示单边。"""

    min: Optional[float] = None
    max: Optional[float] = None

    def contains(self, value: Optional[float]) -> bool:
        if value is None:
            return False
        if self.min is not None and value < self.min:
            return False
        if self.max is not None and value > self.max:
            return False
        return True


@dataclass(frozen=True)
class MacdCond:
    """MACD 条件，全部可选；留空表示不限制。"""

    golden_cross_within: Optional[int] = None  # 最近 N 根内出现 0 轴下→上金叉
    hist_gt0: bool = False                      # 当前 MACD 柱 > 0
    dif_gt0: bool = False                      # DIF 在零轴上方
    dif_range: Optional[Range] = None
    dea_range: Optional[Range] = None
    hist_range: Optional[Range] = None


@dataclass(frozen=True)
class ScreenConfig:
    """筛选条件组合：任何一块为 None 表示不过滤该维度。"""

    name: str = "未命名条件组"
    macd: Optional[MacdCond] = None
    turnover_rate: Optional[Range] = None   # 换手率 %
    pct: Optional[Range] = None             # 涨跌幅 %
    amount_yi: Optional[Range] = None       # 成交额（亿元）
    volume_ratio: Optional[Range] = None    # 量比
    vol_vs_ma5: Optional[Range] = None      # 今日成交量 / 5 日均量
    price: Optional[Range] = None           # 现价区间


@dataclass(frozen=True)
class Match:
    """一只股票的筛选结果：是否命中 + 命中/未命中的条件说明。"""

    symbol: str
    code: str
    name: str
    matched: bool
    reasons: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


class ConfigError(RuntimeError):
    """筛选配置文件非法。"""
