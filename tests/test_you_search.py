"""Tests for the You.com web search source."""
import asyncio

import httpx

from app import search


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(queries, client):
    async def go():
        errors = []
        async with client:
            out = await search.you_api(queries, errors, client)
        return out, errors
    return asyncio.run(go())


def test_no_key(monkeypatch):
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    calls = []
    (imgs, pages), errors = _run(["q"], _client(lambda r: calls.append(r) or httpx.Response(200, json={})))
    assert (imgs, pages, errors, calls) == ([], [], [], [])


def test_parses_pages_and_thumbnails(monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "test-key")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": {"web": [
            {"url": "https://shop.example/stan", "title": "Stan Smith cheap",
             "thumbnail_url": "https://img.example/t.jpg?a=1&amp;b=2",
             "original_thumbnail_url": "https://img.example/full.jpg?a=1&amp;b=2"},
            {"url": "https://blog.example/post", "title": "No thumbnail"},
            {"title": "No url, skipped"},
        ]}, "metadata": {}})

    (imgs, pages), errors = _run(["adidas replica"], _client(handler))
    assert errors == []
    assert seen[0].headers["X-API-Key"] == "test-key"
    assert seen[0].url.params["query"] == "adidas replica"
    assert [p["page_url"] for p in pages] == ["https://shop.example/stan", "https://blog.example/post"]
    assert len(imgs) == 1
    assert imgs[0]["image_url"] == "https://img.example/full.jpg?a=1&b=2"
    assert imgs[0]["thumbnail"] == "https://img.example/t.jpg?a=1&b=2"
    assert imgs[0]["page_url"] == "https://shop.example/stan"
    assert imgs[0]["source"] == "you-api:adidas replica"


def test_query_cap(monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "test-key")
    seen = []
    (_, _), _ = _run([f"q{i}" for i in range(10)],
                     _client(lambda r: seen.append(r) or httpx.Response(200, json={"results": {"web": []}})))
    assert len(seen) == 6


def test_auth_error_stops_early(monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "bad")
    seen = []
    (imgs, pages), errors = _run(["a", "b", "c"],
                                 _client(lambda r: seen.append(r) or httpx.Response(403, json={"message": "Forbidden"})))
    assert (imgs, pages) == ([], [])
    assert len(seen) == 1
    assert errors == ["you-api: HTTPStatusError"]
    assert "bad" not in " ".join(errors)


def test_discover_survives_you_failure(monkeypatch):
    """A failing You.com source must not break discovery."""
    monkeypatch.setattr(search, "_images", lambda q, b, n, errors: [])
    monkeypatch.setattr(search, "_text", lambda q, n, errors: [])

    async def no_brave(queries, errors):
        return [], []

    async def broken_you(queries, errors, client=None):
        errors.append("you-api: ConnectError")
        return [], []

    monkeypatch.setattr(search, "brave_api", no_brave)
    monkeypatch.setattr(search, "you_api", broken_you)
    errors = []

    async def go():
        async with httpx.AsyncClient() as c:
            return await search.discover("Adidas", [], [], c, errors)

    assert asyncio.run(go()) == []
    assert "you-api: ConnectError" in errors
