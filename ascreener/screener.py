# -*- coding: utf-8 -*-
"""条件筛选：JSON 配置 → ScreenConfig；对 (快照, 指标) 逐只判定。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .indicators import HIST_LOOKBACK
from .models import (ConfigError, MacdCond, Match, Range, ScreenConfig)

DEFAULT_CONFIG: dict[str, Any] = {
    "name": "默认：近3日MACD金叉 + 换手3%~25% + 量能放大1.2倍 + 成交额≥5亿",
    "macd": {"golden_cross_within": 3, "dif_gt0": False},
    "turnover_rate": {"min": 3, "max": 25},
    "vol_vs_ma5": {"min": 1.2},
    "amount_yi": {"min": 5},
}


def _range(raw: Any, field: str) -> Optional[Range]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{field} 必须是对象 {{min,max}}")
    try:
        lo = float(raw["min"]) if raw.get("min") is not None else None
        hi = float(raw["max"]) if raw.get("max") is not None else None
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{field} 的 min/max 必须是数字") from e
    if lo is not None and hi is not None and lo > hi:
        raise ConfigError(f"{field} 的 min 不能大于 max")
    return Range(lo, hi)


def _macd(raw: Any) -> Optional[MacdCond]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("macd 必须是对象")
    within = raw.get("golden_cross_within")
    if within is not None and (not isinstance(within, int) or within < 1):
        raise ConfigError("macd.golden_cross_within 必须是 ≥1 的整数")
    if within is not None and within > HIST_LOOKBACK:
        raise ConfigError(
            f"macd.golden_cross_within 不能超过 {HIST_LOOKBACK}（落库 HIST 明细上限）")
    return MacdCond(
        golden_cross_within=within,
        hist_gt0=bool(raw.get("hist_gt0", False)),
        dif_gt0=bool(raw.get("dif_gt0", False)),
        dif_range=_range(raw.get("dif"), "macd.dif"),
        dea_range=_range(raw.get("dea"), "macd.dea"),
        hist_range=_range(raw.get("hist"), "macd.hist"),
    )


def config_from_dict(raw: Any) -> ScreenConfig:
    """dict → 不可变 ScreenConfig，非法输入 fail-fast。"""
    if not isinstance(raw, dict):
        raise ConfigError("筛选配置根节点必须是 JSON 对象")
    cfg = ScreenConfig(
        name=str(raw.get("name", "未命名条件组")),
        macd=_macd(raw.get("macd")),
        turnover_rate=_range(raw.get("turnover_rate"), "turnover_rate"),
        pct=_range(raw.get("pct"), "pct"),
        amount_yi=_range(raw.get("amount_yi"), "amount_yi"),
        volume_ratio=_range(raw.get("volume_ratio"), "volume_ratio"),
        vol_vs_ma5=_range(raw.get("vol_vs_ma5"), "vol_vs_ma5"),
        price=_range(raw.get("price"), "price"),
    )
    if all(v is None or v is False for v in (
            cfg.macd, cfg.turnover_rate, cfg.pct, cfg.amount_yi,
            cfg.volume_ratio, cfg.vol_vs_ma5, cfg.price)):
        raise ConfigError("至少配置一个筛选条件")
    return cfg


def ensure_config_file(path: Path) -> None:
    """配置文件不存在则写默认值，让用户有处可改。"""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_config(path: Path) -> ScreenConfig:
    ensure_config_file(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"配置文件不是合法 JSON：{e}") from e
    return config_from_dict(raw)


def golden_cross(hist_series: tuple, within: int) -> bool:
    """最近 within 根内发生 HIST 从 ≤0 上穿 >0，且当前仍在 0 轴上方。"""
    if len(hist_series) < 2:
        return False
    tail = hist_series[-(within + 1):]
    crossed = any(
        tail[k - 1][2] <= 0 < tail[k][2] for k in range(1, len(tail))
    )
    return bool(crossed and hist_series[-1][2] > 0)


def evaluate(cfg: ScreenConfig, quote: dict, ind: Optional[dict]) -> Match:
    """对单只股票执行全部已配置条件，收集命中与未命中的中文说明。"""
    reasons: list[str] = []
    failed: list[str] = []

    def check_range(label: str, rng: Optional[Range], value: Optional[float],
                    fmt: str = "{:.2f}") -> None:
        if rng is None:
            return
        ok = rng.contains(value)
        text = label + "=" + (fmt.format(value) if value is not None else "—")
        (reasons if ok else failed).append(text)

    check_range("换手率", cfg.turnover_rate, quote.get("turnover_rate"))
    check_range("涨跌幅", cfg.pct, quote.get("pct"))
    check_range("成交额(亿)", cfg.amount_yi,
                (quote.get("amount") or 0) / 1e8)
    check_range("量比", cfg.volume_ratio, quote.get("volume_ratio"))
    check_range("现价", cfg.price, quote.get("price"))

    # 量能相对 5 日均量（快照量单位股，vol_ma5 单位手）
    if cfg.vol_vs_ma5 is not None:
        ratio = None
        if ind and ind.get("vol_ma5"):
            ratio = quote["volume"] / (ind["vol_ma5"] * 100.0)
        check_range("今日量/5日均量", cfg.vol_vs_ma5, ratio)

    mc = cfg.macd
    if mc is not None:
        if ind is None:
            failed.append("MACD：尚无日K数据")
        else:
            if mc.golden_cross_within is not None:
                hit = golden_cross(tuple(ind["hist_series"]), mc.golden_cross_within)
                (reasons if hit else failed).append(
                    f"{mc.golden_cross_within}日内MACD金叉")
            for label, cond, val in (
                    ("HIST>0", mc.hist_gt0, ind["hist"] > 0),
                    ("DIF>0", mc.dif_gt0, ind["dif"] > 0)):
                if cond:
                    (reasons if val else failed).append(label)
            check_range("DIF", mc.dif_range, ind["dif"], "{:.4f}")
            check_range("DEA", mc.dea_range, ind["dea"], "{:.4f}")
            check_range("HIST", mc.hist_range, ind["hist"], "{:.4f}")

    return Match(
        symbol=quote["symbol"], code=quote.get("code", quote["symbol"]),
        name=quote.get("name", ""), matched=not failed,
        reasons=tuple(reasons), failed=tuple(failed),
    )


def screen(cfg: ScreenConfig, quotes: list[dict],
           indicators: dict[str, dict]) -> list[Match]:
    """整池筛选，命中者排前（内部保持成交额排名顺序）。"""
    matches = [evaluate(cfg, q, indicators.get(q["symbol"])) for q in quotes]
    return [m for m in matches if m.matched] + \
           [m for m in matches if not m.matched]
