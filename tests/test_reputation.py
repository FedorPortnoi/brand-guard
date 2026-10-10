"""Tests for PhishStats lookalike domains and Google Safe Browsing, all offline."""
import asyncio

import httpx

from app import reputation


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _phish(monkeypatch, rows):
    monkeypatch.setenv("BG_PHISHSTATS", "1")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=rows)

    async def go():
        errors = []
        async with _client(handler) as c:
            return await reputation.phishstats_reports("Adidas", ["adidas.com"], errors, c), errors, seen
    return asyncio.run(go())


ROWS = [
    {"url": "http://ww25.adidas-mx.club/easter?subid=1", "date": "2025-12-18T21:17:00.000Z", "score": "6.7", "countryname": "United States"},
    {"url": "http://ww25.adidas-mx.club/amazon-dx", "date": "2025-12-17T20:08:00.000Z", "score": "8.1"},
    {"url": "https://sites.google.com/view/adidas-login", "date": "2025-11-01T00:00:00.000Z", "score": "5"},
    {"url": "https://www.adidas.com/us/real", "date": "2025-10-01T00:00:00.000Z", "score": "1"},
]


def test_brand_tokens():
    assert reputation.brand_tokens("Louis Vuitton") == ["louisvuitton", "louis-vuitton"]
    assert reputation.brand_tokens("Adidas") == ["adidas"]
    assert reputation.brand_tokens("BMW") == []  # too short: would match unrelated URLs


def test_phishstats_off_by_flag():
    async def go():
        async with _client(lambda r: 1 / 0) as c:
            return await reputation.phishstats_reports("Adidas", [], [], c)
    assert asyncio.run(go()) == []


def test_phishstats_groups_hosts_and_skips_official(monkeypatch):
    hosts, errors, seen = _phish(monkeypatch, ROWS)
    assert errors == []
    assert seen[0].url.params["_where"] == "(url,like,~adidas~)"
    by = {h["host"]: h for h in hosts}
    assert set(by) == {"ww25.adidas-mx.club", "sites.google.com"}  # adidas.com is official
    lk = by["ww25.adidas-mx.club"]
    assert lk["reports"] == 2 and lk["last_seen"] == "2025-12-18" and lk["max_score"] == 8.1
    assert lk["brand_in_domain"] and lk["domain"] == "adidas-mx.club"
    assert not by["sites.google.com"]["brand_in_domain"]


def test_reputation_flags_lookalike_domain_but_not_shared_host(monkeypatch):
    hosts, _, _ = _phish(monkeypatch, ROWS)
    rep = reputation.ScanReputation(hosts)
    assert rep.phish_hit("https://shop.adidas-mx.club/p")  # same lookalike domain, other subdomain
    assert rep.phish_hit("https://sites.google.com/view/adidas-login")  # exact reported host
    assert not rep.phish_hit("https://www.google.com/search")  # shared platform: domain not flagged

    async def go():
        async with _client(lambda r: 1 / 0) as c:  # Safe Browsing is off: no request
            r = {"score": 50, "category": "marketplace", "reasons": []}
            await rep.apply(r, "http://ww25.adidas-mx.club/x", c, [])
            o = {"score": 0, "category": "official", "reasons": []}
            await rep.apply(o, "http://ww25.adidas-mx.club/x", c, [])
            return r, o
    r, o = asyncio.run(go())
    assert r["score"] == 70 and "PhishStats" in r["reasons"][0] and "2 reports" in r["reasons"][0]
    assert o == {"score": 0, "category": "official", "reasons": []}


def test_safe_browsing(monkeypatch):
    monkeypatch.setenv("GOOGLE_SAFE_BROWSING_KEY", "gsb-key")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["key"] == "gsb-key"
        body = request.read().decode()
        if "bad.example" in body:
            return httpx.Response(200, json={"matches": [{"threatType": "SOCIAL_ENGINEERING", "threat": {"url": "x"}}]})
        return httpx.Response(200, json={})

    async def go():
        rep, errors = reputation.ScanReputation([]), []
        async with _client(handler) as c:
            bad = {"score": 40, "category": "unknown", "reasons": []}
            await rep.apply(bad, "https://bad.example/p", c, errors)
            await rep.apply({"score": 40, "category": "unknown", "reasons": []}, "https://bad.example/p", c, errors)
            ok = {"score": 40, "category": "unknown", "reasons": []}
            await rep.apply(ok, "https://fine.example/p", c, errors)
        return bad, ok, errors
    bad, ok, errors = asyncio.run(go())
    assert bad["score"] == 65 and bad["reasons"] == ["Google Safe Browsing flags this page: social engineering"]
    assert ok == {"score": 40, "category": "unknown", "reasons": []}
    assert len(calls) == 2 and errors == []  # the repeat URL was cached


def test_safe_browsing_failure_is_soft(monkeypatch):
    monkeypatch.setenv("GOOGLE_SAFE_BROWSING_KEY", "gsb-key")

    async def go():
        errors = []
        async with _client(lambda r: httpx.Response(403)) as c:
            r = {"score": 40, "category": "unknown", "reasons": []}
            await reputation.ScanReputation([]).apply(r, "https://x.example", c, errors)
        return r, errors
    r, errors = asyncio.run(go())
    assert r["score"] == 40 and errors == ["safe-browsing: HTTP 403"]
    assert "gsb-key" not in " ".join(errors)
