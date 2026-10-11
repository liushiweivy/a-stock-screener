# -*- coding: utf-8 -*-
"""A 股交易时段调度（纯函数，全部用东八区显式时间）。

快照槽位比整点/半点晚 5 分钟，等行情商数据落定：
09:35/10:05/10:35/11:05/11:35/13:05/13:35/14:05/14:35/15:05；
收盘后 15:20 统一补日 K 并重算 MACD（100 只逐只请求，盘中不跑）。
节假日不额外维护日历：休市日拿到的行情日期不是今天，sync 层记 closed 跳过。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .config import CN_TZ

SNAPSHOT_SLOTS: tuple[time, ...] = tuple(
    time(h, m) for h, m in
    [(9, 35), (10, 5), (10, 35), (11, 5), (11, 35),
     (13, 5), (13, 35), (14, 5), (14, 35), (15, 5)]
)
KLINE_SLOT = time(15, 20)


@dataclass(frozen=True)
class Job:
    kind: str       # snapshot / klines
    slot: str       # HH:MM
    when: datetime


def _is_weekday(day: datetime) -> bool:
    return day.weekday() < 5


def _at(day: datetime, t: time) -> datetime:
    return datetime(day.year, day.month, day.day, t.hour, t.minute, tzinfo=CN_TZ)


def due_jobs(now: datetime) -> tuple[Job, ...]:
    """当前时刻之前、今天尚未执行的槽位（进程中途启动时补齐判定，sync 层去重）。"""
    if not _is_weekday(now):
        return ()
    jobs = [Job("snapshot", t.strftime("%H:%M"), _at(now, t))
            for t in SNAPSHOT_SLOTS if _at(now, t) <= now]
    if _at(now, KLINE_SLOT) <= now:
        jobs.append(Job("klines", KLINE_SLOT.strftime("%H:%M"), _at(now, KLINE_SLOT)))
    return tuple(jobs)


def _next_weekday_noon(day: datetime) -> datetime:
    d = day
    while not _is_weekday(d):
        d += timedelta(days=1)
    return d


def next_snapshot(now: datetime) -> datetime:
    """下一次快照时间（周末/收盘后滚动到下一交易日首槽）。"""
    candidate_day = now if _is_weekday(now) else _next_weekday_noon(now + timedelta(days=1))
    for t in SNAPSHOT_SLOTS:
        when = _at(candidate_day, t)
        if when > now:
            return when
    nxt = _next_weekday_noon(candidate_day + timedelta(days=1))
    return _at(nxt, SNAPSHOT_SLOTS[0])


def next_kline_sync(now: datetime) -> datetime:
    """下一次日 K 同步时间（交易日 15:20）。"""
    d = now
    for _ in range(8):  # 最多跨一个长假周
        if _is_weekday(d):
            when = _at(d, KLINE_SLOT)
            if when > now:
                return when
        d += timedelta(days=1)
    return _at(d, KLINE_SLOT)
