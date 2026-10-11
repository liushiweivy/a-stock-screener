# -*- coding: utf-8 -*-
"""同步编排：快照同步、日 K+指标同步、按交易时段槽位执行。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from . import config
from .datasource import DataError, SinaTencentSource
from .indicators import latest_indicators
from .models import Indicators, Quote
from .schedule import due_jobs
from .storage import Repository

logger = logging.getLogger("ascreener.sync")

# 算 MACD 至少要 slow+signal 根，35 根是底线，实际拉 200 根暖机
_MIN_BARS = 35


@dataclass(frozen=True)
class RunResult:
    kind: str           # snapshot / klines
    slot: str
    ok: bool
    status: str         # ok / closed / skipped / error
    detail: str


def _is_market_closed(quotes: tuple[Quote, ...], today: str) -> bool:
    """所有带腾讯行情日期的股票都不是今天 → 节假日休市（腾讯不可用时返回 False 照常同步）。"""
    dated = [q.market_date for q in quotes if q.market_date]
    return bool(dated) and all(d != today for d in dated)


def sync_snapshot(
    repo: Repository,
    ds: SinaTencentSource,
    now: datetime,
    size: int = config.UNIVERSE_SIZE,
    slot: str = "",
) -> RunResult:
    """一次全市场热度快照：取排行 → 休市判定 → 刷股票池 → 存快照。"""
    try:
        quotes = ds.hot_quotes(size)
    except DataError as e:
        logger.error("快照取数失败：%s", e)
        return RunResult("snapshot", slot, False, "error", str(e)[:300])

    today = now.strftime("%Y-%m-%d")
    if _is_market_closed(quotes, today):
        logger.info("行情日期非今天（%s），判定休市，跳过快照", today)
        return RunResult("snapshot", slot, True, "closed", f"{today} 非交易日")

    ts = now.isoformat(timespec="seconds")
    repo.upsert_universe(quotes, ts)
    repo.insert_snapshot(ts, quotes)
    return RunResult("snapshot", slot, True, "ok", f"{len(quotes)} 只")


def sync_klines(
    repo: Repository,
    ds: SinaTencentSource,
    now: datetime,
    sleep: Callable[[float], None],
    pause: float = config.KLINE_PAUSE_S,
    slot: str = "",
) -> RunResult:
    """逐只拉日 K → 落库 → 重算 MACD/均量。部分失败不影响其余股票。"""
    symbols = repo.universe_symbols()
    if not symbols:
        return RunResult("klines", slot, False, "error", "股票池为空，请先同步快照")

    today = now.strftime("%Y-%m-%d")
    ok_count = 0
    failures: list[str] = []
    updated_at = now.isoformat(timespec="seconds")
    for i, symbol in enumerate(symbols):
        try:
            bars = ds.daily_bars(symbol)
            # 盘中手动触发时最后一根是当日未完成 K，剔除以避免 MACD 假信号；
            # 15:20 定时任务时 now≥15:00，保留当日完整 K
            if now.hour < 15 and bars and bars[-1].date == today:
                bars = bars[:-1]
            if len(bars) < _MIN_BARS:
                raise DataError(f"K 线仅 {len(bars)} 根，少于 {_MIN_BARS}")
            repo.upsert_bars(bars)
            dates, closes, volumes = repo.daily_closes_volumes(symbol)
            # 指标只在「一次拉取得到的同一复权基准窗口」上算，避免库里
            # 老复权基准/新浪不复权数据与新 qfq 拼接造成 EMA 台阶（评审 M2）
            window = config.KLINE_COUNT
            dates, closes, volumes = (dates[-window:], closes[-window:], volumes[-window:])
            dif, dea, hist, vma5, vma20, history = latest_indicators(
                symbol, dates[-1], closes, volumes)
            repo.upsert_indicator(Indicators(
                symbol=symbol, trade_date=dates[-1], close=closes[-1],
                dif=round(dif, 6), dea=round(dea, 6), hist=hist,
                vol_ma5=vma5, vol_ma20=vma20, history=history), updated_at)
            ok_count += 1
        except Exception as e:  # 上游任何异常都不能拖垮整槽（评审 HIGH）
            logger.warning("日 K 同步失败 %s：%s", symbol, e)
            failures.append(symbol)
        if i < len(symbols) - 1 and pause:
            sleep(pause)

    status = "ok" if not failures else ("ok" if ok_count else "error")
    detail = f"{ok_count}/{len(symbols)} 只"
    if failures:
        detail += "，失败：" + ",".join(failures[:5]) + (
            f" 等{len(failures)}只" if len(failures) > 5 else "")
    return RunResult("klines", slot, ok_count > 0, status, detail)


def run_due(
    repo: Repository,
    ds: Optional[SinaTencentSource] = None,
    now: Optional[datetime] = None,
    sleep: Callable[[float], None] = None,
) -> list[RunResult]:
    """按当前时间执行所有到期未跑的槽位（由 sync_runs 去重，同日同槽只跑一次）。

    进程错过多个快照槽位时只真实取数一次，其余槽位记 skipped，避免补打风暴。
    """
    ds = ds or SinaTencentSource()
    now = now or datetime.now(config.CN_TZ)
    sleep = sleep or time.sleep

    results: list[RunResult] = []
    snapshot_done = False
    date = now.strftime("%Y-%m-%d")
    for job in due_jobs(now):
        # 成功与失败都占槽：确定性故障不能每 30s 重放一整天；下一槽位仍会再试
        if repo.ran_slot_recorded(date, job.kind, job.slot):
            continue
        try:
            if job.kind == "snapshot" and snapshot_done:
                result = RunResult(job.kind, job.slot, True, "skipped", "合并到上一槽位")
            elif job.kind == "snapshot":
                result = sync_snapshot(repo, ds, now, slot=job.slot)
                snapshot_done = result.ok
            elif repo.latest_market_date() not in (None, date):
                # 最近快照不是今天 → 节假日休市，不发 100 个无用 K 线请求
                result = RunResult(job.kind, job.slot, True, "closed", f"{date} 非交易日")
            else:
                result = sync_klines(repo, ds, now, sleep=sleep, slot=job.slot)
        except Exception as e:  # 评审 HIGH：任何意外都要落库占槽，不能逃逸出循环
            logger.exception("同步任务异常 %s %s", job.kind, job.slot)
            result = RunResult(job.kind, job.slot, False, "error", str(e)[:300])
        repo.record_run(now, result.kind, result.slot, result.ok,
                        result.status, result.detail)
        results.append(result)
    return results
