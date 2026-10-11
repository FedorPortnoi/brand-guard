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


# --- every answer: the real brand and other places to buy (build plan step 1) ---

def _fake_shop(**facts):
    return {"verdict": "red", "url": "https://adidas-outlet.shop/samba", "domain": "adidas-outlet.shop",
            "facts": {"brand": "Adidas", "product": "Samba OG", **facts}}


def test_the_real_brand_is_found_for_a_shop_using_its_name(monkeypatch):
    queries = []

    async def search(query, errors, count=10):
        queries.append(query)
        return [{"page_url": "https://www.trustpilot.com/review/adidas.com"},
                {"page_url": "https://adidas-outlet.shop/about"},  # the shop being checked never counts
                {"page_url": "https://www.adidas.com/us"}]
    monkeypatch.setattr(listing, "web_search", search)
    brand = asyncio.run(ask.real_brand(_fake_shop(), []))
    assert brand == {"name": "Adidas", "url": "https://www.adidas.com/us", "this_site": False}
    assert queries == ["Adidas official site"]


def test_the_brands_own_site_is_not_looked_up_again(monkeypatch):
    async def search(*a, **k):
        raise AssertionError("no search needed")
    monkeypatch.setattr(listing, "web_search", search)
    own = {"verdict": "green", "url": "https://www.adidas.com/us/samba", "domain": "adidas.com", "facts": {"brand": "adidas"}}
    assert asyncio.run(ask.real_brand(own, [])) == {"name": "adidas", "url": "https://adidas.com", "this_site": True}


def test_every_verdict_gets_alternatives_but_never_red_ones(monkeypatch):
    calls = {}

    async def deep(value, source=None):
        calls.update(value=value, source=source)
        return {"alternatives": [{"host": "fake2.shop", "verdict": "red"}, {"host": "www.footlocker.com", "verdict": "green"},
                                 {"host": "www.zappos.com", "verdict": "yellow"}],
                "manufacturer": {"name": "Adidas", "url": None}, "same_photo": [{"url": "https://aliexpress.com/item/1"}]}

    async def brand(result, errors):
        return {"name": "Adidas", "url": "https://www.adidas.com/us", "this_site": False}
    monkeypatch.setattr(listing, "deep_research", deep)
    monkeypatch.setattr(ask, "real_brand", brand)
    for verdict in ("green", "yellow", "red"):
        out = asyncio.run(ask.extras({**_fake_shop(), "verdict": verdict}))
        assert [a["host"] for a in out["alternatives"]] == ["www.footlocker.com", "www.zappos.com"]
        assert out["real_brand"]["url"] == "https://www.adidas.com/us" and out["alternatives_note"] == ""
    assert calls["source"]["domain"] == "adidas-outlet.shop"  # the check already done is reused, not repeated


def test_the_maker_stands_in_when_the_page_names_no_brand(monkeypatch):
    async def deep(value, source=None):
        return {"alternatives": [], "manufacturer": {"name": "Hoka", "url": "https://www.hoka.com/"}}
    monkeypatch.setattr(listing, "deep_research", deep)
    out = asyncio.run(ask.extras({"verdict": "yellow", "url": "https://kicks.example/p", "domain": "kicks.example",
                                  "facts": {"product": "Clifton 9"}}))
    assert out["real_brand"] == {"name": "Hoka", "url": "https://www.hoka.com/", "this_site": False}
    assert out["alternatives_note"] == "I didn't find other trusted places selling it."


def test_no_product_no_alternatives_and_the_answer_says_why(monkeypatch):
    async def deep(*a, **k):
        raise AssertionError("nothing to search for")
    monkeypatch.setattr(listing, "deep_research", deep)
    out = asyncio.run(ask.extras({"verdict": "yellow", "url": "https://luckyfours.com/", "domain": "luckyfours.com", "facts": {}}))
    assert out["alternatives"] == [] and out["real_brand"] is None and out["alternatives_note"] == ask.NO_PRODUCT


def test_alternatives_leave_out_other_pages_of_the_same_shop(monkeypatch):
    from app import net, rdap, search

    async def web(query, errors, count=10):
        return [{"page_url": "https://adidas-outlet.shop/samba-white", "title": "Buy Samba OG white"},
                {"page_url": "https://www.footlocker.com/product/samba-og/B75806.html", "title": "Buy Samba OG"}]

    async def safe(url):
        return True

    async def fetch(client, url):
        return None

    async def lookup(domain, client):
        return {}

    async def reverse(image_url, errors):
        return []
    monkeypatch.setattr(listing, "web_search", web)
    monkeypatch.setattr(net, "safe_url_async", safe)
    monkeypatch.setattr(net, "fetch_html", fetch)
    monkeypatch.setattr(rdap, "lookup", lookup)
    monkeypatch.setattr(search, "reverse_candidates", reverse)
    out = asyncio.run(listing.deep_research("https://adidas-outlet.shop/samba", source=_fake_shop()))
    assert [a["host"] for a in out["alternatives"]] == ["footlocker.com"]


def test_a_product_name_is_read_from_the_address_when_the_page_blocks_reading():
    assert ask.product_from_url("https://www.adidas.com/us/samba-og-shoes/B75806") == "samba og shoes"
    assert ask.product_from_url("https://shop.example/collections/sale/products/hoka-clifton-9.html") == "hoka clifton 9"
    assert ask.product_from_url("https://www.amazon.com/dp/B0C1234567") == ""  # an id only: nothing to search for
    assert ask.product_from_url("https://luckyfours.com/") == ""


def test_a_blocked_product_page_still_gets_alternatives(monkeypatch):
    seen = {}

    async def deep(value, source=None):
        seen.update(source["facts"])
        return {"alternatives": [{"host": "footlocker.com", "verdict": "green"}], "manufacturer": None}
    monkeypatch.setattr(listing, "deep_research", deep)
    out = asyncio.run(ask.extras({"verdict": "green", "url": "https://www.adidas.com/us/samba-og-shoes/B75806",
                                  "domain": "adidas.com", "facts": {}}))
    assert seen == {"product": "samba og shoes", "site_name": "adidas"}
    assert [a["host"] for a in out["alternatives"]] == ["footlocker.com"]
