# -*- coding: utf-8 -*-
"""安装/卸载开机自启：启动文件夹放纯 ASCII VBS → 隐藏窗口跑 pythonw launch.py。

wscript 按 ANSI 解释 .vbs，因此脚本内容必须全 ASCII（路径已固定在英文目录）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from ascreener import config

_STARTUP = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" \
    / "Programs" / "Startup"
_VBS_PATH = _STARTUP / "a-stock-screener.vbs"
_LAUNCH = config.BASE_DIR / "launch.py"


def vbs_text() -> str:
    exe = config.resolve_pythonw()
    workdir = str(config.BASE_DIR)
    for label, value in ("pythonw", exe), ("项目目录", workdir):
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            raise SystemExit(
                f"{label}含非 ASCII 字符（{value}），VBS 会乱码，请把项目移到纯英文路径")
    return (
        "' a-stock-screener autostart (auto-generated, do not edit)\r\n"
        "Set sh = CreateObject(\"Wscript.Shell\")\r\n"
        f"sh.CurrentDirectory = \"{workdir}\"\r\n"
        f"sh.Run \"\"\"{exe}\"\" \"\"{_LAUNCH}\"\"\", 0, False\r\n"
    )


def install() -> None:
    _STARTUP.mkdir(parents=True, exist_ok=True)
    _VBS_PATH.write_text(vbs_text(), encoding="ascii", newline="\r\n")
    print(f"已安装开机自启：{_VBS_PATH}")
    print("开机登录后会静默起服务并自动打开浏览器；卸载运行 uninstall-autostart.bat")


def uninstall() -> None:
    if _VBS_PATH.exists():
        _VBS_PATH.unlink()
        print(f"已删除：{_VBS_PATH}")
    else:
        print("未安装，无需卸载")


def main() -> int:
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install()
        print(f"提示：Python 升级换目录后需重跑本脚本（当前 {config.resolve_pythonw()}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
