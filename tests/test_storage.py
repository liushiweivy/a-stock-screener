# -*- coding: utf-8 -*-
"""SQLite 仓储往返。"""
from ascreener.models import DailyBar, Indicators, Quote
from ascreener.storage import Repository


def _quote(symbol: str, name: str, amount: float) -> Quote:
    return Quote(symbol=symbol, code=symbol[2:], name=name,
                 price=10.0, prev_close=9.9, open=9.95, high=10.2, low=9.8,
                 pct=1.01, volume=1_000_000, amount=amount, turnover_rate=2.1,
                 volume_ratio=1.3, amplitude=4.0, market_date="2026-10-12",
                 tick_time="20261012100500")


def test_universe_and_snapshot_roundtrip(tmp_path):
    repo = Repository(tmp_path / "t.db")
    qs = (_quote("sh600519", "贵州茅台", 4e9),
          _quote("sz300308", "中际旭创", 2e10))
    repo.upsert_universe(qs, "2026-10-12T10:05:00+08:00")
    repo.insert_snapshot("2026-10-12T10:05:00+08:00", qs)

    # rank 按入参顺序（新浪接口已按成交额降序返回）
    assert repo.universe_symbols() == ["sh600519", "sz300308"]
    ts, rows = repo.latest_snapshot()
    assert ts == "2026-10-12T10:05:00+08:00"
    assert rows[0]["name"] == "贵州茅台" and rows[0]["rank"] == 1
    assert {r["symbol"] for r in rows} == {"sh600519", "sz300308"}
    assert rows[0]["volume_ratio"] == 1.3
    repo.close()


def test_bars_and_indicators(tmp_path):
    repo = Repository(tmp_path / "t.db")
    # 指标查询 INNER JOIN universe：先建池
    repo.upsert_universe((_quote("sh600519", "贵州茅台", 4e9),), "2026-10-12T15:25:00+08:00")
    bars = tuple(
        DailyBar("sh600519", f"2026-09-{d:02d}", 10 + d * 0.1, 10 + d * 0.11,
                 10 + d * 0.12, 10 + d * 0.09, 3000.0 + d)
        for d in range(1, 20)
    )
    repo.upsert_bars(bars)
    dates, closes, volumes = repo.daily_closes_volumes("sh600519")
    assert len(dates) == 19 and closes[-1] > closes[0]

    ind = Indicators("sh600519", dates[-1], closes[-1], 0.12, 0.08, 0.08,
                     3100.0, 3050.0, ((-0.01, -0.02, 0.02), (0.12, 0.08, 0.08)))
    repo.upsert_indicator(ind, "2026-10-12T15:25:00+08:00")
    got = repo.latest_indicators()["sh600519"]
    assert got["hist"] == 0.08
    assert got["hist_series"][-1] == [0.12, 0.08, 0.08]
    repo.close()


def test_run_dedup_by_slot(tmp_path):
    repo = Repository(tmp_path / "t.db")
    from datetime import datetime
    from ascreener.config import CN_TZ
    now = datetime(2026, 10, 12, 10, 5, tzinfo=CN_TZ)
    assert repo.ran_slot_recorded("2026-10-12", "snapshot", "09:35") is False
    repo.record_run(now, "snapshot", "09:35", True, "ok", "100 只")
    assert repo.ran_slot_recorded("2026-10-12", "snapshot", "09:35") is True
    assert repo.ran_slot_recorded("2026-10-12", "snapshot", "10:05") is False
    assert repo.latest_runs()[0]["status"] == "ok"
    repo.close()


def test_failed_slot_also_counts_as_recorded(tmp_path):
    # 评审 HIGH：失败也占槽，避免确定性故障每 30s 重放
    repo = Repository(tmp_path / "t.db")
    from datetime import datetime
    from ascreener.config import CN_TZ
    now = datetime(2026, 10, 12, 9, 35, tzinfo=CN_TZ)
    repo.record_run(now, "snapshot", "09:35", False, "error", "boom")
    assert repo.ran_slot_recorded("2026-10-12", "snapshot", "09:35") is True
    repo.close()


def test_latest_market_date(tmp_path):
    repo = Repository(tmp_path / "t.db")
    assert repo.latest_market_date() is None
    qs = (_quote("sh600519", "贵州茅台", 4e9),)
    repo.insert_snapshot("2026-10-12T09:35:00+08:00", qs)
    assert repo.latest_market_date() == "2026-10-12"
    repo.close()
