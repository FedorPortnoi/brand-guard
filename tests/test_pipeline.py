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
    assert done["verified"] == 1
    assert set(store.SESSIONS[done["scan_id"]]["items"]) == {0}


def test_old_sessions_are_dropped(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(store, "SESSIONS", {})
    monkeypatch.setattr("app.main.SESSIONS", store.SESSIONS)
    for i in range(store.MAX_SESSIONS + 3):
        store.add_session(f"s{i}", {"original": b"jpeg", "items": {}})
    assert len(store.SESSIONS) == store.MAX_SESSIONS
    assert "s0" not in store.SESSIONS and f"s{store.MAX_SESSIONS + 2}" in store.SESSIONS
    c = TestClient(app)
    assert c.get("/api/original/s0.jpg").status_code == 404
    assert c.get(f"/api/original/s{store.MAX_SESSIONS + 2}.jpg").status_code == 200


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
    assert done["verified"] == 0 and done["thumbnail_only"] == 0  # a same-product match is not a pixel copy


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


def test_same_product_leftovers_are_kept_small(monkeypatch):
    """Images waiting for the same-product pass are held at evidence size; the row still describes the full-size original."""
    import base64
    import hashlib
    orig, other_photo, _ = _photos()
    big, size = other_photo, list(Image.open(io.BytesIO(other_photo)).size)
    assert max(size) > 800
    small, fp = pipeline.small_copy(big)
    assert max(Image.open(io.BytesIO(small)).size) <= 800 and small == pipeline.shrink(big)
    assert fp["size"] == size and fp["sha256"] == hashlib.sha256(big).hexdigest()
    if not semantic.enabled():
        pytest.skip("CLIP dependencies not installed")
    ev = _run(orig, [_cand("big photo", big, "https://bottles.example.shop/coke")], monkeypatch, semantic_search=True)
    matches = [d for e, d in ev if e == "match"]
    scan_id = [d for e, d in ev if e == "done"][0]["scan_id"]
    assert len(matches) == 1
    m = matches[0]
    assert m["forensics"]["kind"] == "same_product" and m["forensics"]["size"] == size
    assert m["sha256"] == hashlib.sha256(big).hexdigest()
    evidence = store.SESSIONS[scan_id]["items"][m["idx"]]["evidence"]
    assert max(Image.open(io.BytesIO(base64.b64decode(evidence))).size) <= 800


def test_duplicates_and_identical_bytes_are_checked_once(monkeypatch):
    """The same image+page from two sources gives one row, kept from the stronger source; the same bytes at another
    address get their own row but are compared once. done reports the dedup counts and stage timings."""
    from app import forensics
    orig, _, _ = _photos()
    crop = Image.open(io.BytesIO(orig)); w, h = crop.size
    copy = _jpg(crop.crop((0, 0, int(w * .9), int(h * .9))))
    calls = []
    real = forensics.compare_best
    monkeypatch.setattr(forensics, "compare_best", lambda refs, data: calls.append(1) or real(refs, data))
    cands = [{**_cand("copy", copy, "https://shop.example.xyz/p/1"), "source": "bing:q"},
             {**_cand("copy", copy, "https://shop.example.xyz/p/1"), "source": "lens-exact"},
             {**_cand("mirror", copy, "https://mirror.example.net/p/9"), "source": "brave:q"}]
    ev = _run(orig, cands, monkeypatch)
    rows = [d for e, d in ev if e == "match"]
    done = [d for e, d in ev if e == "done"][0]
    assert sorted((r["page_url"], r["source"]) for r in rows) == [("https://mirror.example.net/p/9", "brave:q"),
                                                                    ("https://shop.example.xyz/p/1", "lens-exact")]
    assert rows[0]["forensics"] is not rows[1]["forensics"]
    assert len(calls) == 1
    assert done["dedup"] == {"duplicate_candidates": 1, "same_bytes": 1}
    assert done["scanned"] == 2 and done["sources"] == {"bing": 1, "lens-exact": 1, "brave": 1}
    assert {"search", "verify", "total", "download", "compare", "evidence", "first_match"} <= set(done["timings"])


def test_strongest_sources_are_checked_first():
    cands = [{"source": s} for s in ("bing:q", "page-crawl", "brave-api:q", "aliexpress:q", "lens-visual", "lens-exact",
                                     "ebay-image", "you-api:q")]
    assert [c["source"] for c in sorted(cands, key=pipeline.source_rank)] == [
        "lens-exact", "lens-visual", "aliexpress:q", "ebay-image", "page-crawl", "bing:q", "brave-api:q", "you-api:q"]


def test_compare_decodes_the_candidate_once(monkeypatch):
    from app import forensics
    orig, _, unrelated = _photos()
    ref = forensics.Reference(orig)
    loads = []
    real = forensics.load
    monkeypatch.setattr(forensics, "load", lambda data: loads.append(1) or real(data))
    assert forensics.compare(ref, unrelated) is None
    assert len(loads) == 1


def test_heic_original_from_an_iphone_is_scanned(monkeypatch):
    """iPhones (and so the iMessage agent) send HEIC; it must scan like a JPEG."""
    pillow_heif = pytest.importorskip("pillow_heif")
    orig, _, _ = _photos()
    b = io.BytesIO(); pillow_heif.from_pillow(Image.open(io.BytesIO(orig)).convert("RGB")).save(b, format="HEIF")
    ev = _run(b.getvalue(), [_cand("copy", orig, "https://shop.example.xyz/p/1")], monkeypatch)
    assert not [d for e, d in ev if e == "error"]
    assert [d["title"] for e, d in ev if e == "match"] == ["copy"]


def test_a_hanging_host_cannot_hold_the_scan_open(monkeypatch):
    """A host that never answers costs one download budget, then the thumbnail is tried; the scan still finishes."""
    import time
    from app import net
    orig, _, _ = _photos()
    monkeypatch.setattr(pipeline, "DOWNLOAD_BUDGET", 0.3)
    monkeypatch.setattr(pipeline, "THUMB_BUDGET", 0.3)
    tried = []

    async def hangs(client, url, referer=None):
        tried.append(url)
        if url and "thumb" in url:
            return orig, 200
        await asyncio.sleep(60)
    monkeypatch.setattr(net, "fetch_image", hangs)
    cands = [{"image_url": "https://slow.example.com/a.jpg", "thumbnail": "https://cdn.example.com/thumb.jpg",
              "page_url": "https://slow.example.com/p", "title": "a", "source": "bing"}]
    t = time.perf_counter()
    ev = _run(orig, cands, monkeypatch)
    assert time.perf_counter() - t < 10
    rows = [d for e, d in ev if e == "match"]
    assert len(rows) == 1 and rows[0]["verification"].startswith("thumbnail-only")
    assert tried == ["https://slow.example.com/a.jpg", "https://cdn.example.com/thumb.jpg"]


def test_host_safety_is_checked_once_per_host(monkeypatch):
    from app import net
    calls = []
    monkeypatch.setattr(net, "_safe_hosts", {})
    monkeypatch.setattr(net, "_safe_host", lambda h: calls.append(h) or True)

    async def go():
        return [await net.safe_url_async(u) for u in
                ("https://cdn.example.com/1.jpg", "https://CDN.example.com/2.jpg", "ftp://cdn.example.com/x", "https://b.example.com/")]
    assert asyncio.run(go()) == [True, True, False, True]
    assert calls == ["cdn.example.com", "b.example.com"]


def test_own_site_pages_are_not_downloaded(monkeypatch):
    """Pages on the brand's own domains and resellers are skipped before any download, and counted."""
    from app import net
    orig, _, _ = _photos()
    fetched = []

    async def fetch(client, url, referer=None):
        fetched.append(url)
        return orig, 200
    monkeypatch.setattr(net, "fetch_image", fetch)
    cands = [{"image_url": f"https://cdn.shop.example/{i}.jpg", "page_url": f"https://www.coca-cola.com/p/{i}",
              "title": "x", "source": "bing"} for i in range(3)]
    cands += [{"image_url": "https://cdn.coca-cola.com/a.jpg", "page_url": "https://partner.example/p", "title": "x",
               "source": "bing"},
              {"image_url": "https://img.example.org/b.jpg", "page_url": "https://reseller.example/p", "title": "x",
               "source": "bing"}]

    async def fake_discover(*a, **k): return cands
    async def no_reverse(*a, **k): return []
    monkeypatch.setattr(search, "discover", fake_discover)
    monkeypatch.setattr(search, "reverse_candidates", no_reverse)

    async def go():
        return [e async for e in pipeline.run_scan(orig, "Coca-Cola", [], ["coca-cola.com"], resellers=["reseller.example"])]
    ev = asyncio.run(go())
    done = [d for e, d in ev if e == "done"][0]
    # a copy of the brand's image on someone else's page is still checked, wherever the image is hosted
    assert fetched == ["https://cdn.coca-cola.com/a.jpg"]
    assert done["own_site"] == 4 and done["scanned"] == 1
    assert any("4 on your own sites skipped" in d.get("message", "") for e, d in ev if e == "status")


def test_a_host_that_keeps_timing_out_is_given_up(monkeypatch):
    """After SLOW_HOST timeouts a host's other images are not requested; their thumbnails are still checked."""
    from app import net
    orig, _, _ = _photos()
    monkeypatch.setattr(pipeline, "SLOW_HOST", 2)
    asked = []

    async def fetch(client, url, referer=None):
        asked.append(url)
        if url and "stalls.example" in url:
            return None, -2
        return orig, 200
    monkeypatch.setattr(net, "fetch_image", fetch)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep(asyncio.sleep))
    cands = [{"image_url": f"https://stalls.example/{i}.jpg", "thumbnail": f"https://thumbs.example/{i}.jpg",
              "page_url": f"https://stalls.example/p/{i}", "title": "x", "source": "bing"} for i in range(6)]
    ev = _run(orig, cands, monkeypatch)
    done = [d for e, d in ev if e == "done"][0]
    # each early candidate tries the image 3 times (two count as timeouts), so the host is given up after one
    assert len([u for u in asked if "stalls.example" in u]) <= 6
    assert len([u for u in asked if "thumbs.example" in u]) == 6
    assert done["slow_hosts"] == ["stalls.example"] and done["thumbnail_only"] == 6


def _no_sleep(real):
    async def sleep(t, *a, **k):
        return await real(0)
    return sleep
