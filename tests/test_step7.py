"""Step 7: a social post we can't see is Couldn't check and asks for a screenshot; no junk alternatives."""
import asyncio

from app import ask, infra, listing, net, rdap

SHELL = b"<html><head><title>Instagram</title></head><body><div id='root'></div><script>window.x=1</script></body></html>"
POST = b'<html><head><meta property="og:description" content="120 likes - kicks on Instagram: DM to order"></head></html>'


def _offline(monkeypatch, html, social=None):
    async def fetch_html(client, url): return html
    async def lookup(domain, client=None): return {"age_days": 4000}
    async def describe(host, client):
        return {"ips": [], "network": None, "org": None, "range": None, "shared": None, "platform": True}
    async def safe(url): return True
    async def final(client, url): return url
    monkeypatch.setattr(net, "fetch_html", fetch_html)
    monkeypatch.setattr(net, "safe_url_async", safe)
    monkeypatch.setattr(rdap, "lookup", lookup)
    monkeypatch.setattr(infra, "describe", describe)
    monkeypatch.setattr(listing, "final_url", final)
    monkeypatch.setattr(listing, "social_info", lambda url: social)


def test_an_app_shell_is_not_the_post():
    assert not listing.shows_post(SHELL) and not listing.shows_post(None)
    assert listing.shows_post(POST)


def test_a_post_behind_a_login_is_couldnt_check_and_asks_for_a_screenshot(monkeypatch):
    _offline(monkeypatch, SHELL)
    r = asyncio.run(listing.check_url("https://www.instagram.com/p/C1a2B3c4D5e/", log=False))
    assert r["verdict"] == "yellow" and r["send_screenshot"] is True
    assert r["facts"]["title"] is None  # the shell's "Instagram" title is not the post's
    assert {s["label"]: s for s in r["signals"]}["Page not inspectable"]["detail"] == "Instagram didn't show us this post without a login."


def test_a_post_we_can_see_gets_a_verdict_and_no_screenshot_request(monkeypatch):
    _offline(monkeypatch, SHELL, social={"account": "@kicks", "title": "Video by kicks", "caption": "New drop"})
    r = asyncio.run(listing.check_url("https://www.instagram.com/p/real/", log=False))
    assert r["verdict"] == "green" and r["send_screenshot"] is False and r["sells"] is None
    _offline(monkeypatch, POST)  # the page itself carries the post's metadata
    assert asyncio.run(listing.check_url("https://www.instagram.com/p/real/", log=False))["verdict"] != "yellow"


def test_a_screenshot_already_sent_is_never_asked_for_again(monkeypatch):
    _offline(monkeypatch, SHELL)
    r = asyncio.run(listing.check_url("https://www.instagram.com/kicks/", log=False, seen_text="Fresh pairs weekly"))
    assert r["send_screenshot"] is False


def test_whether_a_page_sells_anything():
    assert listing.sells(None, {}) is None
    assert listing.sells(b"<title>Warning</title><p>This page is a test phishing page.</p>", {}) is False
    assert listing.sells(b"<p>Fine watches</p><button>Add to cart</button>", {}) is True
    assert listing.sells(b'<link href="https://cdn.shopify.com/s/files/x.css">', {}) is True
    assert listing.sells(b"<p>About us</p>", {"price": 49.0}) is True
    assert listing.sells(b"<footer><img alt='Visa'></footer>", {}) is True  # takes payment


def _no_deep(monkeypatch):
    async def deep(*a, **k):
        raise AssertionError("no alternatives search expected")

    async def no_search(*a, **k):
        raise AssertionError("no store description search expected")
    monkeypatch.setattr(listing, "deep_research", deep)
    monkeypatch.setattr(ask, "describe_store", no_search)


def test_no_alternatives_for_a_page_that_sells_nothing(monkeypatch):
    _no_deep(monkeypatch)

    async def brand(result, errors): return None
    monkeypatch.setattr(ask, "real_brand", brand)
    page = {"verdict": "red", "url": "https://phish.example/login", "domain": "phish.example", "sells": False,
            "facts": {"title": "Google App Engine"}}
    out = asyncio.run(ask.extras(page))
    assert out["alternatives"] == [] and out["alternatives_note"] == ask.NOT_A_STORE


def test_no_alternatives_from_a_platforms_own_description(monkeypatch):
    _no_deep(monkeypatch)
    for url in ("https://www.ebay.com/itm/299999999999", "https://www.instagram.com/p/C1a2B3c4D5e/"):
        assert asyncio.run(ask.alternatives({"verdict": "yellow", "url": url, "domain": url.split("/")[2][4:], "facts": {}})) == ({}, [], {})
    post = {"verdict": "green", "url": "https://www.instagram.com/p/x/", "domain": "instagram.com",
            "facts": {"title": "Video by basickslash"}}  # a post's title is not a product
    assert asyncio.run(ask.alternatives(post)) == ({}, [], {})
