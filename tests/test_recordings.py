"""The replay list: which recorded scans exist and what they found, so a demo can start from a strong one."""
import json

from fastapi.testclient import TestClient

import app.main as main


def _rec(path, brand, rows):
    events = [["status", {"message": "x"}]] + [["match", r] for r in rows] + [["done", {"scanned": 9}]]
    path.write_text(json.dumps({"brand": brand, "original": "", "events": events, "evidence": {}}), encoding="utf8")


def _row(score, category="unauthorized_use", verified=True, demo=False):
    return {"idx": 0, "forensics": {"kind": "exact"} if verified else None, "demo": demo,
            "risk": {"score": score, "category": category, "host": "x.example"}}


def test_recordings_list_counts_only_other_peoples_verified_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    (tmp_path / "scans").mkdir()
    _rec(tmp_path / "demo.json", "Adidas", [_row(80, demo=True), _row(40), _row(0, "official")])
    _rec(tmp_path / "last_scan.json", "Lucky Fours", [_row(0, "official"), _row(5, "authorised_reseller"),
                                                       _row(30, "unverified", verified=False), _row(60)])
    _rec(tmp_path / "scans" / "20261010-120000-a.json", "A", [])
    _rec(tmp_path / "scans" / "20261010-130000-b.json", "B", [_row(70)])
    (tmp_path / "scans" / "20261010-140000-broken.json").write_text("{", encoding="utf8")
    got = TestClient(main.app).get("/api/recordings").json()
    assert [(r["name"], r["brand"], r["flagged"], r["high"], r["demo"]) for r in got] == [
        ("demo", "Adidas", 2, 1, True),
        ("last_scan", "Lucky Fours", 1, 0, False),
        ("scans/20261010-130000-b", "B", 1, 1, False),
        ("scans/20261010-120000-a", "A", 0, 0, False),
    ]


def test_demo_recording_replays(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    _rec(tmp_path / "demo.json", "Adidas", [_row(80)])
    body = TestClient(main.app).post("/api/replay", data={"name": "demo"}).text
    assert "event: original" in body and "event: match" in body and "event: done" in body
