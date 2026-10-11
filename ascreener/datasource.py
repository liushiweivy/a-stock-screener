# -*- coding: utf-8 -*-
"""免费数据源：新浪（热度排行快照）+ 腾讯（批量行情增强 / 前复权日 K），新浪 K 线兜底。

为什么不用 akshare/东方财富：push2.eastmoney.com 在本机对 Python HTTP 客户端
（requests/httpx/curl_cffi 均验证过）直接掐断 TLS 连接，curl 偶发可用但限流明显；
腾讯 qt.gtimg.cn / ifzq.gtimg.cn 与新浪 vip.stock 从 Python 直连稳定。
所有解析函数为纯函数，网络层可在测试中整体替换。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from . import config
from .models import DailyBar, Quote

logger = logging.getLogger("ascreener.datasource")

_SINA_RANK_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData?page=1&num={size}&sort=amount&asc=0&node=hs_a"
)
_SINA_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={count}"
)
_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={symbols}"
_TENCENT_KLINE_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
    "param={symbol},day,,,{count},qfq"
)

_SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn",
}
_TENCENT_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TENCENT_BATCH = 60  # 单次批量行情代码数，保守值避免 URL 过长


class DataError(RuntimeError):
    """数据源请求或解析失败。"""


def _to_float(value: object) -> Optional[float]:
    """空串/None → None，其余转 float；非法值抛 DataError（上游字段变更应尽早暴露）。"""
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as e:
        raise DataError(f"无法解析为数字：{value!r}") from e


# ----------------------------------------------------------------------------
# 纯解析函数
# ----------------------------------------------------------------------------

def parse_sina_rank(text: str) -> tuple[Quote, ...]:
    """解析新浪 hs_a 按成交额排序的快照 JSON。volume 单位股，nmc 单位万元。"""
    try:
        rows = json.loads(text)
    except json.JSONDecodeError as e:
        raise DataError(f"新浪排行返回非合法 JSON：{text[:120]}") from e
    if not isinstance(rows, list):
        raise DataError("新浪排行返回结构异常：根节点不是数组")

    quotes: list[Quote] = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        code = str(row.get("code", ""))
        if not symbol or not code:
            continue
        nmc_wan = _to_float(row.get("nmc"))  # 流通市值（万元）
        # 新浪排行 volume 对所有板块都是「股」（实测：茅台 351 万、寒武纪 1137 万）
        volume = _to_float(row.get("volume")) or 0.0
        quotes.append(Quote(
            symbol=symbol,
            code=code,
            name=str(row.get("name", "")),
            price=_to_float(row.get("trade")) or 0.0,
            prev_close=_to_float(row.get("settlement")) or 0.0,
            open=_to_float(row.get("open")) or 0.0,
            high=_to_float(row.get("high")) or 0.0,
            low=_to_float(row.get("low")) or 0.0,
            pct=_to_float(row.get("changepercent")) or 0.0,
            volume=volume,
            amount=_to_float(row.get("amount")) or 0.0,
            turnover_rate=_to_float(row.get("turnoverratio")) or 0.0,
            pe=_to_float(row.get("per")),
            pb=_to_float(row.get("pb")),
            circ_mv=nmc_wan * 10000 if nmc_wan is not None else None,
            tick_time=str(row.get("ticktime", "")),
        ))
    return tuple(quotes)


@dataclass(frozen=True)
class TencentQuote:
    """腾讯批量行情中快照接口没有的增强字段。"""

    volume_ratio: Optional[float]   # 量比
    amplitude: Optional[float]      # 振幅 %
    market_date: Optional[str]      # YYYY-MM-DD
    tick_time: str                  # YYYYMMDDHHMMSS


def parse_tencent_quotes(text: str) -> dict[str, TencentQuote]:
    """解析 qt.gtimg.cn 的 GBK 多行 v_xxx="~分隔" 文本。"""
    result: dict[str, TencentQuote] = {}
    for chunk in text.split(";"):
        if "=\"" not in chunk:
            continue
        head, body = chunk.split("=\"", 1)
        symbol = head.strip().split("_", 1)[-1]
        fields = body.rstrip("\"").split("~")
        if len(fields) < 50 or not fields[1]:
            continue
        raw_dt = fields[30]
        market_date = (
            f"{raw_dt[0:4]}-{raw_dt[4:6]}-{raw_dt[6:8]}" if len(raw_dt) >= 8 else None
        )
        result[symbol] = TencentQuote(
            volume_ratio=_to_float(fields[49]),
            amplitude=_to_float(fields[43]),
            market_date=market_date,
            tick_time=raw_dt,
        )
    return result


def parse_tencent_kline(symbol: str, payload: dict) -> tuple[DailyBar, ...]:
    """解析腾讯 fqkline：data[symbol] 下 qfqday 或 day，每行 [日期,开,收,高,低,量]。

    量单位：主板/创业板为手；科创板 688/689 为股，此处 /100 归一化为手。
    """
    node = (payload.get("data") or {}).get(symbol) or {}
    rows = node.get("qfqday")
    if rows is None:
        rows = node.get("day", [])
    if not isinstance(rows, list):
        raise DataError(f"腾讯 K 线结构异常：{symbol}")
    bars: list[DailyBar] = []
    # 实测坑：腾讯日 K 主板/创业板量是「手」，科创板（688/689）却是「股」
    # （寒武纪 11,367,966 与新浪股数完全相等）；科创板 /100 统一归一化为手
    is_star = symbol.startswith(("sh688", "sh689"))
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            volume = float(row[5]) / (100.0 if is_star else 1.0)
            bars.append(DailyBar(
                symbol=symbol,
                date=str(row[0]),
                open=float(row[1]),
                close=float(row[2]),
                high=float(row[3]),
                low=float(row[4]),
                volume=volume,
            ))
        except (TypeError, ValueError) as e:
            raise DataError(f"腾讯 K 线单行数值非法：{str(row)[:100]}") from e
    return tuple(bars)


def parse_sina_kline(symbol: str, text: str) -> tuple[DailyBar, ...]:
    """解析新浪 getKLineData：每行 day/open/high/low/close/volume（股），量换算成手。"""
    try:
        rows = json.loads(text)
    except json.JSONDecodeError as e:
        raise DataError(f"新浪 K 线返回非合法 JSON：{text[:120]}") from e
    if not isinstance(rows, list):
        raise DataError(f"新浪 K 线结构异常：根节点是 {type(rows).__name__}")
    required = ("day", "open", "high", "low", "close", "volume")
    bars: list[DailyBar] = []
    for row in rows:
        if not isinstance(row, dict) or any(k not in row for k in required):
            raise DataError(f"新浪 K 线单行字段缺失：{str(row)[:100]}")
        try:
            bars.append(DailyBar(
                symbol=symbol,
                date=str(row["day"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]) / 100.0,
            ))
        except (TypeError, ValueError) as e:
            raise DataError(f"新浪 K 线单行数值非法：{str(row)[:100]}") from e
    return tuple(bars)


def merge_enrichment(
    quotes: tuple[Quote, ...], extra: dict[str, TencentQuote]
) -> tuple[Quote, ...]:
    """把腾讯增强字段并进新浪快照（不可变替换）。"""
    enriched: list[Quote] = []
    for q in quotes:
        tq = extra.get(q.symbol)
        if tq is None:
            enriched.append(q)
        else:
            enriched.append(Quote(
                **{**q.__dict__,
                   "volume_ratio": tq.volume_ratio,
                   "amplitude": tq.amplitude,
                   "market_date": tq.market_date,
                   "tick_time": tq.tick_time or q.tick_time}))
    return tuple(enriched)


# ----------------------------------------------------------------------------
# 网络层
# ----------------------------------------------------------------------------

class SinaTencentSource:
    """免费数据源客户端。session/get 可注入，便于测试与以后替换付费源。"""

    def __init__(
        self,
        timeout: float = config.HTTP_TIMEOUT_S,
        kline_pause: float = config.KLINE_PAUSE_S,
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._timeout = timeout
        self._kline_pause = kline_pause
        self._session = session or requests.Session()
        self._sleep = sleep

    def _get(self, url: str, headers: dict[str, str], *, encoding: Optional[str] = None) -> str:
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self._session.get(url, headers=headers, timeout=self._timeout)
                resp.raise_for_status()
                if encoding:
                    resp.encoding = encoding
                return resp.text
            except requests.RequestException as e:
                last_err = e
                logger.warning("GET 失败（第 %s 次）%s: %s", attempt + 1, url[:80], e)
                self._sleep(0.8 * (attempt + 1))
        raise DataError(f"请求三次均失败：{url[:100]}；{last_err}")

    def hot_quotes(self, size: int = config.UNIVERSE_SIZE) -> tuple[Quote, ...]:
        """成交额排名前 size 的 A 股快照（新浪一次请求），腾讯批量补量比。"""
        text = self._get(_SINA_RANK_URL.format(size=size), _SINA_HEADERS)
        quotes = parse_sina_rank(text)
        if not quotes:
            raise DataError("新浪排行返回 0 条记录")
        quotes = self._enrich(quotes)
        return quotes

    def _enrich(self, quotes: tuple[Quote, ...]) -> tuple[Quote, ...]:
        symbols = [q.symbol for q in quotes]
        extra: dict[str, TencentQuote] = {}
        for i in range(0, len(symbols), _TENCENT_BATCH):
            batch = symbols[i:i + _TENCENT_BATCH]
            url = _TENCENT_QUOTE_URL.format(symbols=",".join(batch))
            try:
                text = self._get(url, _TENCENT_HEADERS, encoding="gbk")
                extra.update(parse_tencent_quotes(text))
            except DataError as e:
                # 增强失败不应阻断主链路：量比/行情日期留空
                logger.warning("腾讯行情增强失败，本批 %s 只缺量比：%s", len(batch), e)
        return merge_enrichment(quotes, extra)

    def daily_bars(self, symbol: str,
                   count: int = config.KLINE_COUNT,
                   min_bars: int = 50) -> tuple[DailyBar, ...]:
        """前复权日 K：腾讯优先，新浪（不复权）兜底。

        腾讯对个别代码会只回寥寥几根（接口异常或次新股），低于 min_bars 也转兜底；
        两边都短（真次新股）才返回短序列，交由上层决定是否可算指标。
        """
        short: tuple[DailyBar, ...] = ()
        try:
            text = self._get(
                _TENCENT_KLINE_URL.format(symbol=symbol, count=count),
                _TENCENT_HEADERS,
            )
            payload = json.loads(text)
            bars = parse_tencent_kline(symbol, payload)
            if len(bars) >= min_bars:
                return bars
            if bars:
                logger.warning("腾讯 K 线仅 %s 根（<%s），转新浪兜底：%s",
                               len(bars), min_bars, symbol)
                short = bars
            else:
                logger.warning("腾讯 K 线为空，转新浪兜底：%s", symbol)
        except (DataError, json.JSONDecodeError) as e:
            logger.warning("腾讯 K 线失败，转新浪兜底：%s %s", symbol, e)
        text = self._get(
            _SINA_KLINE_URL.format(symbol=symbol, count=count), _SINA_HEADERS
        )
        bars = parse_sina_kline(symbol, text)
        if bars:
            return bars
        if short:
            return short
        raise DataError(f"{symbol} 腾讯/新浪日 K 均为空")
