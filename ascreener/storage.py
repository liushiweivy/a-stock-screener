# -*- coding: utf-8 -*-
"""SQLite 存储：股票池、半小时快照、日 K、指标、同步运行记录。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import DailyBar, Indicators, Quote

_SCHEMA = """
CREATE TABLE IF NOT EXISTS universe (
    symbol     TEXT PRIMARY KEY,
    code       TEXT NOT NULL,
    name       TEXT NOT NULL,
    amount     REAL,
    rank       INTEGER,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    ts            TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    name          TEXT,
    price         REAL,
    prev_close    REAL,
    open          REAL,
    high          REAL,
    low           REAL,
    pct           REAL,
    volume        REAL,
    amount        REAL,
    turnover_rate REAL,
    volume_ratio  REAL,
    amplitude     REAL,
    pe            REAL,
    pb            REAL,
    circ_mv       REAL,
    market_date   TEXT,
    tick_time     TEXT,
    PRIMARY KEY (ts, symbol)
);
CREATE TABLE IF NOT EXISTS daily_kline (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL,
    close  REAL,
    high   REAL,
    low    REAL,
    volume REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS indicators (
    symbol      TEXT PRIMARY KEY,
    trade_date  TEXT NOT NULL,
    close       REAL,
    dif         REAL,
    dea         REAL,
    hist        REAL,
    vol_ma5     REAL,
    vol_ma20    REAL,
    hist_series TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_runs (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    kind   TEXT NOT NULL,      -- snapshot / klines
    slot   TEXT,               -- HH:MM，对齐交易时段槽位
    ok     INTEGER NOT NULL,
    status TEXT NOT NULL,      -- ok / closed / error
    detail TEXT
);
"""


class Repository:
    """所有读写集中在此；连接用 WAL，check_same_thread=False 以便 API 线程池访问。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- 股票池 -----------------------------------------------------------

    def upsert_universe(self, quotes: tuple[Quote, ...], ts: str) -> None:
        """按本次成交额排名刷新股票池（新成员记 first_seen，老成员更新排名）。"""
        rows = [
            (q.symbol, q.code, q.name, q.amount, i + 1, ts)
            for i, q in enumerate(quotes)
        ]
        self._conn.executemany(
            """
            INSERT INTO universe(symbol, code, name, amount, rank, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name, amount=excluded.amount,
                rank=excluded.rank, last_seen=excluded.last_seen
            """,
            [(r[0], r[1], r[2], r[3], r[4], r[5], r[5]) for r in rows],
        )

    def universe_symbols(self) -> list[str]:
        cur = self._conn.execute("SELECT symbol FROM universe ORDER BY rank")
        return [row[0] for row in cur.fetchall()]

    # -- 快照 -------------------------------------------------------------

    def insert_snapshot(self, ts: str, quotes: tuple[Quote, ...]) -> None:
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO snapshots(
                ts, symbol, name, price, prev_close, open, high, low, pct,
                volume, amount, turnover_rate, volume_ratio, amplitude,
                pe, pb, circ_mv, market_date, tick_time)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [(ts, q.symbol, q.name, q.price, q.prev_close, q.open, q.high, q.low,
              q.pct, q.volume, q.amount, q.turnover_rate, q.volume_ratio,
              q.amplitude, q.pe, q.pb, q.circ_mv, q.market_date, q.tick_time)
             for q in quotes],
        )

    def latest_snapshot_ts(self) -> Optional[str]:
        row = self._conn.execute("SELECT MAX(ts) AS ts FROM snapshots").fetchone()
        return row["ts"] if row and row["ts"] else None

    def latest_snapshot(self) -> tuple[Optional[str], list[dict]]:
        ts = self.latest_snapshot_ts()
        if ts is None:
            return None, []
        cur = self._conn.execute(
            """
            SELECT s.*, u.rank AS rank
            FROM snapshots s LEFT JOIN universe u ON u.symbol = s.symbol
            WHERE s.ts = ? ORDER BY u.rank
            """,
            (ts,),
        )
        return ts, [dict(row) for row in cur.fetchall()]

    # -- 日 K -------------------------------------------------------------

    def upsert_bars(self, bars: tuple[DailyBar, ...]) -> None:
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO daily_kline(symbol, date, open, close, high, low, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [(b.symbol, b.date, b.open, b.close, b.high, b.low, b.volume) for b in bars],
        )

    def daily_closes_volumes(self, symbol: str) -> tuple[list[str], list[float], list[float]]:
        cur = self._conn.execute(
            "SELECT date, close, volume FROM daily_kline WHERE symbol=? ORDER BY date",
            (symbol,),
        )
        dates, closes, volumes = [], [], []
        for row in cur.fetchall():
            dates.append(row["date"])
            closes.append(row["close"])
            volumes.append(row["volume"])
        return dates, closes, volumes

    # -- 指标 -------------------------------------------------------------

    def upsert_indicator(self, ind: Indicators, updated_at: str) -> None:
        hist_series = json.dumps(ind.history, ensure_ascii=False)
        self._conn.execute(
            """
            INSERT INTO indicators(
                symbol, trade_date, close, dif, dea, hist, vol_ma5, vol_ma20,
                hist_series, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                trade_date=excluded.trade_date, close=excluded.close,
                dif=excluded.dif, dea=excluded.dea, hist=excluded.hist,
                vol_ma5=excluded.vol_ma5, vol_ma20=excluded.vol_ma20,
                hist_series=excluded.hist_series, updated_at=excluded.updated_at
            """,
            (ind.symbol, ind.trade_date, ind.close, ind.dif, ind.dea, ind.hist,
             ind.vol_ma5, ind.vol_ma20, hist_series, updated_at),
        )

    def latest_indicators(self) -> dict[str, dict]:
        # 只返回当前股票池成员，避免池子滚动后载荷无限增长（评审 LOW）
        cur = self._conn.execute(
            "SELECT i.symbol, i.trade_date, i.close, i.dif, i.dea, i.hist, "
            "i.vol_ma5, i.vol_ma20, i.hist_series "
            "FROM indicators i JOIN universe u ON u.symbol = i.symbol"
        )
        out: dict[str, dict] = {}
        for row in cur.fetchall():
            item = dict(row)
            item["hist_series"] = json.loads(item["hist_series"])
            out[item["symbol"]] = item
        return out

    # -- 运行记录 ---------------------------------------------------------

    def record_run(
        self, ts: datetime, kind: str, slot: str, ok: bool, status: str, detail: str = ""
    ) -> None:
        self._conn.execute(
            "INSERT INTO sync_runs(ts, kind, slot, ok, status, detail) "
            "VALUES (?,?,?,?,?,?)",
            (ts.isoformat(timespec="seconds"), kind, slot, 1 if ok else 0, status, detail),
        )

    def ran_slot_recorded(self, date: str, kind: str, slot: str) -> bool:
        """该槽位今天是否已跑过（成功失败都算；失败不每 30s 重放，下一槽位再试）。"""
        row = self._conn.execute(
            "SELECT 1 FROM sync_runs WHERE substr(ts,1,10)=? AND kind=? AND slot=? "
            "LIMIT 1",
            (date, kind, slot),
        ).fetchone()
        return row is not None

    def latest_market_date(self) -> Optional[str]:
        """最近一次快照里腾讯给出的行情交易日（节假日判定用）。"""
        row = self._conn.execute(
            "SELECT MAX(market_date) AS d FROM snapshots "
            "WHERE ts=(SELECT MAX(ts) FROM snapshots)"
        ).fetchone()
        return row["d"] if row else None

    def latest_runs(self, limit: int = 6) -> list[dict]:
        cur = self._conn.execute(
            "SELECT ts, kind, slot, ok, status, detail FROM sync_runs "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
