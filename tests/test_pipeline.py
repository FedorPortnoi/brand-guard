"""End-to-end scan pipeline, offline: discovery is faked, verification is real."""
import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image

from app import pipeline, search, semantic, store

REAL = Path(__file__).parent.parent / "data" / "real_eval"


def _jpg(img, q=85):
    b = io.BytesIO(); img.convert("RGB").save(b, "JPEG", quality=q); return b.getvalue()


def _run(data, cands, monkeypatch, **kw):
    async def fake_discover(*a, **k): return cands
    async def no_reverse(*a, **k): return []
    monkeypatch.setattr(search, "discover", fake_discover)
    monkeypatch.setattr(search, "reverse_candidates", no_reverse)

    async def go():
        return [e async for e in pipeline.run_scan(data, "Coca-Cola", [], [], **kw)]
    return asyncio.run(go())


def _cand(name, blob, page):
    return {"image_url": f"demo://{name}", "page_url": page, "title": name, "source": "test", "blob": blob}


def _photos():
    a, b = REAL / "bottle__coca_cola_bottle_0.jpg", REAL / "bottle__coca_cola_bottle_1.jpg"
    unrelated = REAL / "watch__casio_wristwatch_0.jpg"
    if not (a.exists() and b.exists() and unrelated.exists()):
        pytest.skip("real evaluation photos not downloaded (data/real_eval)")
    return a.read_bytes(), b.read_bytes(), unrelated.read_bytes()


def test_pixel_copy_matches_and_unrelated_does_not(monkeypatch):
    orig, _, unrelated = _photos()
    crop = Image.open(io.BytesIO(orig)); w, h = crop.size
    cands = [_cand("copy", _jpg(crop.crop((0, 0, int(w * .9), int(h * .9)))), "https://shop.example.xyz/p/1"),
             _cand("other", unrelated, "https://watches.example.com/casio")]
    ev = _run(orig, cands, monkeypatch)
    matches = [d for e, d in ev if e == "match"]
    done = [d for e, d in ev if e == "done"][0]
    assert [m["title"] for m in matches] == ["copy"]
    assert done["unrelated"] == 1 and done["semantic"] == 0
    assert set(store.SESSIONS[done["scan_id"]]["items"]) == {0}


def test_semantic_pass_finds_same_product_other_photo(monkeypatch):
    if not semantic.enabled():
        pytest.skip("CLIP dependencies not installed")
    orig, other_photo, unrelated = _photos()
    cands = [_cand("same product", other_photo, "https://bottles.example.shop/coke"),
             _cand("other", unrelated, "https://watches.example.com/casio")]
    off = [d for e, d in _run(orig, cands, monkeypatch) if e == "match"]
    assert off == []  # a different photo is not a pixel copy
    ev = _run(orig, cands, monkeypatch, semantic_search=True)
    matches = [d for e, d in ev if e == "match"]
    done = [d for e, d in ev if e == "done"][0]
    assert [m["title"] for m in matches] == ["same product"]
    f = matches[0]["forensics"]
    assert f["kind"] == "same_product" and f["similarity"] >= semantic.SEMANTIC_MIN
    assert f["c2pa"]["state"] in ("not_present", "valid", "invalid", "trusted", "error", "unknown")
    assert done["semantic"] == 1 and done["unrelated"] == 1


def test_lookalikes_reputation_and_canonical_url(monkeypatch):
    """PhishStats hosts become a lookalikes event and raise risk; AliExpress affiliate links are swapped for item URLs."""
    from app import marketplaces, reputation
    orig, _, _ = _photos()
    crop = Image.open(io.BytesIO(orig)); w, h = crop.size
    copy = _jpg(crop.crop((0, 0, int(w * .9), int(h * .9))))
    phish = [{"host": "coca-cola-promo.club", "domain": "coca-cola-promo.club", "reports": 3, "last_seen": "2026-10-01",
              "max_score": 7.0, "brand_in_domain": True, "country": None}]

    async def fake_phish(*a, **k): return phish
    async def fake_canonical(client, url):
        return "https://www.aliexpress.com/item/42.html" if url and "s.click.aliexpress.com" in url else url
    monkeypatch.setattr(reputation, "phishstats_reports", fake_phish)
    monkeypatch.setattr(marketplaces, "canonical_page_url", fake_canonical)
    cands = [_cand("lookalike", copy, "https://coca-cola-promo.club/win"),
             {**_cand("ali", copy, "https://s.click.aliexpress.com/e/_x"), "image_url": "demo://ali", "seller": "Fizz Store"}]
    ev = _run(orig, cands, monkeypatch)
    look = [d for e, d in ev if e == "lookalikes"]
    assert look and look[0]["domains"][0]["host"] == "coca-cola-promo.club"
    by = {m["title"]: m for e, m in ev if e == "match"}
    assert any("PhishStats" in r for r in by["lookalike"]["risk"]["reasons"])
    assert by["ali"]["page_url"] == "https://www.aliexpress.com/item/42.html"
    assert by["ali"]["seller"] == "Fizz Store"
    assert not any("PhishStats" in r for r in by["ali"]["risk"]["reasons"])


def test_unverified_rows_collapse_per_page_and_sources_are_counted(monkeypatch):
    """Several blocked images on one suspicious page give one unverified row; done reports candidates per source."""
    from app import net
    orig, _, _ = _photos()

    async def blocked(client, url, referer=None): return None, 403
    monkeypatch.setattr(net, "fetch_image", blocked)
    cands = [{"image_url": f"https://replica.example.shop/{i}.jpg", "page_url": "https://replica.example.shop/p",
              "title": "replica sneakers", "source": "page-crawl"} for i in range(4)]
    cands.append({"image_url": "https://other.example.shop/x.jpg", "page_url": "https://other.example.shop/q",
                  "title": "fake sneakers", "source": "brave-api:q"})
    ev = _run(orig, cands, monkeypatch)
    rows = [d for e, d in ev if e == "match"]
    assert sorted(r["page_url"] for r in rows) == ["https://other.example.shop/q", "https://replica.example.shop/p"]
    done = [d for e, d in ev if e == "done"][0]
    assert done["sources"] == {"page-crawl": 4, "brave-api": 1}


def test_one_same_product_row_per_page(monkeypatch):
    """Two photos of the same product on one page give one row (the closer one); another page still gets its own."""
    if not semantic.enabled():
        pytest.skip("CLIP dependencies not installed")
    orig, other_photo, _ = _photos()
    flipped = _jpg(Image.open(io.BytesIO(other_photo)).transpose(Image.FLIP_LEFT_RIGHT))
    cands = [{**_cand("photo A", other_photo, "https://shop.example.com/coke"), "image_url": "demo://a"},
             {**_cand("photo B", flipped, "https://shop.example.com/coke"), "image_url": "demo://b"},
             {**_cand("other shop", other_photo, "https://another.example.net/coke"), "image_url": "demo://c"}]
    matches = [d for e, d in _run(orig, cands, monkeypatch, semantic_search=True) if e == "match"]
    pages = [m["page_url"] for m in matches]
    assert sorted(pages) == ["https://another.example.net/coke", "https://shop.example.com/coke"]
