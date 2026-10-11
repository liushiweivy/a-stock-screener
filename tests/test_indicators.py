# -*- coding: utf-8 -*-
"""指标纯函数：EMA/MACD/均量。"""
import math

import pytest

from ascreener.indicators import ema, latest_indicators, ma, macd


def test_ema_constant_series_stays_flat():
    assert ema([7.0] * 5, 12) == [7.0] * 5


def test_ema_known_values_period2():
    # alpha=2/3，首值种子：1 → 1.6667 → 2.5556 → 3.5185
    out = ema([1.0, 2.0, 3.0, 4.0], 2)
    assert out[0] == 1.0
    assert out[1] == pytest.approx(1.6667, abs=1e-4)
    assert out[2] == pytest.approx(2.5556, abs=1e-4)
    assert out[3] == pytest.approx(3.5185, abs=1e-4)


def test_ema_rejects_nonpositive_period():
    with pytest.raises(ValueError):
        ema([1, 2], 0)


def test_ma_basic_and_insufficient():
    assert ma([1.0, 2.0, 3.0], 3) == 2.0
    assert ma([1.0, 2.0], 3) is None


def test_macd_flat_is_zero_uptrend_positive():
    flat = macd([10.0] * 40)
    assert all(dif == 0 and hist == 0 for dif, _, hist in flat)

    up = macd([10 + i * 0.5 for i in range(60)])
    assert up[-1][0] > 0          # DIF > 0
    assert up[-1][2] > 0          # HIST > 0


def test_macd_length_and_fast_lt_slow():
    series = macd([1.0, 2.0, 3.0])
    assert len(series) == 3
    with pytest.raises(ValueError):
        macd([1, 2, 3], fast=26, slow=12)


def test_latest_indicators_bundle():
    closes = [10 + i * 0.2 for i in range(50)]
    volumes = [1000.0] * 50
    dif, dea, hist, vma5, vma20, history = latest_indicators(
        "sh600519", "2026-10-09", closes, volumes, hist_tail=5)
    assert dif > 0 and hist > 0
    assert vma5 == 1000.0 and vma20 == 1000.0
    assert len(history) == 5
    assert all(len(t) == 3 and all(math.isfinite(x) for x in t) for t in history)
