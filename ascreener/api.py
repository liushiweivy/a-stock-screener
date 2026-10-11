# -*- coding: utf-8 -*-
"""FastAPI 应用：暗色看板 + JSON API；lifespan 内置 30 秒粒度调度循环。"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import config
from .datasource import SinaTencentSource
from .models import ConfigError
from .screener import load_config, screen
from .schedule import next_kline_sync, next_snapshot
from .storage import Repository
from .sync import run_due, sync_klines, sync_snapshot

logger = logging.getLogger("ascreener.api")

_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

_SCHEDULE_TICK_S = 30


class SyncState:
    """手动/定时同步共用一把锁，防止重入。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running: str = ""  # 正在跑的类型，空=空闲


def _load_raw_config() -> dict:
    config_path = config.SCREEN_CONFIG_PATH
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def create_app(repo: Repository | None = None,
               ds: SinaTencentSource | None = None,
               scheduler=run_due) -> FastAPI:
    repo = repo or Repository(config.DB_PATH)
    ds = ds or SinaTencentSource()
    state = SyncState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()

        def scheduled_run() -> list:
            # 与手动同步共用锁：拿不到（手动在跑）就跳过本拍，下一拍再试
            if not state.lock.acquire(blocking=False):
                return []
            state.running = "scheduled"
            try:
                return scheduler(repo, ds)
            finally:
                state.running = ""
                state.lock.release()

        async def scheduler_loop() -> None:
            while not stop.is_set():
                try:
                    await run_in_threadpool(scheduled_run)
                except Exception as e:  # 循环不能因单次异常死掉
                    logger.exception("定时同步异常：%s", e)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=_SCHEDULE_TICK_S)
                except asyncio.TimeoutError:
                    pass

        task = asyncio.create_task(scheduler_loop())
        yield
        stop.set()
        task.cancel()

    app = FastAPI(title="a-stock-screener", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

    @app.middleware("http")
    async def add_security_headers(request, call_next):
        # 本机服务：状态变更接口拒绝跨站 Origin，并限 Host 为本机（防局域网页面 CSRF）
        if request.method == "POST":
            host = request.headers.get("host", "")
            origin = request.headers.get("origin")
            host_ok = host in (f"127.0.0.1:{config.PORT}",
                               f"localhost:{config.PORT}", "testserver")
            origin_ok = origin is None or origin.startswith(
                (f"http://127.0.0.1:{config.PORT}", f"http://localhost:{config.PORT}"))
            if not host_ok or not origin_ok:
                return JSONResponse({"ok": False, "error": "拒绝跨站请求"}, status_code=403)
        response = await call_next(request)
        response.headers.update(_SECURITY_HEADERS)
        return response

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(config.STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict:
        now = datetime.now(config.CN_TZ)
        snap_ts, quotes = repo.latest_snapshot()
        return {
            "status": "ok",
            "now": now.isoformat(timespec="seconds"),
            "universe_size": len(repo.universe_symbols()),
            "latest_snapshot": snap_ts,
            "snapshot_rows": len(quotes),
            "next_snapshot": next_snapshot(now).isoformat(timespec="minutes"),
            "next_klines": next_kline_sync(now).isoformat(timespec="minutes"),
            "sync_running": state.running,
            "recent_runs": repo.latest_runs(),
        }

    @app.get("/api/quotes")
    async def quotes() -> dict:
        snap_ts, rows = repo.latest_snapshot()
        indicators = repo.latest_indicators()
        try:
            cfg = load_config(config.SCREEN_CONFIG_PATH)
            cfg_error = ""
        except ConfigError as e:
            cfg, cfg_error = None, str(e)
        matches = screen(cfg, rows, indicators) if cfg else []
        match_map = {
            m.symbol: {"matched": m.matched, "reasons": m.reasons, "failed": m.failed}
            for m in matches
        }
        return {
            "ts": snap_ts,
            "config": _load_raw_config(),
            "config_error": cfg_error,
            "quotes": rows,
            "indicators": indicators,
            "matches": match_map,
        }

    @app.get("/api/config")
    async def get_config() -> dict:
        return _load_raw_config()

    @app.post("/api/sync")
    async def trigger_sync(kind: str = "snapshot"):
        if kind not in ("snapshot", "klines"):
            return JSONResponse({"ok": False, "error": "kind 只能是 snapshot/klines"},
                                status_code=400)
        if not state.lock.acquire(blocking=False):
            return JSONResponse(
                {"ok": False, "error": f"已有同步在运行（{state.running}）"}, status_code=409)
        state.running = kind
        try:
            now = datetime.now(config.CN_TZ)
            if kind == "snapshot":
                result = await run_in_threadpool(sync_snapshot, repo, ds, now)
            else:
                import time
                result = await run_in_threadpool(
                    sync_klines, repo, ds, now, time.sleep)
            repo.record_run(now, result.kind, "manual", result.ok,
                            result.status, result.detail)
            return {"ok": result.ok, "status": result.status, "detail": result.detail}
        except Exception as e:
            logger.exception("手动同步失败：%s", e)
            return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)
        finally:
            state.running = ""
            state.lock.release()

    return app
