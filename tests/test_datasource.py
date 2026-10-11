# -*- coding: utf-8 -*-
"""数据源纯解析 + 合并；网络层用假 Session 验证重试与兜底。"""
import json

import pytest
import requests

from ascreener.datasource import (DataError, SinaTencentSource, merge_enrichment,
                                  parse_sina_kline, parse_sina_rank,
                                  parse_tencent_kline, parse_tencent_quotes)
from ascreener.models import Quote

# ---------------------------------------------------------------------------
# 样本（字段形态按 2026-10-11 真实抓包裁剪）
# ---------------------------------------------------------------------------

SINA_RANK = json.dumps([
    {"symbol": "sz300308", "code": "300308", "name": "中际旭创", "trade": "775.290",
     "pricechange": -7.71, "changepercent": -0.985, "open": "775.640",
     "high": "780.000", "low": "730.000", "settlement": "783.000",
     "volume": 27933721, "amount": 21091586583, "ticktime": "15:35:00",
     "per": 79.111, "pb": 22.911, "nmc": 86052305.9},
    {"symbol": "sh600519", "code": "600519", "name": "贵州茅台", "trade": "1263.000",
     "pricechange": 7.21, "changepercent": 0.57, "open": "1255.420",
     "high": "1282.000", "low": "1255.420", "settlement": "1255.790",
     "volume": 3511100, "amount": 4453223189, "ticktime": "15:35:00",
     "per": 19.39, "pb": 6.28, "nmc": 157885300.0},
], ensure_ascii=False)


def _tencent_line(symbol: str, fields: list[str]) -> str:
    return f'v_{symbol}="' + "~".join(fields) + '";'


def _maotai_line() -> str:
    f = [""] * 68
    f[0] = "1"; f[1] = "贵州茅台"; f[2] = "600519"; f[3] = "1263.00"
    f[4] = "1255.79"; f[5] = "1255.42"; f[6] = "35111"
    f[30] = "20261009161459"; f[31] = "7.21"; f[32] = "0.57"
    f[33] = "1282.00"; f[34] = "1255.42"; f[37] = "445322"
    f[38] = "0.28"; f[39] = "19.39"; f[43] = "2.12"
    f[44] = "15788.53"; f[46] = "6.28"; f[49] = "1.18"
    return _tencent_line("sh600519", f)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def test_parse_sina_rank_fields():
    rows = parse_sina_rank(SINA_RANK)
    assert len(rows) == 2
    top = rows[0]
    assert top.symbol == "sz300308" and top.name == "中际旭创"
    assert top.pct == pytest.approx(-0.985)
    assert top.amount == 21091586583
    assert top.circ_mv == pytest.approx(86052305.9 * 10000)
    assert top.pe and top.pb


def test_parse_sina_rank_bad_json():
    with pytest.raises(DataError):
        parse_sina_rank("not-json")
    with pytest.raises(DataError):
        parse_sina_rank('{"x":1}')


def test_parse_sina_rank_volume_always_shares():
    # 实测：新浪排行对所有板块（含科创板）volume 都返回「股」，不做任何换算
    # （寒武纪 sh688256 当日 11,367,966 股，与新浪日 K 股数一致）
    star = json.dumps([{"symbol": "sh688256", "code": "688256", "name": "寒武纪",
                        "trade": "985.50", "settlement": "980.50", "open": "980",
                        "high": "990", "low": "975", "changepercent": 0.51,
                        "volume": 11367966, "amount": 10864000000,
                        "turnoverratio": 1.81, "per": 0, "pb": 0, "nmc": 0}])
    rows = parse_sina_rank(star)
    assert rows[0].volume == 11_367_966


def test_parse_tencent_quotes():
    extra = parse_tencent_quotes(_maotai_line())
    q = extra["sh600519"]
    assert q.volume_ratio == 1.18
    assert q.amplitude == 2.12
    assert q.market_date == "2026-10-09"
    assert q.tick_time == "20261009161459"


def test_merge_enrichment():
    quotes = parse_sina_rank(SINA_RANK)
    extra = parse_tencent_quotes(_maotai_line())
    merged = {q.symbol: q for q in merge_enrichment(quotes, extra)}
    assert merged["sh600519"].volume_ratio == 1.18
    assert merged["sh600519"].market_date == "2026-10-09"
    assert merged["sz300308"].volume_ratio is None  # 未增强的保持原样


def test_parse_tencent_kline():
    payload = {"code": 0, "data": {"sh600519": {"qfqday": [
        ["2026-06-01", "1298.976", "1281.576", "1298.976", "1273.286", "43845.000"],
        ["2026-06-02", "1283.000", "1290.500", "1295.000", "1280.100", "30120.000"],
    ]}}}
    bars = parse_tencent_kline("sh600519", payload)
    assert bars[0].date == "2026-06-01"
    assert bars[0].open == 1298.976 and bars[0].close == 1281.576
    assert bars[0].high == 1298.976 and bars[0].low == 1273.286
    assert bars[0].volume == 43845.0  # 手


def test_parse_tencent_kline_star_market_shares_to_lots():
    # 实测坑：腾讯日 K 科创板量返回「股」（11,367,966 与新浪股数相同），
    # 解析层 /100 归一化为手；主板返回的本来就是手，保持不变
    payload = {"code": 0, "data": {"sh688256": {"qfqday": [
        ["2026-10-09", "979.100", "985.990", "998.500", "916.200", "11367966.000"],
    ]}}}
    bars = parse_tencent_kline("sh688256", payload)
    assert bars[0].volume == 113_679.66


def test_parse_sina_kline_unit_conversion():
    text = json.dumps([{"day": "2026-09-28", "open": "1236.000", "high": "1244.010",
                        "low": "1228.100", "close": "1243.880", "volume": "2821830"}])
    bars = parse_sina_kline("sh600519", text)
    assert bars[0].close == 1243.88
    assert bars[0].volume == 28218.3  # 股 → 手


def test_parse_sina_kline_bad_shapes():
    # 评审 HIGH：上游返回对象而非数组、缺字段、数值非法都要抛 DataError
    with pytest.raises(DataError):
        parse_sina_kline("sh600519", '{"day": "x"}')
    with pytest.raises(DataError):
        parse_sina_kline("sh600519", '[{"day": "2026-09-28"}]')
    with pytest.raises(DataError):
        parse_sina_kline("sh600519", '[{"day":"d","open":"x","high":"1",'
                                      '"low":"1","close":"1","volume":"100"}]')


def test_parse_tencent_kline_bad_node():
    with pytest.raises(DataError):
        parse_tencent_kline("sh600519", {"data": {"sh600519": {"qfqday": {}}}})


# ---------------------------------------------------------------------------
# 网络层（假 Session）
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, text: str, encoding: str = "utf-8") -> None:
        self.text = text
        self.encoding = encoding

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, routes: dict, failures: int = 0,
                 fail_keys: tuple[str, ...] = ()) -> None:
        self._routes = routes
        self._failures_left = failures
        self._fail_keys = fail_keys
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        if self._failures_left > 0 and (not self._fail_keys
                                        or any(k in url for k in self._fail_keys)):
            self._failures_left -= 1
            raise requests.ConnectionError("reset by peer")
        for key, text in self._routes.items():
            if key in url:
                return _Resp(text, "gbk" if "qt.gtimg" in url else "utf-8")
        raise AssertionError(f"未注册的 URL：{url}")


def test_hot_quotes_end_to_end_with_fake_session():
    ds = SinaTencentSource(session=_FakeSession(
        {"getHQNodeData": SINA_RANK, "qt.gtimg.cn": _maotai_line()}),
        sleep=lambda _s: None)
    quotes = ds.hot_quotes(size=2)
    assert len(quotes) == 2
    mt = {q.symbol: q for q in quotes}
    assert mt["sh600519"].volume_ratio == 1.18
    assert mt["sh600519"].market_date == "2026-10-09"


def test_enrichment_failure_does_not_break_snapshot():
    # 腾讯行情连续三次失败 → 快照仍返回，量比为 None
    ds = SinaTencentSource(session=_FakeSession(
        {"getHQNodeData": SINA_RANK}, failures=9,
        fail_keys=("qt.gtimg.cn",)), sleep=lambda _s: None)
    quotes = ds.hot_quotes(size=2)
    assert quotes[0].volume_ratio is None


def test_hot_quotes_retries_then_raises():
    ds = SinaTencentSource(session=_FakeSession(
        {}, failures=9, fail_keys=("getHQNodeData",)), sleep=lambda _s: None)
    with pytest.raises(DataError):
        ds.hot_quotes(size=2)


def test_daily_bars_tencent_then_sina_fallback():
    tencent_payload = json.dumps({"data": {"sh600519": {}}})
    sina_text = json.dumps([{"day": "2026-09-28", "open": "1", "high": "2",
                             "low": "0.9", "close": "1.5", "volume": "10000"}])
    ds = SinaTencentSource(session=_FakeSession(
        {"fqkline": tencent_payload, "getKLineData": sina_text}),
        sleep=lambda _s: None)
    bars = ds.daily_bars("sh600519", count=200)
    assert bars[0].date == "2026-09-28" and bars[0].volume == 100.0
