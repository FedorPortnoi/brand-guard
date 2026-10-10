"""Every recorded scan is also kept in data/scans/, so a new scan never destroys a good one."""
from fastapi.testclient import TestClient

from app import main


def test_record_keeps_history_and_replays_it(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "SCAN_HISTORY", 3)
    sess = {"brand": "Adidas Originals", "original": b"\xff\xd8jpeg", "official_domains": [], "items": {}}
    for i in range(5):
        (tmp_path / "scans").mkdir(exist_ok=True)
        for old in (tmp_path / "scans").glob("*.json"):  # distinct timestamps without sleeping
            old.rename(old.with_name(f"0{i}{old.name}"))
        main._record(sess, [("status", {"message": f"scan {i}"}), ("done", {"scanned": i})])
    files = sorted(p.name for p in (tmp_path / "scans").glob("*.json"))
    assert len(files) == 3 and all(f.endswith("-adidas-originals.json") for f in files)
    assert (tmp_path / "last_scan.json").exists()

    stem = files[-1][:-5]
    r = TestClient(main.app).post("/api/replay", data={"name": f"scans/{stem}"})
    assert r.status_code == 200 and "event: done" in r.text
    assert TestClient(main.app).post("/api/replay", data={"name": "scans/missing"}).status_code == 404
