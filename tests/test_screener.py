# -*- coding: utf-8 -*-
"""筛选配置解析与逐只判定。"""
import pytest

from ascreener.models import ConfigError, MacdCond, Range, ScreenConfig
from ascreener.screener import config_from_dict, evaluate, golden_cross, load_config


def _quote(**over):
    base = {"symbol": "sh600519", "code": "600519", "name": "贵州茅台",
            "price": 1263.0, "pct": 0.57, "volume": 3_511_100.0,
            "amount": 4_453_223_189.0, "turnover_rate": 0.28,
            "volume_ratio": 1.18}
    base.update(over)
    return base


def _ind(**over):
    base = {"dif": 1.5, "dea": 1.2, "hist": 0.6, "vol_ma5": 30000.0,
            "hist_series": ((-0.2, -0.1, -0.3), (0.1, -0.05, 0.3), (1.5, 1.2, 0.6))}
    base.update(over)
    return base


def test_config_parses_ranges():
    cfg = config_from_dict({
        "name": "t",
        "turnover_rate": {"min": 3, "max": 20},
        "amount_yi": {"min": 5},
    })
    assert cfg.turnover_rate == Range(3.0, 20.0)
    assert cfg.amount_yi == Range(5.0, None)


def test_config_rejects_inverted_range_and_empty():
    with pytest.raises(ConfigError):
        config_from_dict({"turnover_rate": {"min": 9, "max": 1}})
    with pytest.raises(ConfigError):
        config_from_dict({"name": "空"})


def test_config_macd_within_validation():
    with pytest.raises(ConfigError):
        config_from_dict({"macd": {"golden_cross_within": 0}})
    with pytest.raises(ConfigError):
        config_from_dict({"macd": {"golden_cross_within": 21}})  # 超 HIST 明细上限
    cfg = config_from_dict({"macd": {"golden_cross_within": 3, "dif_gt0": True}})
    assert cfg.macd == MacdCond(golden_cross_within=3, hist_gt0=False, dif_gt0=True)


def test_golden_cross_logic():
    series = ((0, 0, -0.3), (0, 0, -0.1), (0, 0, 0.2), (0, 0, 0.4))
    assert golden_cross(series, 3) is True
    # 已经在零轴上太久（3 根内无穿越）不算
    assert golden_cross(((0, 0, 0.5), (0, 0, 0.6), (0, 0, 0.7)), 2) is False
    # 穿了但当前又跌回零轴下
    back = ((0, 0, -0.1), (0, 0, 0.2), (0, 0, -0.2))
    assert golden_cross(back, 3) is False


def test_evaluate_match_and_failures():
    cfg = ScreenConfig(
        name="t",
        macd=MacdCond(golden_cross_within=3),
        turnover_rate=Range(0.1, 5.0),
        amount_yi=Range(10.0, None),
    )
    m = evaluate(cfg, _quote(), _ind())
    assert m.matched is True
    assert any("金叉" in r for r in m.reasons)
    assert any("成交额(亿)=44.53" in r for r in m.reasons)

    m2 = evaluate(cfg, _quote(turnover_rate=0.05, amount=1e8), _ind())
    assert m2.matched is False
    assert any("换手率" in f for f in m2.failed)
    assert any("成交额(亿)=1.00" in f for f in m2.failed)


def test_evaluate_macd_missing_data_fails():
    cfg = ScreenConfig(name="t", macd=MacdCond(hist_gt0=True))
    m = evaluate(cfg, _quote(), None)
    assert m.matched is False
    assert "日K" in m.failed[0]


def test_vol_vs_ma5_ratio():
    # 今日成交 3,000,000 股，5 日均量 20,000 手=2,000,000 股 → 1.5 倍
    cfg = ScreenConfig(name="t", vol_vs_ma5=Range(1.2, None))
    m = evaluate(cfg, _quote(volume=3_000_000), _ind(vol_ma5=20000.0))
    assert m.matched is True


def test_load_config_missing_file_writes_default(tmp_path):
    path = tmp_path / "cfg.json"
    cfg = load_config(path)
    assert path.exists()
    assert cfg.macd is not None
