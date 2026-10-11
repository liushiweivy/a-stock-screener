# -*- coding: utf-8 -*-
"""全局配置：环境变量优先，缺省走本机个人默认值。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"

# Web 服务
HOST = os.getenv("AS_HOST", "127.0.0.1")
PORT = int(os.getenv("AS_PORT", "8772"))

# 同步参数
UNIVERSE_SIZE = int(os.getenv("AS_UNIVERSE_SIZE", "100"))   # 热门股池数量（按成交额排名）
KLINE_COUNT = int(os.getenv("AS_KLINE_COUNT", "200"))      # 每只拉取的日 K 根数（MACD 暖机）
HTTP_TIMEOUT_S = float(os.getenv("AS_HTTP_TIMEOUT", "10"))
KLINE_PAUSE_S = float(os.getenv("AS_KLINE_PAUSE", "0.15")) # 逐只拉日 K 的节流间隔

# 存储 / 配置
def resolve_pythonw() -> str:
    """同目录 pythonw.exe（无窗口后台解释器），缺失则回退当前解释器。

    launch.py 与 install_autostart.py 共用，Python 升级换目录后重跑安装脚本即可。
    """
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else sys.executable


DB_PATH = DATA_DIR / "ascreener.db"
SCREEN_CONFIG_PATH = DATA_DIR / "screen_config.json"
SERVER_LOG = DATA_DIR / "server.log"

# A 股交易时区（机器通常就是东八区，显式固定避免漂移）
from datetime import timezone, timedelta  # noqa: E402

CN_TZ = timezone(timedelta(hours=8))

DATA_DIR.mkdir(parents=True, exist_ok=True)
