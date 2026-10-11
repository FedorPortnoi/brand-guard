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


def test_every_verdict_gets_alternatives_but_only_legit_ones(monkeypatch):
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
        # zappos was rated Couldn't check (yellow): a store we couldn't check is not a trusted place to buy
        assert [a["host"] for a in out["alternatives"]] == ["www.footlocker.com"]
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

    async def search(query, errors, count=10):
        return [{"page_url": "https://luckyfours.com/", "title": "Lucky Fours"}]  # names the store, not what it sells
    monkeypatch.setattr(listing, "deep_research", deep)
    monkeypatch.setattr(listing, "web_search", search)
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


def test_what_a_store_sells_is_read_from_its_search_title():
    assert ask.store_category("Lucky Fours® | #1 Rated Affordable Luxury Watches", "luckyfours") == "Affordable Luxury Watches"
    assert ask.store_category("Men's Watches – Lucky Fours", "luckyfours") == "Men's Watches"
    assert ask.store_category("Valeen - Official Site | Women's Dresses & Tops", "valeen") == "Women's Dresses & Tops"
    assert ask.store_category("Lucky Fours", "luckyfours") == ""


def test_a_store_known_only_by_name_gets_stores_selling_the_same_kind_of_thing(monkeypatch):
    seen = {}

    async def search(query, errors, count=10):
        seen["query"] = query
        return [{"page_url": "https://www.reddit.com/r/Watches/x", "title": "[Lucky Fours] A Good Buy?"},
                {"page_url": "https://luckyfours.com/products/santos", "title": "Lucky Fours® Santos"},
                {"page_url": "https://luckyfours.com/", "title": "Lucky Fours® | #1 Rated Affordable Luxury Watches"}]

    async def deep(value, source=None):
        seen["product"] = source["facts"]["product"]
        return {"alternatives": [{"host": "watchwarehouse.com", "verdict": "green"}], "manufacturer": None}
    monkeypatch.setattr(listing, "web_search", search)
    monkeypatch.setattr(listing, "deep_research", deep)
    out = asyncio.run(ask.extras({"verdict": "yellow", "url": "https://luckyfours.com/", "domain": "luckyfours.com", "facts": {}}))
    assert seen == {"query": "luckyfours.com", "product": "Affordable Luxury Watches"}  # the home page's title, not a product's
    assert out["alternatives_for"] == {"what": "Affordable Luxury Watches", "kind": "category"}
    assert [a["host"] for a in out["alternatives"]] == ["watchwarehouse.com"]


def test_blog_posts_and_guides_are_not_places_to_buy():
    assert listing.ARTICLE_PATH.search("/post/cheapest-luxury-watches")
    assert listing.ARTICLE_PATH.search("/blogs/news/best-sneakers-2026")
    assert not listing.ARTICLE_PATH.search("/products/samba-og")


# --- pages we can't read live (owner's re-check of step 1: "Lucky Fours is legit but the agent couldn't find it") ---

def test_an_unreachable_page_is_judged_from_its_archived_copy(monkeypatch):
    import httpx
    from app import archive, infra, net, rdap, reputation
    monkeypatch.setenv("BG_WAYBACK_READ", "1")

    async def unreachable(client, url):
        return None

    async def describe(host, client):
        return {"ips": [], "shared": "Shopify", "platform": True}

    async def age(domain, client):
        return {"age_days": 1100}

    class NoThreats:
        def __init__(self, *a): pass
        async def safe_browsing(self, url, client, errors): return []

    def wayback(request):
        assert request.url.host == "web.archive.org"
        if "/id_/" in request.url.path or request.url.path.startswith("/web/2"):
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  content=b"<html><head><title>Lucky Fours | Affordable Luxury Watches</title></head></html>",
                                  extensions={})
        return httpx.Response(404)
    real_get = httpx.AsyncClient.get

    async def get(self, url, **kw):
        if "web.archive.org" in str(url):
            resp = wayback(httpx.Request("GET", str(url)))
            resp.request = httpx.Request("GET", "https://web.archive.org/web/20260819174851id_/https://luckyfours.com/")
            return resp
        return await real_get(self, url, **kw)

    async def final(client, url):
        return url
    monkeypatch.setattr(net, "fetch_html", unreachable)
    monkeypatch.setattr(infra, "describe", describe)
    monkeypatch.setattr(rdap, "lookup", age)
    monkeypatch.setattr(reputation, "ScanReputation", NoThreats)
    monkeypatch.setattr(listing, "final_url", final)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)

    async def safe(url):
        return True
    monkeypatch.setattr(net, "safe_url_async", safe)
    r = asyncio.run(listing.check_url("https://luckyfours.com/", log=False))
    labels = {s["label"] for s in r["signals"]}
    assert r["archived"].startswith("2026-08-19") and "Read an archived copy" in labels
    assert "Page not inspectable" not in labels and r["verdict"] == "green"
    assert r["facts"]["title"] == "Lucky Fours | Affordable Luxury Watches"


def test_a_heavy_page_keeps_its_head_but_an_oversized_image_is_dropped():
    import httpx
    from app import net
    big = b"<html><head><title>Kith</title></head>" + b"x" * 5000

    def handler(request):
        kind = "text/html" if request.url.path == "/page" else "image/jpeg"
        return httpx.Response(200, headers={"content-type": kind}, content=big)

    async def run():
        async def safe(url):
            return True
        net_safe, net.safe_url_async = net.safe_url_async, safe
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                page, _ = await net._get(c, "https://kith.example/page", {}, 1000, "html")
                image, _ = await net._get(c, "https://kith.example/img", {}, 1000, "image")
        finally:
            net.safe_url_async = net_safe
        return page, image
    page, image = asyncio.run(run())
    assert page is not None and page.startswith(b"<html><head><title>Kith") and len(page) == 1000
    assert image is None


def test_names_sold_under_a_registry_suffix_belong_to_their_owner():
    from app.sellers import registrable_domain
    assert registrable_domain("kiths.us.com") == "kiths.us.com"  # us.com resells names, like co.uk
    assert registrable_domain("shop.example.co.uk") == "example.co.uk"
    assert registrable_domain("luxe.myshopify.com") == "luxe.myshopify.com"
    assert registrable_domain("www.adidas.com") == "adidas.com"
    assert registrable_domain("203.0.113.7") == "203.0.113.7"


def test_a_look_alike_of_the_brand_is_never_suggested(monkeypatch):
    async def deep(value, source=None):
        return {"alternatives": [
            {"host": "kiths.us.com", "verdict": "yellow", "signals": [{"label": "Brand name in a longer address", "tone": "bad"}]},
            {"host": "stockx.com", "verdict": "green", "signals": [{"label": "Established domain", "tone": "good"}]}],
            "manufacturer": None}
    monkeypatch.setattr(listing, "deep_research", deep)
    out = asyncio.run(ask.extras({"verdict": "green", "url": "https://kith.com/products/box-logo-hoodie", "domain": "kith.com",
                                  "facts": {"product": "Box Logo Hoodie"}}))
    assert [a["host"] for a in out["alternatives"]] == ["stockx.com"]
    flagged = listing.assess("https://kiths.us.com/", b"<html><title>Kith</title></html>", {"age_days": 40}, [], [], claimed_brand="Kith")
    assert "Brand name in a longer address" in {s["label"] for s in flagged["signals"]}


def test_a_store_home_page_gets_stores_for_what_it_sells_not_its_own_title(monkeypatch):
    seen = {}

    async def deep(value, source=None):
        seen["product"] = source["facts"]["product"]
        return {"alternatives": [{"host": "watchwarehouse.com", "verdict": "green"}], "manufacturer": None}
    monkeypatch.setattr(listing, "deep_research", deep)
    out = asyncio.run(ask.extras({"verdict": "green", "url": "https://luckyfours.com/", "domain": "luckyfours.com",
                                  "facts": {"title": "Lucky Fours® | #1 Rated Affordable Luxury Watches"}}))
    assert seen["product"] == "Affordable Luxury Watches"
    assert out["alternatives_for"] == {"what": "Affordable Luxury Watches", "kind": "category"}


# --- one message, several things (owner's re-check: "is Lucky Fours legit? and https://kith.com/." only answered Kith) ---

def _no_extras(monkeypatch):
    async def none(result):
        return {}
    monkeypatch.setattr(ask, "extras", none)


def test_a_store_name_and_a_link_in_one_message_are_both_checked_in_order(monkeypatch):
    _no_extras(monkeypatch)

    async def search(query, errors, count=10):
        return [{"page_url": "https://luckyfours.com/"}]

    async def check(url, brand="", seen_text="", **kw):
        return {"verdict": "green", "url": url, "domain": url.split("//")[-1].strip("/").rstrip(".")}
    monkeypatch.setattr(listing, "web_search", search)
    monkeypatch.setattr(listing, "check_url", check)
    r = asyncio.run(ask.ask("is Lucky Fours legit? and https://kith.com/."))
    assert r["found"]["what"] == "Lucky Fours: luckyfours.com"  # written first, answered first
    assert [o["found"]["what"] for o in r["others"]] == ["kith.com"]


def test_one_item_failing_does_not_lose_the_others(monkeypatch):
    _no_extras(monkeypatch)

    async def search(query, errors, count=10):
        return []  # the name finds no website

    async def check(url, brand="", seen_text="", **kw):
        return {"verdict": "red", "url": url, "domain": "luxe-kicks.store"}
    monkeypatch.setattr(listing, "web_search", search)
    monkeypatch.setattr(listing, "check_url", check)
    r = asyncio.run(ask.ask("is Zzqxv Boutique legit and luxe-kicks.store"))
    assert r["found"]["how"] == "link"
    assert r["others"] == [{"error": "I couldn't find a website for \"Zzqxv Boutique\". Send me the link instead.",
                            "found": {"how": "name", "what": "Zzqxv Boutique"}}]


def test_at_most_three_things_are_checked_and_filler_is_not_a_store(monkeypatch):
    _no_extras(monkeypatch)
    checked = []

    async def check(url, brand="", seen_text="", **kw):
        checked.append(url)
        return {"verdict": "green", "url": url, "domain": url}
    monkeypatch.setattr(listing, "check_url", check)
    r = asyncio.run(ask.ask("check a.shop b.shop c.shop d.shop for me"))
    assert checked == ["a.shop", "b.shop", "c.shop"] and len(r["others"]) == 2
    assert ask.store_name("check  for me") == ""
