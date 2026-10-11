# -*- coding: utf-8 -*-
"""同步编排：假数据源 + 临时 SQLite 端到端。"""
from datetime import datetime, timedelta

import pytest

from ascreener.config import CN_TZ
from ascreener.datasource import DataError
from ascreener.models import Quote
from ascreener.storage import Repository
from ascreener.sync import run_due, sync_klines, sync_snapshot


def _quote(symbol: str, market_date: str) -> Quote:
    return Quote(symbol=symbol, code=symbol[2:], name=f"股票{symbol}",
                 price=11.0, prev_close=10.0, open=10.2, high=11.5, low=10.1,
                 pct=10.0, volume=5_000_000, amount=8e8, turnover_rate=4.2,
                 volume_ratio=1.6, amplitude=14.0, market_date=market_date,
                 tick_time=market_date.replace("-", "") + "100500")


class FakeDS:
    def __init__(self, n: int = 5, closed: bool = False,
                 fail_hot: bool = False, crash_symbol: str | None = None,
                 end_date: datetime | None = None) -> None:
        self.today = "2026-10-12"
        self.closed = closed
        self.fail_hot = fail_hot
        self.crash_symbol = crash_symbol
        self.end_date = end_date or datetime(2026, 9, 9)
        self.quotes = tuple(
            _quote(f"sh600{i:03d}", "2026-10-09" if closed else self.today)
            for i in range(n))
        self.kline_calls: list[str] = []

    def hot_quotes(self, size: int = 100):
        if self.fail_hot:
            raise DataError("连接被掐断")
        return self.quotes[:size]

    def daily_bars(self, symbol: str, count: int = 200):
        self.kline_calls.append(symbol)
        if self.crash_symbol == symbol:
            raise RuntimeError("意外的解析异常")
        base = self.end_date - timedelta(days=69)
        return tuple(
            _bar(symbol, base + timedelta(days=i), i) for i in range(70))


def _bar(symbol: str, dt: datetime, i: int):
    from ascreener.models import DailyBar
    close = 10.0 + i * 0.05
    return DailyBar(symbol, dt.strftime("%Y-%m-%d"), close - 0.05, close,
                    close + 0.1, close - 0.2, 4000.0 + i)


def _monday(hour: int, minute: int) -> datetime:
    return datetime(2026, 10, 12, hour, minute, tzinfo=CN_TZ)


def test_snapshot_writes_universe_and_rows(tmp_path):
    repo = Repository(tmp_path / "t.db")
    result = sync_snapshot(repo, FakeDS(), _monday(10, 10), size=5)
    assert result.ok and result.status == "ok"
    ts, rows = repo.latest_snapshot()
    assert len(rows) == 5 and rows[0]["market_date"] == "2026-10-12"
    assert len(repo.universe_symbols()) == 5
    repo.close()


def test_snapshot_closed_day_skipped(tmp_path):
    repo = Repository(tmp_path / "t.db")
    ds = FakeDS(closed=True)
    result = sync_snapshot(repo, ds, _monday(10, 10), size=5)
    assert result.ok and result.status == "closed"
    assert repo.latest_snapshot_ts() is None  # 休市不落快照
    repo.close()


def test_snapshot_data_error(tmp_path):
    repo = Repository(tmp_path / "t.db")
    result = sync_snapshot(repo, FakeDS(fail_hot=True), _monday(10, 10), size=5)
    assert not result.ok and result.status == "error"
    repo.close()


def test_klines_empty_universe_errors(tmp_path):
    repo = Repository(tmp_path / "t.db")
    result = sync_klines(repo, FakeDS(), _monday(15, 25), sleep=lambda _s: None)
    assert result.status == "error" and "股票池为空" in result.detail
    repo.close()


def test_klines_compute_indicators(tmp_path):
    repo = Repository(tmp_path / "t.db")
    ds = FakeDS(n=4)
    sync_snapshot(repo, ds, _monday(15, 5), size=4)
    result = sync_klines(repo, ds, _monday(15, 25), sleep=lambda _s: None, pause=0)
    assert result.ok and result.status == "ok" and "4/4" in result.detail
    inds = repo.latest_indicators()
    assert set(inds) == set(repo.universe_symbols())
    sample = next(iter(inds.values()))
    assert sample["dif"] > 0 and sample["hist"] > 0  # 单调上涨
    assert len(sample["hist_series"]) == 20
    repo.close()


def test_run_due_marks_missed_slots_skipped_and_dedups(tmp_path):
    repo = Repository(tmp_path / "t.db")
    ds = FakeDS(n=3)
    # 周一 10:10：09:35、10:05 两个槽位到期
    results = run_due(repo, ds, now=_monday(10, 10), sleep=lambda _s: None)
    assert [r.status for r in results] == ["ok", "skipped"]
    # 再跑一次：槽位已记录，不重复取数
    assert run_due(repo, ds, now=_monday(10, 10), sleep=lambda _s: None) == []
    repo.close()


def test_klines_unexpected_exception_isolated_per_symbol(tmp_path):
    # 评审 HIGH：单只任意异常都不能拖垮整槽，其余股票照常出指标
    repo = Repository(tmp_path / "t.db")
    ds = FakeDS(n=4, crash_symbol="sh600002")
    sync_snapshot(repo, ds, _monday(15, 5), size=4)
    result = sync_klines(repo, ds, _monday(15, 25), sleep=lambda _s: None, pause=0)
    assert result.ok and "3/4" in result.detail
    assert set(repo.latest_indicators()) == {"sh600000", "sh600001", "sh600003"}
    repo.close()


def test_run_due_failure_occupies_slot_no_replay(tmp_path):
    # 评审 HIGH：确定性故障落库占槽，下一拍不再重放（下一槽位仍会再试）
    repo = Repository(tmp_path / "t.db")
    ds = FakeDS(fail_hot=True)
    results = run_due(repo, ds, now=_monday(10, 10), sleep=lambda _s: None)
    assert len(results) == 2 and all(r.status == "error" for r in results)
    assert run_due(repo, ds, now=_monday(10, 10), sleep=lambda _s: None) == []
    repo.close()


def test_run_due_klines_skipped_on_holiday(tmp_path):
    repo = Repository(tmp_path / "t.db")
    ds = FakeDS(n=3, closed=True)  # 快照判定休市，不会写入今日 market_date
    # 手工塞一份行情日期为 10-09（上一交易日）的快照，模拟节假日
    stale = tuple(_quote(f"sh600{i:03d}", "2026-10-09") for i in range(3))
    repo.insert_snapshot("2026-10-12T15:05:00+08:00", stale)
    results = run_due(repo, ds, now=_monday(15, 30), sleep=lambda _s: None)
    kline_jobs = [r for r in results if r.kind == "klines"]
    assert kline_jobs and kline_jobs[0].status == "closed"
    assert ds.kline_calls == []
    repo.close()


def test_klines_intraday_drops_today_partial_bar(tmp_path):
    # 盘中手动同步：当日未完成 K 剔除，指标停在上一交易日
    repo = Repository(tmp_path / "t.db")
    ds = FakeDS(n=1, end_date=datetime(2026, 10, 12))
    sync_snapshot(repo, ds, _monday(10, 5), size=1)
    result = sync_klines(repo, ds, _monday(10, 10), sleep=lambda _s: None, pause=0)
    assert result.ok
    ind = repo.latest_indicators()["sh600000"]
    assert ind["trade_date"] == "2026-10-11"
    repo.close()


def test_run_due_weekend_noop(tmp_path):
    repo = Repository(tmp_path / "t.db")
    sunday = datetime(2026, 10, 11, 10, 0, tzinfo=CN_TZ)
    assert run_due(repo, FakeDS(), now=sunday) == []
    repo.close()
