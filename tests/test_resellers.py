"""Authorised resellers: low risk, kept out of the Sellers tab, alerts and text-only rows."""
import asyncio
import io

from fastapi.testclient import TestClient
from PIL import Image

from app import monitor, monitor_api, pipeline, risk, search, sellers
from app.main import app

F = {"kind": "exact", "label": "Exact / re-compressed copy", "changed_pct": 0, "c2pa": {"state": "not_present"},
     "ai_signal": {"level": "none", "hints": []}}


def test_parse_domains():
    assert risk.parse_domains(" Target.com, www.BestBuy.com,,  ") == ["target.com", "bestbuy.com"]


def test_reseller_scores_low_and_official_still_wins():
    r = risk.assess("https://www.target.com/p/airpods", "Apple AirPods Pro 2 - replica?", ["apple.com"], F,
                    resellers=["target.com"])
    assert r == {"category": "authorised_reseller", "score": 5, "host": "target.com",
                 "reasons": ["Domain is on your authorised resellers list", "Exact / re-compressed copy"]}
    r = risk.assess("https://store.apple.com/x", "AirPods", ["apple.com"], F, resellers=["apple.com"])
    assert r["category"] == "official"
    r = risk.assess("https://shop.example.com/x", "AirPods replica", ["apple.com"], F, resellers=["target.com"])
    assert r["category"] == "likely_counterfeit"


def test_reseller_never_gets_a_text_only_row():
    assert risk.context_only("https://www.amazon.com/x", "AirPods wholesale replica", [], ["amazon.com"]) is None
    assert risk.context_only("https://www.amazon.com/x", "AirPods wholesale replica", [], []) is not None


def test_resellers_left_out_of_sellers_tab():
    ms = [{"idx": 0, "page_url": "https://www.target.com/p/1", "risk": {"score": 5, "category": "authorised_reseller"}},
          {"idx": 1, "page_url": "https://fake.example.shop/p/2", "risk": {"score": 60, "category": "unknown"}}]
    assert [g["name"] for g in sellers.group_matches(ms)] == ["example.shop"]


def test_pipeline_applies_resellers(monkeypatch):
    b = io.BytesIO(); Image.effect_noise((256, 256), 60).convert("RGB").save(b, "JPEG", quality=90)
    img = b.getvalue()
    cands = [{"image_url": "demo://1", "page_url": "https://www.target.com/p/1", "title": "item", "source": "t", "blob": img},
             {"image_url": "demo://2", "page_url": "https://other.example.com/p/2", "title": "item", "source": "t", "blob": img}]

    async def fake_discover(*a, **k): return cands
    async def none(*a, **k): return []
    monkeypatch.setattr(search, "discover", fake_discover)
    monkeypatch.setattr(search, "reverse_candidates", none)

    async def go():
        return [e async for e in pipeline.run_scan(img, "Acme", [], [], resellers=["target.com"])]
    by_host = {d["risk"]["host"]: d["risk"] for e, d in asyncio.run(go()) if e == "match"}
    assert by_host["target.com"]["category"] == "authorised_reseller"
    assert by_host["other.example.com"]["category"] != "authorised_reseller"


def test_watch_stores_resellers(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "MON_DIR", tmp_path)
    monkeypatch.setattr(monitor_api, "MON_DIR", tmp_path)
    r = TestClient(app).post("/api/watches", files={"image": ("a.jpg", b"\xff\xd8x", "image/jpeg")},
                             data={"brand": "Acme", "official_domains": "acme.com", "resellers": "Target.com, www.bestbuy.com"})
    assert r.status_code in (200, 201)
    w = r.json().get("watch", r.json())
    assert w["resellers"] == ["target.com", "bestbuy.com"]
