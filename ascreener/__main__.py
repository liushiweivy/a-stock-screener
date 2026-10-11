# -*- coding: utf-8 -*-
"""命令行入口：python -m ascreener {sync|screen|serve}"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config
from .datasource import SinaTencentSource
from .models import ConfigError
from .screener import load_config, screen
from .storage import Repository
from .sync import sync_klines, sync_snapshot


def _cmd_sync(args: argparse.Namespace) -> int:
    repo = Repository(config.DB_PATH)
    ds = SinaTencentSource()
    now = datetime.now(config.CN_TZ)
    slot = "manual"
    results = []
    if args.snapshot or args.all:
        if args.ignore_closed:
            # 初始化/调试用：跳过休市判定直接取数（周末拿到的是上一交易日收盘快照）
            from . import sync as sync_mod
            sync_mod._is_market_closed = lambda quotes, today: False
        results.append(sync_snapshot(repo, ds, now, slot=slot))
    if args.klines or args.all:
        results.append(sync_klines(repo, ds, now, sleep=time.sleep, slot=slot))
    for r in results:
        print(f"[{r.kind}] {r.status}: {r.detail}")
        repo.record_run(now, r.kind, slot, r.ok, r.status, r.detail)
    repo.close()
    return 0 if all(r.ok for r in results) else 1


def _cmd_screen(args: argparse.Namespace) -> int:
    repo = Repository(config.DB_PATH)
    cfg_path = Path(args.config) if args.config else config.SCREEN_CONFIG_PATH
    cfg = load_config(cfg_path)
    _, quotes = repo.latest_snapshot()
    if not quotes:
        repo.close()
        print("数据库里还没有快照，先运行：python -m ascreener sync --all --ignore-closed")
        return 1
    indicators = repo.latest_indicators()
    matches = screen(cfg, quotes, indicators)
    hit = [m for m in matches if m.matched]
    print(f"条件组：{cfg.name}")
    print(f"股票池 {len(quotes)} 只，命中 {len(hit)} 只：")
    for m in hit:
        print(f"  {m.code} {m.name}  [{'；'.join(m.reasons)}]")
    if args.export:
        _export_csv(Path(args.export), hit, quotes, indicators)
        print(f"已导出：{args.export}")
    repo.close()
    return 0


def _export_csv(path: Path, matches, quotes, indicators) -> None:
    by_symbol = {q["symbol"]: q for q in quotes}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["代码", "名称", "现价", "涨跌幅%", "换手率%", "量比",
                         "成交额(亿)", "DIF", "DEA", "HIST", "命中条件"])
        for m in matches:
            q = by_symbol[m.symbol]
            ind = indicators.get(m.symbol, {})
            writer.writerow([
                m.code, m.name, q.get("price"), q.get("pct"),
                q.get("turnover_rate"), q.get("volume_ratio"),
                round(q["amount"] / 1e8, 2),
                ind.get("dif"), ind.get("dea"), ind.get("hist"),
                "；".join(m.reasons),
            ])


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run("ascreener.api:create_app", factory=True,
                host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ascreener", description="A股数据同步与条件筛选")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="立即同步一次")
    p_sync.add_argument("--snapshot", action="store_true", help="只同步行情快照")
    p_sync.add_argument("--klines", action="store_true", help="只补日K并重算指标")
    p_sync.add_argument("--all", action="store_true", help="快照 + 日K（初始化用）")
    p_sync.add_argument("--ignore-closed", action="store_true",
                        help="忽略休市判定（周末初始化/调试）")
    p_sync.set_defaults(func=_cmd_sync)

    p_screen = sub.add_parser("screen", help="按条件筛选并输出命中股票")
    p_screen.add_argument("config", nargs="?", help="筛选条件 JSON 路径（默认 data/screen_config.json）")
    p_screen.add_argument("--export", help="命中结果导出 CSV 路径")
    p_screen.set_defaults(func=_cmd_screen)

    p_serve = sub.add_parser("serve", help="启动 Web 看板（含内置定时调度）")
    p_serve.add_argument("--host", default=config.HOST)
    p_serve.add_argument("--port", type=int, default=int(config.PORT))
    p_serve.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    if args.cmd == "sync" and not (args.snapshot or args.klines or args.all):
        args.snapshot = True
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
