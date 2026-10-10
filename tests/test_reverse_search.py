"""Tests for reverse image search via SerpApi."""
import asyncio
import pytest
import httpx

from app import search


def _mock_transport():
    """Create a mock transport for httpx."""
    def transport_func(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        engine = params.get("engine")
        api_key = params.get("api_key")
        search_type = params.get("type", "")

        if not api_key:
            return httpx.Response(400, json={"error": "Invalid API key"})

        if engine == "google_lens" and search_type == "exact_matches":
            return httpx.Response(200, json={
                "exact_matches": [
                    {"thumbnail": "https://example.com/thumb1.jpg", "image": "https://example.com/img1.jpg",
                     "link": "https://example.com/page1", "title": "Exact match 1"},
                    {"image": "https://example.com/img2.jpg", "link": "https://example.com/page2",
                     "title": "Exact match 2"}
                ]
            })

        if engine == "google_lens" and search_type == "visual_matches":
            return httpx.Response(200, json={
                "visual_matches": [
                    {"thumbnail": "https://example.com/thumb3.jpg", "image": "https://example.com/img3.jpg",
                     "link": "https://example.com/page3", "title": "Visual match 1"},
                    {"thumbnail": "https://example.com/thumb4.jpg", "link": "https://example.com/page4",
                     "title": "Visual match 2"}
                ]
            })

        if engine == "yandex_images":
            return httpx.Response(200, json={
                "images_results": [
                    {"thumbnail": "https://example.com/thumb5.jpg", "original": "https://example.com/img5.jpg",
                     "link": "https://example.com/page5", "title": "Yandex match 1"},
                    {"original": "https://example.com/img6.jpg", "link": "https://example.com/page6",
                     "title": "Yandex match 2"}
                ]
            })

        return httpx.Response(400, json={"error": "Unknown engine"})

    return httpx.MockTransport(transport_func)


def test_no_key(monkeypatch):
    """Test that no key returns empty list with no requests."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    errors = []
    result = asyncio.run(search.reverse_candidates("https://example.com/image.jpg", errors))
    assert result == []
    assert errors == []


def test_no_image_url(monkeypatch):
    """Test that empty image_url returns empty list."""
    monkeypatch.setenv("SERPAPI_KEY", "test_key")
    errors = []
    result = asyncio.run(search.reverse_candidates("", errors))
    assert result == []
    assert errors == []


def test_all_engines(monkeypatch):
    """Test that all three engines are parsed correctly."""
    monkeypatch.setenv("SERPAPI_KEY", "test_key")
    monkeypatch.delenv("SERPAPI_ENGINES", raising=False)

    async def run_test():
        errors = []
        client = httpx.AsyncClient(transport=_mock_transport())
        try:
            result = await search.reverse_candidates("https://example.com/image.jpg", errors, client)
        finally:
            await client.aclose()

        assert len(result) > 0
        for cand in result:
            assert "image_url" in cand
            assert "thumbnail" in cand
            assert "page_url" in cand
            assert "title" in cand
            assert "source" in cand
            assert cand.get("reverse") is True

        sources = {c["source"] for c in result}
        assert "lens-exact" in sources
        assert "lens-visual" in sources
        assert "yandex-reverse" in sources
        assert errors == []

    asyncio.run(run_test())


def test_engine_selection(monkeypatch):
    """Test that SERPAPI_ENGINES filters which engines are called."""
    monkeypatch.setenv("SERPAPI_KEY", "test_key")
    monkeypatch.setenv("SERPAPI_ENGINES", "lens_exact")

    async def run_test():
        errors = []
        client = httpx.AsyncClient(transport=_mock_transport())
        try:
            result = await search.reverse_candidates("https://example.com/image.jpg", errors, client)
        finally:
            await client.aclose()

        sources = {c["source"] for c in result}
        assert sources == {"lens-exact"}

    asyncio.run(run_test())


def test_deduplication(monkeypatch):
    """Test that duplicates across engines are removed."""
    def dedup_transport(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        engine = params.get("engine")

        if engine == "google_lens" and params.get("type") == "exact_matches":
            return httpx.Response(200, json={
                "exact_matches": [
                    {"thumbnail": "https://example.com/thumb.jpg", "image": "https://example.com/same.jpg",
                     "link": "https://example.com/page1", "title": "Title 1"}
                ]
            })

        if engine == "google_lens" and params.get("type") == "visual_matches":
            return httpx.Response(200, json={
                "visual_matches": [
                    {"image": "https://example.com/same.jpg", "thumbnail": "https://example.com/thumb.jpg",
                     "link": "https://example.com/page2", "title": "Title 2"}
                ]
            })

        if engine == "yandex_images":
            return httpx.Response(200, json={"images_results": []})

        return httpx.Response(400)

    monkeypatch.setenv("SERPAPI_KEY", "test_key")
    monkeypatch.setenv("SERPAPI_ENGINES", "lens_exact,lens_visual,yandex")

    async def run_test():
        errors = []
        client = httpx.AsyncClient(transport=httpx.MockTransport(dedup_transport))
        try:
            result = await search.reverse_candidates("https://example.com/image.jpg", errors, client)
        finally:
            await client.aclose()

        image_urls = [c["image_url"] for c in result]
        assert len(image_urls) == 1
        assert image_urls.count("https://example.com/same.jpg") == 1

    asyncio.run(run_test())


def test_error_handling(monkeypatch):
    """Test that errors from one engine don't break others and no key is exposed."""
    def mixed_transport(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        engine = params.get("engine")

        if engine == "google_lens" and params.get("type") == "exact_matches":
            return httpx.Response(429, json={})

        if engine == "google_lens" and params.get("type") == "visual_matches":
            return httpx.Response(200, json={
                "visual_matches": [
                    {"image": "https://example.com/img.jpg", "link": "https://example.com/page",
                     "title": "Success"}
                ]
            })

        if engine == "yandex_images":
            return httpx.Response(200, json={"error": "Invalid API key"})

        return httpx.Response(400)

    monkeypatch.setenv("SERPAPI_KEY", "test_key")
    monkeypatch.setenv("SERPAPI_ENGINES", "lens_exact,lens_visual,yandex")

    async def run_test():
        errors = []
        client = httpx.AsyncClient(transport=httpx.MockTransport(mixed_transport))
        try:
            result = await search.reverse_candidates("https://example.com/image.jpg", errors, client)
        finally:
            await client.aclose()

        assert len(result) > 0
        assert result[0]["source"] == "lens-visual"

        assert len(errors) >= 2
        error_str = " ".join(errors)
        assert "test_key" not in error_str
        assert "429" in error_str or "lens-exact" in error_str
        assert "yandex" in error_str or "Invalid API key" in error_str

    asyncio.run(run_test())


def test_missing_fields(monkeypatch):
    """Test defensive parsing when fields are missing."""
    def sparse_transport(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        engine = params.get("engine")

        if engine == "google_lens" and params.get("type") == "exact_matches":
            return httpx.Response(200, json={
                "exact_matches": [
                    {"link": "https://example.com/page1", "title": "No image"},
                    {"image": "https://example.com/img.jpg", "title": "Has image"}
                ]
            })

        if engine == "google_lens" and params.get("type") == "visual_matches":
            return httpx.Response(200, json={"visual_matches": []})

        if engine == "yandex_images":
            return httpx.Response(200, json={"images_results": []})

        return httpx.Response(400)

    monkeypatch.setenv("SERPAPI_KEY", "test_key")

    async def run_test():
        errors = []
        client = httpx.AsyncClient(transport=httpx.MockTransport(sparse_transport))
        try:
            result = await search.reverse_candidates("https://example.com/image.jpg", errors, client)
        finally:
            await client.aclose()

        assert len(result) == 1
        assert result[0]["image_url"] == "https://example.com/img.jpg"

    asyncio.run(run_test())


def test_visual_matches_capped_at_40(monkeypatch):
    """Test that visual matches are capped at 40."""
    def many_visual_transport(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        engine = params.get("engine")

        if engine == "google_lens" and params.get("type") == "visual_matches":
            matches = [
                {"image": f"https://example.com/img{i}.jpg", "link": f"https://example.com/page{i}",
                 "title": f"Visual match {i}"}
                for i in range(50)
            ]
            return httpx.Response(200, json={"visual_matches": matches})

        return httpx.Response(200, json={})

    monkeypatch.setenv("SERPAPI_KEY", "test_key")
    monkeypatch.setenv("SERPAPI_ENGINES", "lens_visual")

    async def run_test():
        errors = []
        client = httpx.AsyncClient(transport=httpx.MockTransport(many_visual_transport))
        try:
            result = await search.reverse_candidates("https://example.com/image.jpg", errors, client)
        finally:
            await client.aclose()

        assert len(result) == 40

    asyncio.run(run_test())


def test_ordering(monkeypatch):
    """Test that results are ordered: exact, Yandex, visual."""
    monkeypatch.setenv("SERPAPI_KEY", "test_key")

    async def run_test():
        errors = []
        client = httpx.AsyncClient(transport=_mock_transport())
        try:
            result = await search.reverse_candidates("https://example.com/image.jpg", errors, client)
        finally:
            await client.aclose()

        sources = [c["source"] for c in result]

        exact_idx = next((i for i, s in enumerate(sources) if s == "lens-exact"), None)
        yandex_idx = next((i for i, s in enumerate(sources) if s == "yandex-reverse"), None)
        visual_idx = next((i for i, s in enumerate(sources) if s == "lens-visual"), None)

        if exact_idx is not None and yandex_idx is not None:
            assert exact_idx < yandex_idx
        if yandex_idx is not None and visual_idx is not None:
            assert yandex_idx < visual_idx
        if exact_idx is not None and visual_idx is not None:
            assert exact_idx < visual_idx

    asyncio.run(run_test())


def test_app_imports():
    """Test that the app can still be imported."""
    import app.main
    assert app.main.app is not None
