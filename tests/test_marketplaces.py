"""Tests for eBay image search and AliExpress (OneFindMe) sources, all offline."""
import asyncio
import base64
import json

import httpx

from app import marketplaces, sellers


def _run(coro_fn, handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await coro_fn(c)
    return asyncio.run(go())


def _ebay_env(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "app-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "cert-secret")
    monkeypatch.setitem(marketplaces._ebay_token, "value", None)


def test_ebay_off_without_keys():
    errors = []
    assert _run(lambda c: marketplaces.ebay_image_candidates(b"img", errors, c),
                lambda r: httpx.Response(500)) == []
    assert errors == []


def test_ebay_search_by_image(monkeypatch):
    _ebay_env(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/oauth2/token"):
            assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"app-id:cert-secret").decode()
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        assert request.url.path == "/buy/browse/v1/item_summary/search_by_image"
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
        assert json.loads(request.content)["image"] == base64.b64encode(b"jpegbytes").decode()
        return httpx.Response(200, json={"itemSummaries": [
            {"title": "Stan Smith 1:1", "itemWebUrl": "https://www.ebay.com/itm/123?hash=x",
             "image": {"imageUrl": "https://i.ebayimg.com/a.jpg"}, "thumbnailImages": [{"imageUrl": "https://i.ebayimg.com/t.jpg"}],
             "seller": {"username": "cheap_kicks"}, "price": {"value": "19.99", "currency": "USD"}},
            {"title": "no image", "itemWebUrl": "https://www.ebay.com/itm/456"},
        ]})

    errors = []
    out = _run(lambda c: marketplaces.ebay_image_candidates(b"jpegbytes", errors, c), handler)
    assert errors == []
    assert out == [{"image_url": "https://i.ebayimg.com/a.jpg", "thumbnail": "https://i.ebayimg.com/t.jpg",
                    "page_url": "https://www.ebay.com/itm/123", "title": "Stan Smith 1:1", "source": "ebay-image",
                    "reverse": True, "seller": "cheap_kicks", "price": "19.99 USD"}]
    # token is cached: a second search makes no new token request
    _run(lambda c: marketplaces.ebay_image_candidates(b"jpegbytes", errors, c), handler)
    assert sum(r.url.path.endswith("/oauth2/token") for r in seen) == 1


def test_ebay_error_has_no_secrets(monkeypatch):
    _ebay_env(monkeypatch)
    errors = []
    out = _run(lambda c: marketplaces.ebay_image_candidates(b"x", errors, c),
               lambda r: httpx.Response(401, json={"error": "invalid_client"}))
    assert out == [] and errors == ["ebay: HTTP 401"]
    assert "cert-secret" not in " ".join(errors)


def test_aliexpress_off_by_flag():
    errors = []
    assert _run(lambda c: marketplaces.aliexpress_candidates("Adidas", [], errors, c),
                lambda r: httpx.Response(500)) == []


def test_aliexpress_parses_and_dedupes(monkeypatch):
    monkeypatch.setenv("BG_ALIEXPRESS", "1")
    queries = []

    def handler(request):
        queries.append(request.url.params["query"])
        return httpx.Response(200, json={"query": "x", "results": [
            {"title": "Adidas Stan Smith", "price": 64.07, "currency": "USD", "orders": 63, "store": "Active Motion Hub Store",
             "image": "https://ae-pic-a1.aliexpress-media.com/kf/A.jpg", "url": "https://s.click.aliexpress.com/e/_abc"},
            {"title": "no image", "url": "https://s.click.aliexpress.com/e/_def"},
        ]})

    errors = []
    out = _run(lambda c: marketplaces.aliexpress_candidates("Adidas", ["stan smith", "sneakers", "extra"], errors, c), handler)
    assert queries == ["Adidas", "Adidas stan smith", "Adidas sneakers"]
    assert len(out) == 1  # same image across queries kept once
    assert out[0]["seller"] == "Active Motion Hub Store"
    assert out[0]["price"] == "64.07 USD" and out[0]["orders"] == 63
    assert out[0]["page_url"] == "https://s.click.aliexpress.com/e/_abc"


def test_canonical_page_url():
    def handler(request):
        assert request.url.host == "s.click.aliexpress.com"
        return httpx.Response(302, headers={"location": "https://www.aliexpress.com/item/3256808597573635.html?aff_fcid=x&sk=y"})

    out = _run(lambda c: marketplaces.canonical_page_url(c, "https://s.click.aliexpress.com/e/_c3iLuaYt"), handler)
    assert out == "https://www.aliexpress.com/item/3256808597573635.html"
    # other URLs are returned untouched, without a request
    assert _run(lambda c: marketplaces.canonical_page_url(c, "https://shop.example/p"), lambda r: 1 / 0) == "https://shop.example/p"
    # a failed lookup keeps the original link
    assert _run(lambda c: marketplaces.canonical_page_url(c, "https://s.click.aliexpress.com/e/_x"),
                lambda r: httpx.Response(200)) == "https://s.click.aliexpress.com/e/_x"


def test_seller_name_from_source_groups_listings():
    ms = [{"idx": i, "page_url": f"https://www.aliexpress.com/item/{i}.html", "seller": "Active Motion Hub Store",
           "risk": {"score": 60, "category": "marketplace"}, "verification": "verified"} for i in range(3)]
    ident = sellers.seller_identity(ms[0])
    assert ident["kind"] == "marketplace_seller" and ident["name"] == "Active Motion Hub Store"
    groups = sellers.group_matches(ms)
    assert len(groups) == 1 and groups[0]["listings"] == 3
