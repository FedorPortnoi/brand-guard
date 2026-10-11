"""Step 6: the crowd signal, "checked N times this week, M came out Not legit", from the investigation log."""
import asyncio
from datetime import datetime, timedelta, timezone

from app import infra, investigations, listing, net, rdap

PAGE = b"<html><head><title>Trail Shoe</title></head><body>Shoes</body></html>"


def _offline(monkeypatch, age_days):
    async def fetch_html(client, url): return PAGE
    async def lookup(domain, client=None): return {"age_days": age_days[0]}
    async def describe(host, client):
        return {"ips": ["198.51.100.9"], "network": "X", "org": "X", "range": None, "shared": None, "platform": False}
    async def safe(url): return True
    async def final(client, url): return url
    monkeypatch.setattr(net, "fetch_html", fetch_html)
    monkeypatch.setattr(net, "safe_url_async", safe)
    monkeypatch.setattr(rdap, "lookup", lookup)
    monkeypatch.setattr(infra, "describe", describe)
    monkeypatch.setattr(listing, "final_url", final)


def test_each_check_counts_the_site_this_week_and_how_often_it_was_flagged(monkeypatch):
    age = [3]  # a 3-day-old domain: Not legit
    _offline(monkeypatch, age)
    first = asyncio.run(listing.check_url("https://new-shop.example/a"))
    assert first["crowd"] == {"checked": 1, "flagged": 1, "days": 7}
    second = asyncio.run(listing.check_url("https://new-shop.example/b"))  # another page, same site
    assert second["crowd"] == {"checked": 2, "flagged": 2, "days": 7}
    age[0] = 4000  # the same site judged Legit later (say its registration became readable)
    third = asyncio.run(listing.check_url("https://www.new-shop.example/c"))
    assert third["verdict"] == "green" and third["crowd"] == {"checked": 3, "flagged": 2, "days": 7}
    other = asyncio.run(listing.check_url("https://other-shop.example/a"))
    assert other["crowd"]["checked"] == 1  # by site: other sites don't count


def test_checks_older_than_a_week_and_unlogged_checks_do_not_count(monkeypatch):
    _offline(monkeypatch, [3])
    real_now = investigations._now
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="seconds")
    investigations._now = lambda: old
    try:
        asyncio.run(listing.check_url("https://new-shop.example/a"))  # logged 8 days ago
    finally:
        investigations._now = real_now
    r = asyncio.run(listing.check_url("https://new-shop.example/a", log=False))
    assert r["crowd"] == {"checked": 0, "flagged": 0, "days": 7}
    assert len(investigations.history()) == 1  # the old check is in the log, just outside the week


def test_on_a_marketplace_the_listing_is_counted_not_the_whole_platform():
    for url, score, category in (("https://www.ebay.com/itm/1", 60, "likely_counterfeit"), ("https://www.ebay.com/itm/1", 10, "low_concern"),
                                 ("https://www.ebay.com/itm/2", 60, "likely_counterfeit")):
        inv = f"link-{url[-1]}-{score}"
        investigations.start(inv, "x", [], [], [], "", "link")
        investigations.add_listing(inv, {"idx": 0, "page_url": url, "title": "", "verification": "link-check",
                                         "risk": {"host": "ebay.com", "score": score, "category": category}},
                                   {"ips": [], "platform": True})
    assert investigations.crowd("ebay.com", "https://www.ebay.com/itm/1", True) == {"checked": 2, "flagged": 1, "days": 7}
    assert investigations.crowd("ebay.com", "https://www.ebay.com/itm/3", True)["checked"] == 0
