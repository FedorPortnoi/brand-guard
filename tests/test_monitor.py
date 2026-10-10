"""Tests for monitoring: watches, alerts, scheduler."""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.monitor as monitor_module
import app.monitor_api as monitor_api_module
from app.monitor import fingerprint, run_watch

@pytest.fixture
def tmp_mon_dir(tmp_path):
    """Point monitor storage to a temp directory."""
    mon_dir = tmp_path / "monitor"
    mon_dir.mkdir()
    original_mon = monitor_module.MON_DIR
    original_api = monitor_api_module.MON_DIR
    monitor_module.MON_DIR = mon_dir
    monitor_api_module.MON_DIR = mon_dir
    yield mon_dir
    monitor_module.MON_DIR = original_mon
    monitor_api_module.MON_DIR = original_api


@pytest.fixture
def fake_run_scan():
    """Fake run_scan async generator for tests."""
    async def gen(data, brand, keywords, official_domains, image_url="", inject_demo=False, **kw):
        scan_id = "test_scan_123"
        yield "status", {"message": "Searching...", "scan_id": scan_id}
        yield "match", {
            "idx": 0,
            "page_url": "https://example.com/product",
            "image_url": "https://example.com/image.jpg",
            "title": "Product Page",
            "sha256": "hash_0",
            "risk": {"score": 75, "category": "marketplace", "host": "example.com"},
            "forensics": {"label": "Exact copy"},
        }
        yield "match", {
            "idx": 1,
            "page_url": "https://fake.com/item",
            "image_url": "https://fake.com/item.jpg",
            "title": "Fake Item",
            "sha256": "hash_1",
            "risk": {"score": 30, "category": "unknown", "host": "fake.com"},
            "forensics": {"label": "Modified"},
        }
        yield "done", {"scanned": 2, "scan_id": scan_id, "verified": 2, "unrelated": 0, "unverified": 0, "thumbnail_only": 0, "semantic": 0}
    return gen


def test_fingerprint():
    """Test fingerprint generation."""
    match_with_sha = {"sha256": "abc123"}
    assert fingerprint(match_with_sha) == "abc123"

    match_no_sha = {"page_url": "https://x.com", "image_url": "https://x.com/img.jpg"}
    fp = fingerprint(match_no_sha)
    assert len(fp) == 64  # SHA256 hex


def test_first_run_baseline(tmp_mon_dir, fake_run_scan):
    """First run creates a baseline alert and marks all seen."""
    async def run_test():
        # Setup watch
        watch = {
            "id": "w1",
            "brand": "Acme",
            "keywords": ["acme"],
            "official_domains": ["acme.com"],
            "image_url": "https://acme.com/logo.jpg",
            "interval_hours": 24,
            "min_score": 40,
            "webhook_url": "",
            "created_at": "2026-10-01T00:00:00",
            "last_run_at": None,
            "next_run_at": "2026-10-01T00:00:00",
            "last_scan_id": None,
            "running": False,
            "seen": [],
            "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")

        # Write dummy image
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy image data")

        with patch("app.monitor.run_scan", fake_run_scan):
            result = await run_watch("w1")

        assert result["watch_id"] == "w1"
        assert result["total"] == 2
        assert result["new"] == 2
        assert result["alerted"] is True

        # Check alerts
        alerts = json.loads((tmp_mon_dir / "alerts.json").read_text(encoding="utf8"))
        assert len(alerts) == 1
        assert alerts[0]["type"] == "baseline"
        assert "2 matches" in alerts[0]["message"]
        assert alerts[0]["items"] == []

        # Check seen
        watch_after = json.loads(watches_file.read_text(encoding="utf8"))["w1"]
        assert len(watch_after["seen"]) == 2
        assert "hash_0" in watch_after["seen"]
        assert "hash_1" in watch_after["seen"]
        assert watch_after["last_scan_id"] == "test_scan_123"

    asyncio.run(run_test())


def test_second_run_new_matches(tmp_mon_dir, fake_run_scan):
    """Second run detects new matches, filters by min_score."""
    async def run_test():
        watch = {
            "id": "w1",
            "brand": "Acme",
            "keywords": ["acme"],
            "official_domains": ["acme.com"],
            "image_url": "",
            "interval_hours": 24,
            "min_score": 40,
            "webhook_url": "",
            "created_at": "2026-10-01T00:00:00",
            "last_run_at": "2026-10-07T12:00:00",
            "next_run_at": "2026-10-08T12:00:00",
            "last_scan_id": "old_scan",
            "running": False,
            "seen": ["hash_0"],  # Only first match seen before
            "history": [{"at": "2026-10-07T12:00:00", "total": 2, "new": 2, "high": 1}],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy")

        with patch("app.monitor.run_scan", fake_run_scan):
            result = await run_watch("w1")

        assert result["new"] == 1  # hash_1 is new
        # hash_1 score is 30, min_score is 40, so no high matches, no alert
        assert result["alerted"] is False

        alerts = json.loads((tmp_mon_dir / "alerts.json").read_text(encoding="utf8"))
        assert len(alerts) == 0  # No alert because no high-score matches

    asyncio.run(run_test())


def test_filters_demo_and_official(tmp_mon_dir, fake_run_scan):
    """Demo and official matches are ignored."""
    async def run_test():
        async def gen_with_demo(data, brand, keywords, official_domains, image_url="", inject_demo=False, **kw):
            scan_id = "test_scan"
            yield "status", {"message": "Searching...", "scan_id": scan_id}
            yield "match", {
                "idx": 0,
                "page_url": "https://demo.x",
                "image_url": "https://demo.x/img",
                "title": "DEMO",
                "sha256": "demo_hash",
                "demo": True,  # Should be ignored
                "risk": {"score": 90, "category": "marketplace"},
                "forensics": {"label": "Demo"},
            }
            yield "match", {
                "idx": 1,
                "page_url": "https://acme.com/page",
                "image_url": "https://acme.com/img",
                "title": "Official",
                "sha256": "official_hash",
                "risk": {"score": 90, "category": "official", "host": "acme.com"},  # Should be ignored
                "forensics": {"label": "Official"},
            }
            yield "match", {
                "idx": 2,
                "page_url": "https://real-fake.com/product",
                "image_url": "https://real-fake.com/img",
                "title": "Real Fake",
                "sha256": "real_hash",
                "risk": {"score": 70, "category": "marketplace"},
                "forensics": {"label": "Altered"},
            }
            yield "done", {"scanned": 3, "scan_id": scan_id, "verified": 3, "unrelated": 0, "unverified": 0, "thumbnail_only": 0, "semantic": 0}

        watch = {
            "id": "w1",
            "brand": "Acme",
            "keywords": [],
            "official_domains": ["acme.com"],
            "image_url": "",
            "interval_hours": 24,
            "min_score": 40,
            "webhook_url": "",
            "created_at": "2026-10-01T00:00:00",
            "last_run_at": None,
            "next_run_at": "2026-10-01T00:00:00",
            "last_scan_id": None,
            "running": False,
            "seen": [],
            "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy")

        with patch("app.monitor.run_scan", gen_with_demo):
            result = await run_watch("w1")

        # Only real_hash should be processed (total=1 in baseline)
        assert result["total"] == 1
        watch_after = json.loads(watches_file.read_text(encoding="utf8"))["w1"]
        assert len(watch_after["seen"]) == 1
        assert "real_hash" in watch_after["seen"]

    asyncio.run(run_test())


def test_error_handling(tmp_mon_dir):
    """A failed scan records an error alert and clears running flag."""
    async def run_test():
        watch = {
            "id": "w1",
            "brand": "Acme",
            "keywords": [],
            "official_domains": [],
            "image_url": "",
            "interval_hours": 24,
            "min_score": 40,
            "webhook_url": "",
            "created_at": "2026-10-01T00:00:00",
            "last_run_at": None,
            "next_run_at": "2026-10-01T00:00:00",
            "last_scan_id": None,
            "running": False,
            "seen": [],
            "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        # Missing image file will cause an error

        result = await run_watch("w1")
        assert "error" in result

        # Check running flag is cleared
        watch_after = json.loads(watches_file.read_text(encoding="utf8"))["w1"]
        assert watch_after["running"] is False

        # Check error alert
        alerts = json.loads((tmp_mon_dir / "alerts.json").read_text(encoding="utf8"))
        assert len(alerts) == 1
        assert alerts[0]["type"] == "error"

    asyncio.run(run_test())


def test_error_pushes_next_run_forward(tmp_mon_dir):
    """A failed scan moves next_run_at out by the interval, so the scheduler doesn't retry every tick."""
    async def run_test():
        now = datetime.now(timezone.utc)
        due_time = datetime.fromtimestamp(now.timestamp() - 1, tz=timezone.utc).isoformat(timespec="seconds")
        watch = {
            "id": "w1", "brand": "Acme", "keywords": [], "official_domains": [], "image_url": "",
            "interval_hours": 2, "min_score": 40, "webhook_url": "",
            "created_at": "2026-10-01T00:00:00", "last_run_at": None, "next_run_at": due_time,
            "last_scan_id": None, "running": False, "seen": [], "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        # Missing image file will cause an error

        result = await run_watch("w1")
        assert "error" in result

        watch_after = json.loads(watches_file.read_text(encoding="utf8"))["w1"]
        next_run = datetime.fromisoformat(watch_after["next_run_at"]).timestamp()
        expected = now.timestamp() + 2 * 3600
        assert abs(next_run - expected) < 60

        # Further scheduler ticks right away must not re-run the failing watch
        stop = asyncio.Event()
        scheduler_task = asyncio.create_task(monitor_module.scheduler_loop(stop, tick=0.05))
        await asyncio.sleep(0.3)
        stop.set()
        await scheduler_task

        alerts = json.loads((tmp_mon_dir / "alerts.json").read_text(encoding="utf8"))
        assert len(alerts) == 1
        assert alerts[0]["type"] == "error"

    asyncio.run(run_test())


def test_scheduler(tmp_mon_dir, fake_run_scan):
    """Scheduler runs due watches one at a time."""
    async def run_test():
        # Create two watches: one due, one not
        now = datetime.now(timezone.utc)
        due_time = datetime.fromtimestamp(now.timestamp() - 1, tz=timezone.utc).isoformat(timespec="seconds")
        future_time = datetime.fromtimestamp(now.timestamp() + 3600, tz=timezone.utc).isoformat(timespec="seconds")

        watch1 = {
            "id": "w1",
            "brand": "A",
            "keywords": [],
            "official_domains": [],
            "image_url": "",
            "interval_hours": 1,
            "min_score": 40,
            "webhook_url": "",
            "created_at": "2026-10-01T00:00:00",
            "last_run_at": None,
            "next_run_at": due_time,
            "last_scan_id": None,
            "running": False,
            "seen": [],
            "history": [],
        }
        watch2 = {
            "id": "w2",
            "brand": "B",
            "keywords": [],
            "official_domains": [],
            "image_url": "",
            "interval_hours": 1,
            "min_score": 40,
            "webhook_url": "",
            "created_at": "2026-10-01T00:00:00",
            "last_run_at": None,
            "next_run_at": future_time,
            "last_scan_id": None,
            "running": False,
            "seen": [],
            "history": [],
        }

        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch1, "w2": watch2}), encoding="utf8")
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy")
        (tmp_mon_dir / "w2.img").write_bytes(b"dummy")

        stop = asyncio.Event()
        with patch("app.monitor.run_scan", fake_run_scan):
            scheduler_task = asyncio.create_task(monitor_module.scheduler_loop(stop, tick=0.1))

            # Give scheduler time to run
            await asyncio.sleep(0.5)
            stop.set()
            await scheduler_task

        # Check that w1 was run (history updated)
        watches_after = json.loads(watches_file.read_text(encoding="utf8"))
        assert len(watches_after["w1"]["history"]) > 0
        # w2 should not be run
        assert len(watches_after["w2"]["history"]) == 0

    asyncio.run(run_test())


def test_webhook_sending(tmp_mon_dir, fake_run_scan):
    """Webhook is called with correct JSON on new matches."""
    async def run_test():
        watch = {
            "id": "w1",
            "brand": "Brand",
            "keywords": [],
            "official_domains": [],
            "image_url": "",
            "interval_hours": 24,
            "min_score": 60,  # Only hash_0 (75) qualifies
            "webhook_url": "https://example.com/alert",
            "created_at": "2026-10-07T00:00:00",
            "last_run_at": "2026-10-07T12:00:00",
            "next_run_at": "2026-10-08T12:00:00",
            "last_scan_id": "old",
            "running": False,
            "seen": [],
            "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy")

        posted = []

        async def mock_post(self, url, json=None):
            posted.append((url, json))

        with patch("app.monitor.run_scan", fake_run_scan):
            with patch.object(httpx.AsyncClient, "post", mock_post):
                await run_watch("w1")

        assert len(posted) == 1
        assert posted[0][0] == "https://example.com/alert"
        assert "text" in posted[0][1]

    asyncio.run(run_test())


def test_webhook_validation(monkeypatch):
    """Webhook URL must be https:// and pass safe_url check. DNS is faked so the test stays offline."""
    import socket
    from app.net import safe_url
    hosts = {"hooks.slack.com": "34.196.0.1", "discord.com": "162.159.128.233", "localhost": "127.0.0.1",
             "127.0.0.1": "127.0.0.1", "192.168.1.1": "192.168.1.1"}

    def fake_getaddrinfo(host, *a, **k):
        if host not in hosts:
            raise socket.gaierror("unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (hosts[host], 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    # These should pass validation
    assert safe_url("https://hooks.slack.com/services/abc") is True
    assert safe_url("https://discord.com/api/webhooks/123") is True

    # URLs that should fail
    assert safe_url("http://hooks.example.com") is False  # http, not https
    assert safe_url("https://localhost:8000") is False  # private IP
    assert safe_url("https://127.0.0.1") is False  # loopback
    assert safe_url("https://192.168.1.1") is False  # private IP


def test_api_create_watch(tmp_mon_dir):
    """API: POST /api/watches creates a watch and optionally runs it."""
    from app.monitor_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    with patch("app.monitor.run_watch"):
        response = client.post(
            "/api/watches",
            data={
                "brand": "TestBrand",
                "keywords": "key1, key2",
                "official_domains": "brand.com",
                "image_url": "https://brand.com/logo.jpg",
                "interval_hours": "12",
                "min_score": "50",
                "webhook_url": "https://example.com/webhook",
            },
            files={"image": ("test.jpg", b"fake jpg data")},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["brand"] == "TestBrand"
    assert data["interval_hours"] == 12
    assert data["min_score"] == 50
    assert "seen" not in data  # seen should not be returned
    assert "id" in data


def test_api_list_watches(tmp_mon_dir):
    """API: GET /api/watches lists without seen, with seen_count."""
    from app.monitor_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Pre-populate
    watch = {
        "id": "w1",
        "brand": "A",
        "keywords": [],
        "official_domains": [],
        "image_url": "",
        "interval_hours": 24,
        "min_score": 40,
        "webhook_url": "",
        "created_at": "2026-10-01T00:00:00",
        "last_run_at": "2026-10-01T01:00:00",
        "next_run_at": "2026-10-02T01:00:00",
        "last_scan_id": "s1",
        "running": False,
        "seen": ["hash1", "hash2"],
        "history": [],
    }
    watches_file = tmp_mon_dir / "watches.json"
    watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")

    response = client.get("/api/watches")
    assert response.status_code == 200
    watches = response.json()
    assert len(watches) == 1
    assert watches[0]["id"] == "w1"
    assert watches[0]["seen_count"] == 2
    assert "seen" not in watches[0]


def test_api_delete_watch(tmp_mon_dir):
    """API: DELETE /api/watches/{id} removes watch and image."""
    from app.monitor_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    watch = {
        "id": "w1",
        "brand": "A",
        "keywords": [],
        "official_domains": [],
        "image_url": "",
        "interval_hours": 24,
        "min_score": 40,
        "webhook_url": "",
        "created_at": "2026-10-01T00:00:00",
        "last_run_at": None,
        "next_run_at": "2026-10-01T00:00:00",
        "last_scan_id": None,
        "running": False,
        "seen": [],
        "history": [],
    }
    watches_file = tmp_mon_dir / "watches.json"
    watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
    (tmp_mon_dir / "w1.img").write_bytes(b"data")

    response = client.delete("/api/watches/w1")
    assert response.status_code == 200

    # Check removed
    watches = json.loads(watches_file.read_text(encoding="utf8"))
    assert "w1" not in watches
    assert not (tmp_mon_dir / "w1.img").exists()

    # Try to delete again
    response = client.delete("/api/watches/w1")
    assert response.status_code == 404


def test_api_run_now(tmp_mon_dir):
    """API: POST /api/watches/{id}/run returns 202, or 409 if running."""
    from app.monitor_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    watch = {
        "id": "w1",
        "brand": "A",
        "keywords": [],
        "official_domains": [],
        "image_url": "",
        "interval_hours": 24,
        "min_score": 40,
        "webhook_url": "",
        "created_at": "2026-10-01T00:00:00",
        "last_run_at": None,
        "next_run_at": "2026-10-01T00:00:00",
        "last_scan_id": None,
        "running": False,
        "seen": [],
        "history": [],
    }
    watches_file = tmp_mon_dir / "watches.json"
    watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")

    with patch("app.monitor_api.run_watch"):
        response = client.post("/api/watches/w1/run")
        assert response.status_code == 202
        assert response.json()["started"] is True

    # Try when running
    watch["running"] = True
    watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
    response = client.post("/api/watches/w1/run")
    assert response.status_code == 409


def test_api_alerts_list(tmp_mon_dir):
    """API: GET /api/alerts returns alerts, newest first."""
    from app.monitor_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    alerts = [
        {
            "id": "a1",
            "watch_id": "w1",
            "brand": "A",
            "type": "baseline",
            "created_at": "2026-10-01T00:00:00",
            "message": "Baseline",
            "items": [],
            "read": False,
        },
        {
            "id": "a2",
            "watch_id": "w1",
            "brand": "A",
            "type": "new_matches",
            "created_at": "2026-10-02T00:00:00",
            "message": "New matches",
            "items": [],
            "read": False,
        },
    ]
    alerts_file = tmp_mon_dir / "alerts.json"
    alerts_file.write_text(json.dumps(alerts), encoding="utf8")

    response = client.get("/api/alerts")
    assert response.status_code == 200
    result = response.json()
    assert len(result) == 2
    assert result[0]["id"] == "a2"  # Newest first
    assert result[1]["id"] == "a1"

    # Filter unread
    alerts[0]["read"] = True
    alerts_file.write_text(json.dumps(alerts), encoding="utf8")
    response = client.get("/api/alerts?unread=1")
    result = response.json()
    assert len(result) == 1
    assert result[0]["id"] == "a2"


def test_api_alerts_mark_read(tmp_mon_dir):
    """API: POST /api/alerts/read marks alerts as read."""
    from app.monitor_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    alerts = [
        {"id": "a1", "watch_id": "w1", "brand": "A", "type": "baseline", "created_at": "2026-10-01T00:00:00", "message": "", "items": [], "read": False},
        {"id": "a2", "watch_id": "w1", "brand": "A", "type": "new_matches", "created_at": "2026-10-02T00:00:00", "message": "", "items": [], "read": False},
    ]
    alerts_file = tmp_mon_dir / "alerts.json"
    alerts_file.write_text(json.dumps(alerts), encoding="utf8")

    response = client.post("/api/alerts/read", json={"ids": ["a1"]})
    assert response.status_code == 200

    alerts_after = json.loads(alerts_file.read_text(encoding="utf8"))
    assert alerts_after[0]["read"] is True
    assert alerts_after[1]["read"] is False

    # Mark all
    response = client.post("/api/alerts/read", json={"all": True})
    alerts_after = json.loads(alerts_file.read_text(encoding="utf8"))
    assert all(a["read"] for a in alerts_after)


def test_overlapping_run_is_skipped(tmp_mon_dir):
    """Second run_watch on same watch while first is running is skipped."""
    async def run_test():
        event = asyncio.Event()
        async def gen_blocking(data, brand, keywords, official_domains, image_url="", inject_demo=False, **kw):
            yield "status", {"message": "Searching...", "scan_id": "scan1"}
            await event.wait()  # Block until test releases
            yield "match", {
                "idx": 0, "page_url": "https://x.com", "image_url": "https://x.com/img",
                "title": "T", "sha256": "hash1", "risk": {"score": 75, "category": "other"},
                "forensics": {"label": "Exact copy"},
            }
            yield "done", {"scanned": 1, "scan_id": "scan1", "verified": 1, "unrelated": 0, "unverified": 0, "thumbnail_only": 0, "semantic": 0}

        watch = {
            "id": "w1", "brand": "A", "keywords": [], "official_domains": [], "image_url": "",
            "interval_hours": 24, "min_score": 40, "webhook_url": "",
            "created_at": "2026-10-01T00:00:00", "last_run_at": None, "next_run_at": "2026-10-01T00:00:00",
            "last_scan_id": None, "running": False, "seen": [], "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy")

        with patch("app.monitor.run_scan", gen_blocking):
            task1 = asyncio.create_task(run_watch("w1"))
            await asyncio.sleep(0.1)  # Let first run start
            result2 = await run_watch("w1")  # Try second run
            event.set()  # Release first run
            result1 = await task1

        assert result2.get("skipped") == "already running"
        assert result1.get("scan_id") == "scan1"

    asyncio.run(run_test())


def test_mark_read_during_run_not_lost(tmp_mon_dir):
    """Mark-read during a run doesn't lose the write."""
    async def run_test():
        # First create an alert
        alerts_file = tmp_mon_dir / "alerts.json"
        alerts_file.write_text(json.dumps([
            {"id": "a1", "watch_id": "w1", "brand": "A", "type": "baseline",
             "created_at": "2026-10-01T00:00:00", "message": "test", "items": [], "read": False},
        ]), encoding="utf8")

        watch = {
            "id": "w1", "brand": "A", "keywords": [], "official_domains": [], "image_url": "",
            "interval_hours": 24, "min_score": 40, "webhook_url": "",
            "created_at": "2026-10-01T00:00:00", "last_run_at": "2026-10-01T00:00:00",
            "next_run_at": "2026-10-02T00:00:00",
            "last_scan_id": None, "running": False, "seen": [], "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy")

        event = asyncio.Event()
        async def gen_blocking(data, brand, keywords, official_domains, image_url="", inject_demo=False, **kw):
            yield "status", {"message": "Searching...", "scan_id": "scan1"}
            await event.wait()
            yield "match", {
                "idx": 0, "page_url": "https://x.com", "image_url": "https://x.com/img",
                "title": "T", "sha256": "hash1", "risk": {"score": 75, "category": "other"},
                "forensics": {"label": "Exact copy"},
            }
            yield "done", {"scanned": 1, "scan_id": "scan1", "verified": 1, "unrelated": 0, "unverified": 0, "thumbnail_only": 0, "semantic": 0}

        with patch("app.monitor.run_scan", gen_blocking):
            task = asyncio.create_task(run_watch("w1"))
            await asyncio.sleep(0.1)
            # Mark existing alert read while run is in progress
            await monitor_module.mark_alerts_read(alert_ids={"a1"})
            event.set()
            result = await task

        # Check that old alert is still marked read and new alert was added
        alerts = json.loads((tmp_mon_dir / "alerts.json").read_text(encoding="utf8"))
        assert len(alerts) == 2
        assert alerts[0]["id"] == "a1"
        assert alerts[0]["read"] is True
        assert alerts[1]["type"] == "new_matches"

    asyncio.run(run_test())


def test_delete_watch_during_run_not_resurrected(tmp_mon_dir, fake_run_scan):
    """Deleting a watch during a run doesn't resurrect it."""
    async def run_test():
        watch = {
            "id": "w1", "brand": "A", "keywords": [], "official_domains": [], "image_url": "",
            "interval_hours": 24, "min_score": 40, "webhook_url": "",
            "created_at": "2026-10-01T00:00:00", "last_run_at": None, "next_run_at": "2026-10-01T00:00:00",
            "last_scan_id": None, "running": False, "seen": [], "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy")

        event = asyncio.Event()
        async def gen_blocking(data, brand, keywords, official_domains, image_url="", inject_demo=False, **kw):
            yield "status", {"message": "Searching...", "scan_id": "scan1"}
            await event.wait()
            yield "match", {
                "idx": 0, "page_url": "https://x.com", "image_url": "https://x.com/img",
                "title": "T", "sha256": "hash1", "risk": {"score": 75, "category": "other"},
                "forensics": {"label": "Exact copy"},
            }
            yield "done", {"scanned": 1, "scan_id": "scan1", "verified": 1, "unrelated": 0, "unverified": 0, "thumbnail_only": 0, "semantic": 0}

        with patch("app.monitor.run_scan", gen_blocking):
            task = asyncio.create_task(run_watch("w1"))
            await asyncio.sleep(0.1)
            # Delete watch while run is in progress
            await monitor_module.delete_watch("w1")
            event.set()
            result = await task

        # Check that watch is still deleted
        watches = json.loads((tmp_mon_dir / "watches.json").read_text(encoding="utf8"))
        assert "w1" not in watches

    asyncio.run(run_test())


def test_run_now_query_param(tmp_mon_dir):
    """POST /api/watches?run_now=1 starts a background run."""
    from app.monitor_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    with patch("app.monitor_api.run_watch"):
        response = client.post(
            "/api/watches?run_now=1",
            data={
                "brand": "Brand",
                "keywords": "",
                "official_domains": "",
                "image_url": "",
                "interval_hours": "24",
                "min_score": "40",
                "webhook_url": "",
            },
            files={"image": ("test.jpg", b"data")},
        )

    assert response.status_code == 200


def test_next_run_at_is_iso_string(tmp_mon_dir, fake_run_scan):
    """next_run_at is always ISO-8601 string after a run."""
    async def run_test():
        watch = {
            "id": "w1", "brand": "A", "keywords": [], "official_domains": [], "image_url": "",
            "interval_hours": 24, "min_score": 40, "webhook_url": "",
            "created_at": "2026-10-01T00:00:00", "last_run_at": None, "next_run_at": "2026-10-01T00:00:00",
            "last_scan_id": None, "running": False, "seen": [], "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy")

        with patch("app.monitor.run_scan", fake_run_scan):
            result = await run_watch("w1")

        watches_after = json.loads(watches_file.read_text(encoding="utf8"))
        next_run = watches_after["w1"]["next_run_at"]
        # Should be a string in ISO format
        assert isinstance(next_run, str)
        # Should be parseable as ISO datetime
        datetime.fromisoformat(next_run)

    asyncio.run(run_test())


def test_stop_timeout_5s(tmp_mon_dir):
    """stop() waits up to 5s for scheduler and cancels if still running."""
    async def run_test():
        event = asyncio.Event()
        async def gen_slow(data, brand, keywords, official_domains, image_url="", inject_demo=False, **kw):
            yield "status", {"message": "Searching...", "scan_id": "scan1"}
            await event.wait()  # Will never complete in this test
            yield "done", {"scanned": 0, "scan_id": "scan1", "verified": 0, "unrelated": 0, "unverified": 0, "thumbnail_only": 0, "semantic": 0}

        watch = {
            "id": "w1", "brand": "A", "keywords": [], "official_domains": [], "image_url": "",
            "interval_hours": 24, "min_score": 40, "webhook_url": "",
            "created_at": "2026-10-01T00:00:00", "last_run_at": None, "next_run_at": "2026-10-01T00:00:00",
            "last_scan_id": None, "running": False, "seen": [], "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy")

        with patch("app.monitor.run_scan", gen_slow):
            # Start scheduler
            task = monitor_module.start()
            await asyncio.sleep(0.1)  # Let scheduler detect the due watch
            # Stop should complete within ~5s
            import time
            start = time.time()
            await monitor_module.stop()
            elapsed = time.time() - start
            assert elapsed < 10  # Should be around 5s, definitely less than 10

    asyncio.run(run_test())


def test_pluralize_messages(tmp_mon_dir):
    """Messages are properly pluralized."""
    async def run_test():
        # Test with 1 match (singular)
        async def gen_one(data, brand, keywords, official_domains, image_url="", inject_demo=False, **kw):
            yield "status", {"message": "Searching...", "scan_id": "scan1"}
            yield "match", {
                "idx": 0, "page_url": "https://x.com", "image_url": "https://x.com/img",
                "title": "T", "sha256": "h1", "risk": {"score": 75, "category": "other"},
                "forensics": {"label": "Exact copy"},
            }
            yield "done", {"scanned": 1, "scan_id": "scan1", "verified": 1, "unrelated": 0, "unverified": 0, "thumbnail_only": 0, "semantic": 0}

        watch = {
            "id": "w1", "brand": "Brand", "keywords": [], "official_domains": [], "image_url": "",
            "interval_hours": 24, "min_score": 40, "webhook_url": "",
            "created_at": "2026-10-01T00:00:00", "last_run_at": None, "next_run_at": "2026-10-01T00:00:00",
            "last_scan_id": None, "running": False, "seen": [], "history": [],
        }
        watches_file = tmp_mon_dir / "watches.json"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")
        (tmp_mon_dir / "w1.img").write_bytes(b"dummy")

        with patch("app.monitor.run_scan", gen_one):
            await run_watch("w1")

        alerts = json.loads((tmp_mon_dir / "alerts.json").read_text(encoding="utf8"))
        assert "Baseline recorded: 1 match" in alerts[0]["message"]

        # Test with 2 matches (plural)
        watch["running"] = False
        watch["seen"] = ["h1"]
        watch["last_run_at"] = "2026-10-01T00:00:00"
        watches_file.write_text(json.dumps({"w1": watch}), encoding="utf8")

        async def gen_two(data, brand, keywords, official_domains, image_url="", inject_demo=False, **kw):
            yield "status", {"message": "Searching...", "scan_id": "scan2"}
            yield "match", {
                "idx": 0, "page_url": "https://x.com", "image_url": "https://x.com/img",
                "title": "T", "sha256": "h1", "risk": {"score": 75, "category": "other"},
                "forensics": {"label": "Exact copy"},
            }
            yield "match", {
                "idx": 1, "page_url": "https://y.com", "image_url": "https://y.com/img",
                "title": "T2", "sha256": "h2", "risk": {"score": 75, "category": "other"},
                "forensics": {"label": "Modified"},
            }
            yield "done", {"scanned": 2, "scan_id": "scan2", "verified": 2, "unrelated": 0, "unverified": 0, "thumbnail_only": 0, "semantic": 0}

        with patch("app.monitor.run_scan", gen_two):
            await run_watch("w1")

        alerts = json.loads((tmp_mon_dir / "alerts.json").read_text(encoding="utf8"))
        assert "1 new likely copy of Brand since" in alerts[1]["message"]
