# -*- coding: utf-8 -*-
"""交易时段调度纯逻辑。"""
from datetime import datetime

from ascreener.config import CN_TZ
from ascreener.schedule import (KLINE_SLOT, SNAPSHOT_SLOTS, due_jobs,
                                next_kline_sync, next_snapshot)


def _dt(month: int, day: int, h: int, m: int) -> datetime:
    # 2026-10-12 是周一
    return datetime(2026, month, day, h, m, tzinfo=CN_TZ)


def test_weekend_has_no_due_jobs():
    # 2026-10-11 周日
    assert due_jobs(_dt(10, 11, 10, 0)) == ()


def test_due_slots_before_open_empty():
    assert due_jobs(_dt(10, 12, 9, 30)) == ()


def test_due_slots_mid_morning():
    jobs = due_jobs(_dt(10, 12, 10, 10))
    kinds = [(j.kind, j.slot) for j in jobs]
    assert kinds == [("snapshot", "09:35"), ("snapshot", "10:05")]


def test_due_after_close_includes_klines():
    jobs = due_jobs(_dt(10, 12, 15, 30))
    assert jobs[-1].kind == "klines"
    assert jobs[-1].slot == "15:20"
    assert len([j for j in jobs if j.kind == "snapshot"]) == len(SNAPSHOT_SLOTS)


def test_next_snapshot_within_day():
    nxt = next_snapshot(_dt(10, 12, 9, 40))
    assert (nxt.hour, nxt.minute) == (10, 5)


def test_next_snapshot_after_close_rolls_weekday():
    nxt = next_snapshot(_dt(10, 12, 15, 30))
    assert nxt.day == 13 and (nxt.hour, nxt.minute) == (9, 35)


def test_next_snapshot_on_sunday_rolls_to_monday():
    nxt = next_snapshot(_dt(10, 11, 12, 0))
    assert nxt.day == 12 and nxt.weekday() == 0
    assert (nxt.hour, nxt.minute) == (SNAPSHOT_SLOTS[0].hour, SNAPSHOT_SLOTS[0].minute)


def test_next_kline_sync_today_then_next_day():
    today = next_kline_sync(_dt(10, 12, 10, 0))
    assert today.day == 12 and (today.hour, today.minute) == (
        KLINE_SLOT.hour, KLINE_SLOT.minute)
    later = next_kline_sync(_dt(10, 12, 16, 0))
    assert later.day == 13
