import asyncio

from fastapi.testclient import TestClient

from app import ask, listing
from app.main import app


def test_addresses_in_a_message_or_a_screenshot():
    assert ask.addresses("is https://adidas-outlet.shop/samba?x=1 legit?") == ["https://adidas-outlet.shop/samba?x=1"]
    assert ask.addresses("saw this on adidas-outlet.shop/samba-og, DM to order") == ["adidas-outlet.shop/samba-og"]
    # emails, file names, clock times and abbreviations are not addresses
    assert ask.addresses("mail sales@shop.com about IMG_0042.jpg at 7.11 pm, e.g. tomorrow") == []


def test_the_shops_own_address_beats_a_platform_name():
    assert ask.pick_address(["instagram.com", "luxe-kicks.store"]) == "luxe-kicks.store"
    assert ask.pick_address(["https://www.ebay.com/itm/123"]) == "https://www.ebay.com/itm/123"
    assert ask.pick_address(["instagram.com"]) is None


def test_an_account_in_a_screenshot_is_checked_on_its_platform():
    assert ask.account_url(["adidas.deals.us", "@adidas.deals.us", "DM to order"]) == \
        ("https://www.instagram.com/adidas.deals.us/", "@adidas.deals.us on Instagram")
    assert ask.account_url(["TikTok Shop", "@kickz_plug"])[0] == "https://www.tiktok.com/@kickz_plug"
    assert ask.account_url(["no handle here", "email me@x.com"]) is None


def test_a_store_name_is_taken_from_the_question():
    assert ask.store_name("Is Lucky Fours legit?") == "Lucky Fours"
    assert ask.store_name("is this store a scam: Valeen") == "Valeen"


def test_a_name_is_looked_up_and_review_sites_are_skipped(monkeypatch):
    async def search(query, errors, count=10):
        return [{"page_url": "https://www.trustpilot.com/review/luckyfours.com"},
                {"page_url": "https://www.instagram.com/luckyfours/"},
                {"page_url": "https://luckyfours.com/"}]
    checked = {}

    async def check(url, brand="", seen_text="", **kw):
        checked.update(url=url, brand=brand)
        return {"verdict": "green", "domain": "luckyfours.com"}
    monkeypatch.setattr(listing, "web_search", search)
    monkeypatch.setattr(listing, "check_url", check)
    r = asyncio.run(ask.ask("is Lucky Fours legit?"))
    assert checked == {"url": "https://luckyfours.com/", "brand": "Lucky Fours"}
    assert r["found"]["how"] == "name" and r["found"]["what"] == "Lucky Fours: luckyfours.com"


def test_screenshot_wording_counts_like_a_caption(monkeypatch):
    plain = listing.assess("https://www.instagram.com/kicks/", None, {}, [], [])
    seen = listing.assess("https://www.instagram.com/kicks/", None, {}, [], [], seen_text="DM to order, Zelle only. 1:1 replica")
    labels = {s["label"] for s in seen["signals"]}
    assert {"Payment off the platform", "High-risk sales language"} <= labels
    assert seen["score"] > plain["score"] and seen["verdict"] == "red"
    # on a shop's own site too, not only on social posts
    shop = listing.assess("https://kicks.example/p", None, {"age_days": 900}, [], [], seen_text="Pay by Zelle")
    assert "Payment off the platform" in {s["label"] for s in shop["signals"]}


def test_ask_route_reads_a_screenshot(monkeypatch):
    monkeypatch.setattr(ask, "read_text", lambda image: ["9:41", "luxe-kicks.store", "Samba OG $39"])

    async def check(url, brand="", seen_text="", **kw):
        return {"verdict": "red", "domain": "luxe-kicks.store", "url": url, "seen": seen_text}
    monkeypatch.setattr(listing, "check_url", check)
    r = TestClient(app).post("/api/listing/ask", files={"image": ("s.png", b"png", "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "luxe-kicks.store" and body["found"]["how"] == "screenshot"
    assert "Samba OG" in body["seen"]


def test_ask_route_says_what_to_send_when_nothing_is_found(monkeypatch):
    monkeypatch.setattr(ask, "read_text", lambda image: ["just a photo of shoes"])
    r = TestClient(app).post("/api/listing/ask", files={"image": ("s.png", b"png", "image/png")})
    assert r.status_code == 422 and "Send me the link" in r.json()["detail"]
