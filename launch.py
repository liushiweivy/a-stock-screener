# -*- coding: utf-8 -*-
"""幂等启动器：后台 pythonw 拉起 uvicorn（含定时调度）→ 健康检查 → 开浏览器。

双击 / 开机自启都走这里；服务已在跑时只打开页面。全程无控制台窗口。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from ascreener import config

PAGE_URL = f"http://{config.HOST}:{config.PORT}/"
HEALTH_URL = f"http://{config.HOST}:{config.PORT}/api/health"
_LOG_LIMIT = 1 * 1024 * 1024


def is_healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1) as resp:
            return resp.status == 200
    except OSError:
        return False


def rotate_log(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > _LOG_LIMIT:
            old = path.with_suffix(".log.1")
            if old.exists():
                old.unlink()
            path.rename(old)
    except OSError:
        # 旧服务可能仍占着文件句柄；轮转失败不阻断启动（追加写即可）
        pass


def start_server() -> None:
    rotate_log(config.SERVER_LOG)
    log = open(config.SERVER_LOG, "ab")
    creationflags = 0x08 | 0x200 | 0x08000000  # DETACHED_PROCESS|NEW_PROCESS_GROUP|NO_WINDOW
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    subprocess.Popen(
        [config.resolve_pythonw(), "-X", "utf8", "-m", "uvicorn",
         "ascreener.api:create_app", "--factory",
         "--host", config.HOST, "--port", str(config.PORT)],
        cwd=str(config.BASE_DIR), stdout=log, stderr=log,
        stdin=subprocess.DEVNULL, env=env, creationflags=creationflags,
        close_fds=True,
    )


def wait_until_healthy(seconds: int = 25) -> bool:
    for _ in range(seconds):
        if is_healthy():
            return True
        time.sleep(1)
    return False


def alert(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, message, "a-stock-screener 启动失败", 0x10)


def main() -> int:
    if is_healthy():
        webbrowser.open(PAGE_URL, new=2)
        return 0
    start_server()
    if wait_until_healthy():
        webbrowser.open(PAGE_URL, new=2)
        return 0
    alert(f"服务 25 秒内未就绪，见日志：{config.SERVER_LOG}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
