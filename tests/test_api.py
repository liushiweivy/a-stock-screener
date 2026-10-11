# -*- coding: utf-8 -*-
"""Web API：健康、快照+指标+匹配、手动同步、安全头。"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from ascreener import config
from ascreener.api import create_app
from ascreener.config import CN_TZ
from ascreener.storage import Repository
from ascreener.sync import sync_klines, sync_snapshot
from tests.test_sync import FakeDS  # 复用假数据源


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SCREEN_CONFIG_PATH", tmp_path / "cfg.json")
    repo = Repository(tmp_path / "t.db")
    ds = FakeDS(n=5)
    now = datetime(2026, 10, 12, 15, 25, tzinfo=CN_TZ)
    sync_snapshot(repo, ds, now, size=5)
    sync_klines(repo, ds, now, sleep=lambda _s: None, pause=0)
    return repo, ds


def _client(repo, ds) -> TestClient:
    return TestClient(create_app(repo=repo, ds=ds, scheduler=lambda r, d: None))


def test_health(seeded):
    repo, ds = seeded
    with _client(repo, ds) as client:
        body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["universe_size"] == 5
    assert body["latest_snapshot"] and body["snapshot_rows"] == 5
    assert "next_snapshot" in body and "next_klines" in body


def test_quotes_payload_has_indicators_and_matches(seeded):
    repo, ds = seeded
    with _client(repo, ds) as client:
        body = client.get("/api/quotes").json()
    assert len(body["quotes"]) == 5
    assert body["config"]["name"]  # 缺省配置自动生成
    assert len(body["indicators"]) == 5
    match_entry = next(iter(body["matches"].values()))
    assert set(match_entry) == {"matched", "reasons", "failed"}


def test_manual_snapshot(seeded):
    repo, ds = seeded
    with _client(repo, ds) as client:
        resp = client.post("/api/sync?kind=snapshot")
        assert resp.json()["ok"] is True
        bad = client.post("/api/sync?kind=nope")
        assert bad.status_code == 400


def test_manual_sync_rejects_cross_origin(seeded):
    repo, ds = seeded
    with _client(repo, ds) as client:
        resp = client.post("/api/sync?kind=snapshot",
                           headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 403


def test_security_headers(seeded):
    repo, ds = seeded
    with _client(repo, ds) as client:
        resp = client.get("/api/health")
    assert "content-security-policy" in {k.lower() for k in resp.headers}
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
