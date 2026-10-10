"""Tests for Wayback Machine archiving and its effect on drafts and reports, all offline."""
import asyncio

import httpx
from fastapi.testclient import TestClient

from app import archive, archive_api, report, takedown
from app.main import app
from app.store import SESSIONS


def _snap(url, handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await archive.snapshot(url, c)
    return asyncio.run(go())


def test_anonymous_save():
    def handler(request):
        assert request.url.path == "/save/https://shop.example/p"
        return httpx.Response(302, headers={"location": "https://web.archive.org/web/20261010122945/https://shop.example/p"})
    res = _snap("https://shop.example/p", handler)
    assert res == {"state": "saved", "url": "https://web.archive.org/web/20261010122945/https://shop.example/p",
                   "captured_at": "2026-10-10T12:29:45+00:00"}


def test_rate_limited_save_falls_back_to_existing_snapshot():
    def handler(request):
        if request.url.path.startswith("/save/"):
            return httpx.Response(429)
        assert request.url.path == "/cdx/search/cdx"
        return httpx.Response(200, json=[["timestamp", "original"], ["20250101000000", "https://shop.example/p"],
                                         ["20261001000000", "https://shop.example/p"]])
    res = _snap("https://shop.example/p", handler)
    assert res["state"] == "existing"
    assert res["url"] == "https://web.archive.org/web/20261001000000/https://shop.example/p"
    assert "HTTP 429" in res["error"]


def test_failed_with_no_snapshot():
    def handler(request):
        if request.url.path.startswith("/save/"):
            return httpx.Response(429)
        return httpx.Response(200, json=[])
    res = _snap("https://shop.example/p", handler)
    assert res == {"state": "failed", "error": "HTTP 429"}


def test_authenticated_save(monkeypatch):
    monkeypatch.setenv("IA_ACCESS_KEY", "acc")
    monkeypatch.setenv("IA_SECRET_KEY", "sec")
    polls = []

    async def no_sleep(_):
        return None
    monkeypatch.setattr(archive.asyncio, "sleep", no_sleep)

    def handler(request):
        assert request.headers["Authorization"] == "LOW acc:sec"
        if request.method == "POST":
            return httpx.Response(200, json={"job_id": "job1"})
        polls.append(1)
        if len(polls) < 2:
            return httpx.Response(200, json={"status": "pending"})
        return httpx.Response(200, json={"status": "success", "timestamp": "20261010130000", "original_url": "https://shop.example/p"})
    res = _snap("https://shop.example/p", handler)
    assert res["state"] == "saved" and res["url"].endswith("/web/20261010130000/https://shop.example/p")
    assert len(polls) == 2


def _session():
    match = {"idx": 0, "page_url": "https://shop.example/p", "image_url": "https://shop.example/a.jpg", "title": "Stan Smith",
             "seller": "cheap_kicks", "price": "19.99 USD", "captured_at": "2026-10-10T12:00:00+00:00", "sha256": "ab" * 32,
             "verification": "verified", "forensics": None,
             "risk": {"score": 70, "category": "marketplace", "reasons": ["Third-party marketplace listing"], "host": "shop.example"}}
    demo = {**match, "idx": 1, "demo": True}
    SESSIONS["arc1"] = {"brand": "Adidas", "original": b"", "official_domains": [], "items": {0: {"match": match, "evidence": None},
                                                                                              1: {"match": demo, "evidence": None}}}
    return match


def test_archive_endpoint_stores_result_and_drafts_include_it(monkeypatch):
    match = _session()

    async def fake_snapshot(url, client=None):
        return {"state": "saved", "url": f"https://web.archive.org/web/20261010122945/{url}", "captured_at": "2026-10-10T12:29:45+00:00"}
    monkeypatch.setattr(archive_api.archive, "snapshot", fake_snapshot)
    try:
        r = TestClient(app).post("/api/archive/arc1", json={"idxs": [0, 1]})
        assert r.status_code == 200
        res = r.json()["results"]
        assert res["0"]["state"] == "saved" and res["1"]["state"] == "skipped"
        assert match["archive"]["url"].endswith("/https://shop.example/p")

        body = takedown._build_notice_body("copyright", [(0, match)], "Adidas", {})
        assert "Archived copy (Wayback Machine, 2026-10-10T12:29:45+00:00): https://web.archive.org/web/" in body
        assert "Seller: cheap_kicks" in body
        pdf = report.build("Adidas", b"", None, match)
        assert pdf.startswith(b"%PDF")
    finally:
        SESSIONS.pop("arc1", None)


def test_archive_endpoint_validation():
    _session()
    try:
        c = TestClient(app)
        assert c.post("/api/archive/nope", json={"idxs": [0]}).status_code == 404
        assert c.post("/api/archive/arc1", json={"idxs": []}).status_code == 400
        assert c.post("/api/archive/arc1", json={"idxs": [99]}).status_code == 404
        assert c.post("/api/archive/arc1", json={"idxs": list(range(21))}).status_code == 400
    finally:
        SESSIONS.pop("arc1", None)
