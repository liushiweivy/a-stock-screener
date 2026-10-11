# -*- coding: utf-8 -*-
"""技术指标纯函数：EMA / MACD / 均量。输入均为时间正序（旧→新）。"""
from __future__ import annotations

from typing import Optional, Sequence

# 落库的 HIST 明细根数：金叉回看窗口上限（golden_cross_within 不能超过它）
HIST_LOOKBACK = 20


def ema(values: Sequence[float], period: int) -> list[float]:
    """指数移动平均；首值作种子（与常见行情软件递推口径一致，200 根暖机足够收敛）。"""
    if period <= 0:
        raise ValueError("period 必须为正整数")
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(out[-1] + alpha * (value - out[-1]))
    return out


def ma(values: Sequence[float], period: int) -> Optional[float]:
    """最后 period 根简单均值；样本不足返回 None。"""
    if period <= 0:
        raise ValueError("period 必须为正整数")
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def macd(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> list[tuple[float, float, float]]:
    """MACD：返回与 closes 等长的 (DIF, DEA, HIST)，HIST=2*(DIF-DEA)（A 股行情软件口径）。"""
    if fast >= slow:
        raise ValueError("fast 必须小于 slow")
    if not closes:
        return []
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    dif_series = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea_series = ema(dif_series, signal)
    return [
        (dif, dea, round(2.0 * (dif - dea), 6))
        for dif, dea in zip(dif_series, dea_series)
    ]


def latest_indicators(
    symbol: str,
    trade_date: str,
    closes: Sequence[float],
    volumes: Sequence[float],
    hist_tail: int = HIST_LOOKBACK,
) -> tuple[float, float, float, Optional[float], Optional[float],
           tuple[tuple[float, float, float], ...]]:
    """算最新一根的 DIF/DEA/HIST、5 日/20 日均量，以及最近 hist_tail 根明细。"""
    series = macd(closes)
    dif, dea, hist = series[-1]
    history = tuple((round(d, 6), round(e, 6), h) for d, e, h in series[-hist_tail:])
    return dif, dea, hist, ma(volumes, 5), ma(volumes, 20), history
