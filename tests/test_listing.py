from app import listing
from fastapi.testclient import TestClient
from app.main import app


PRODUCT_HTML = b"""<!doctype html><html><head>
<meta property="og:title" content="Trail Shoe 4">
<script type="application/ld+json">{
  "@context":"https://schema.org", "@type":"Product", "name":"Trail Shoe 4",
  "brand":{"@type":"Brand","name":"North Ridge"},
  "manufacturer":{"@type":"Organization","name":"North Ridge Works","url":"https://northridge.example"},
  "offers":{"@type":"Offer","price":"89.50","priceCurrency":"USD","seller":{"name":"Hill Shop"}}
}</script></head><body>Returns accepted within 30 days.</body></html>"""


def test_extracts_product_facts_from_json_ld():
    facts = listing.page_facts(PRODUCT_HTML, "https://shop.example/shoe")
    assert facts["product"] == "Trail Shoe 4"
    assert facts["brand"] == "North Ridge"
    assert facts["manufacturer"] == "North Ridge Works"
    assert facts["price"] == 89.5 and facts["currency"] == "USD"
    assert facts["seller"] == "Hill Shop"


def test_verdicts_are_signal_based_and_never_claim_certainty():
    green = listing.assess("https://established.example/item", PRODUCT_HTML, {"age_days": 2200}, [], [])
    assert green["verdict"] == "green"
    assert "not a guarantee" in green["summary"].lower() and green["headline"] == "Legit"

    # a marketplace listing is no longer held at "not sure" for being on a marketplace: it is a warning, not a verdict
    etsy = listing.assess("https://etsy.com/listing/1", PRODUCT_HTML, {"age_days": 7200}, [], [])
    assert etsy["verdict"] == "green"
    assert any(s["label"] == "Third-party marketplace" for s in etsy["signals"])

    red = listing.assess("http://deal.example.xyz/win", b"<title>Pay with gift card to claim your prize</title>",
                         {"age_days": 4}, ["SOCIAL_ENGINEERING"], [])
    assert red["verdict"] == "red" and red["headline"] == "Not legit"
    assert any(s["label"] == "Known threat warning" for s in red["signals"])


def test_url_normalization():
    assert listing.normalize_url("shop.example/item") == "https://shop.example/item"
    for value in ("", "not a url", "file:///tmp/item"):
        try:
            listing.normalize_url(value)
        except listing.ListingError:
            pass
        else:
            raise AssertionError(value)


def test_alternatives_rank_trust_before_price():
    rows = [{"verdict": "yellow", "price": 10}, {"verdict": "green", "price": 90},
            {"verdict": "green", "price": 70}, {"verdict": "red", "price": 2}]
    ranked = listing.rank_alternatives(rows)
    assert [(x["verdict"], x["price"]) for x in ranked] == [
        ("green", 70), ("green", 90), ("yellow", 10), ("red", 2)]


def test_listing_route(monkeypatch):
    expected = {"verdict": "green", "url": "https://shop.example/item"}

    async def fake_check(url):
        assert url == "shop.example/item"
        return expected

    monkeypatch.setattr(listing, "check_url", fake_check)
    response = TestClient(app).post("/api/listing/check", json={"url": "shop.example/item"})
    assert response.status_code == 200 and response.json() == expected


# --- brand settings, social posts, servers, the log, suppliers -------------------------------------------------

import asyncio  # noqa: E402

from app import infra, investigations, net, rdap  # noqa: E402


def _labels(r):
    return {s["label"]: s for s in r["signals"]}


def test_brand_settings_settle_a_page_that_blocks_reading():
    """adidas.com blocks automated visits; with the brand's official list it is still clearly the brand's own site."""
    url = "https://www.adidas.com/us/stan-smith-shoes/FX5502.html"
    nothing = listing.assess(url, None, {}, [], [])
    assert nothing["verdict"] == "yellow"  # no page, no domain age: nothing to judge by
    aged = listing.assess(url, None, {"age_days": 11000}, [], [])
    assert aged["verdict"] == "green"  # the page is blocked, but a 30-year-old domain is evidence
    vouched = listing.assess(url, None, {"age_days": 11000}, [], [], brand="Adidas", official=["adidas.com"])
    assert vouched["verdict"] == "green" and "The brand's own site" in _labels(vouched)
    reseller = listing.assess("https://www.footlocker.com/p/1", None, {"age_days": 9000}, [], [], resellers=["footlocker.com"])
    assert reseller["verdict"] == "green" and "Authorised reseller" in _labels(reseller)


def test_a_big_brand_site_is_recognised_without_settings():
    """An old domain at a brand-protection registrar, on the company's own network: green even when the page is blocked."""
    r = listing.assess("https://www.adidas.com/us/x", None, {"age_days": 11000, "registrar": "CSC Corporate Domains, Inc."}, [], [],
                       server={"ips": ["87.237.217.231"], "org": "adidas netmaster", "network": "ADIDAS"})
    assert r["verdict"] == "green"
    assert {"Brand-protection registrar", "Runs its own network"} <= set(_labels(r))
    shop = listing.assess("https://valeen.co/", b"<title>Valen</title>", {"age_days": 900}, [], [],
                          server={"ips": ["23.227.38.65"], "org": "Shopify, Inc.", "shared": "Shopify"})
    assert "Shared hosting" in _labels(shop) and _labels(shop)["Shared hosting"]["impact"] == 0


def test_brand_name_inside_a_longer_address_is_flagged():
    r = listing.assess("https://nike-outlet-sale.shop/p/1", PRODUCT_HTML, {"age_days": 40}, [], [], claimed_brand="Nike")
    assert "Brand name in a longer address" in _labels(r) and r["verdict"] == "red"
    own = listing.assess("https://nike.com/p/1", PRODUCT_HTML, {"age_days": 9000}, [], [], claimed_brand="Nike")
    assert "Brand name in a longer address" not in _labels(own)


def test_social_posts_use_the_post_not_the_platforms_domain():
    post = {"account": "luxe.deals.store", "followers": 812, "title": "AJ1 restock", "posted": "2026-10-01",
            "caption": "1:1 quality, DM to order, pay by Zelle"}
    r = listing.assess("https://www.tiktok.com/@luxe.deals.store/video/1", None, {"age_days": 11000}, [], [], social=post)
    labels = _labels(r)
    assert "Established domain" not in labels and "Domain age unavailable" not in labels  # tiktok.com's age is irrelevant
    assert "Payment off the platform" in labels and "High-risk sales language" in labels
    assert labels["Posted by"]["detail"].startswith("luxe.deals.store, 812 followers") and r["verdict"] == "red"
    verified = listing.assess("https://www.youtube.com/watch?v=x", None, {}, [], [],
                              social={"account": "adidas", "verified": True, "title": "Stan Smith"})
    assert _labels(verified)["Posted by"]["tone"] == "good"


def test_a_server_shared_with_flagged_sites_counts_only_off_shared_platforms():
    seen = [{"host": "fake-a.example", "same_ips": ["203.0.113.7"], "same_site": False, "same_url": False, "score": 70,
             "verification": "verified"}]
    r = listing.assess("https://fake-b.example/p", PRODUCT_HTML, {"age_days": 400}, [], seen,
                       server={"ips": ["203.0.113.7"], "network": "EXAMPLE-VPS"})
    assert "Shares a server with flagged sites" in _labels(r)
    assert "Seen in prior investigations" not in _labels(r)  # a neighbour's history is not this site's history
    shared = listing.assess("https://fake-b.example/p", PRODUCT_HTML, {"age_days": 400}, [], seen,
                            server={"ips": ["203.0.113.7"], "shared": "Shopify"})
    assert "Shares a server with flagged sites" not in _labels(shared)


def _offline_check(monkeypatch, html=PRODUCT_HTML):
    async def fetch_html(client, url): return html
    async def lookup(domain, client=None): return {"age_days": 1200, "registrar": "Example Registrar"}
    async def describe(host, client):
        return {"ips": ["198.51.100.9"], "network": "EXAMPLE", "org": "Example", "range": None, "shared": None, "platform": False}
    async def safe(url): return True
    async def final(client, url): return url
    monkeypatch.setattr(net, "fetch_html", fetch_html)
    monkeypatch.setattr(net, "safe_url_async", safe)
    monkeypatch.setattr(rdap, "lookup", lookup)
    monkeypatch.setattr(infra, "describe", describe)
    monkeypatch.setattr(listing, "final_url", final)


def test_a_checked_link_is_logged_and_recognised_next_time(monkeypatch):
    _offline_check(monkeypatch)
    first = asyncio.run(listing.check_url("https://shop.example/trail-shoe"))
    assert first["image_url"] is None and first["server"]["ips"] == ["198.51.100.9"]
    hist = investigations.history()
    assert [(h["origin"], h["listings"], h["brand"]) for h in hist] == [("link", 1, "North Ridge")]
    second = asyncio.run(listing.check_url("https://shop.example/trail-shoe"))
    assert "Seen before" not in _labels(second)  # checked before, but that check was not red: no warning
    red_before = [{"host": "shop.example", "same_site": True, "same_url": True, "same_ips": [], "score": 70,
                   "verification": "link-check"}]
    assert "Seen in prior investigations" in _labels(listing.assess("https://shop.example/x", PRODUCT_HTML, {"age_days": 900}, [], red_before))
    asyncio.run(listing.check_url("https://shop.example/trail-shoe", log=False))
    assert len(investigations.history()) == 2


def test_product_photo_and_google_lens_link(monkeypatch):
    html = PRODUCT_HTML.replace(b'<meta property="og:title"',
                                b'<meta property="og:image" content="https://cdn.shop.example/shoe.jpg"><meta property="og:title"')
    _offline_check(monkeypatch, html)
    r = asyncio.run(listing.check_url("https://shop.example/trail-shoe", log=False))
    assert r["image_url"] == "https://cdn.shop.example/shoe.jpg"
    assert r["lens_url"] == "https://lens.google.com/uploadbyurl?url=https%3A%2F%2Fcdn.shop.example%2Fshoe.jpg"


def test_supplier_leads_separate_suppliers_from_search_pages():
    lead = listing.supplier_lead
    assert lead("https://www.alibaba.com/product-detail/White-Sneakers_1601.html", "White sneakers")["kind"] == "supplier"
    shop = lead("https://shoesfactory.en.alibaba.com/", "Shoes Factory")
    assert shop["kind"] == "supplier" and shop["supplier"] == "shoesfactory" and shop["site"] == "Alibaba"
    assert lead("https://www.dhgate.com/wholesale/leather+sneakers.html", "x")["kind"] == "search page"
    assert lead("https://www.made-in-china.com/products-search/hot-china-products/Sneaker.html", "x")["kind"] == "search page"
    assert listing._strip_brand("Nike Air Force 1 '07 - White", "Nike") == "Air Force 1 '07 White"


def test_supplier_search_modes(monkeypatch):
    _offline_check(monkeypatch)
    asked = []

    async def web(query, errors, count=10):
        asked.append(query)
        return [{"page_url": "https://www.dhgate.com/wholesale/trail+shoe.html", "title": "trail shoe wholesale"},
                {"page_url": "https://www.alibaba.com/product-detail/Trail-Shoe_1.html", "title": "Trail shoe OEM"},
                {"page_url": "https://www.ebay.com/itm/1", "title": "not a supplier site"}]
    monkeypatch.setattr(listing, "web_search", web)
    src = asyncio.run(listing.suppliers("https://shop.example/trail-shoe", "source"))
    assert src["query"] == "Trail Shoe 4" and all("North Ridge" not in q for q in asked)  # copies are listed unbranded
    assert [x["kind"] for x in src["leads"]] == ["supplier", "search page"] and src["maker"] is None
    asked.clear()
    mk = asyncio.run(listing.suppliers("https://shop.example/trail-shoe", "maker"))
    assert mk["maker"]["name"] == "North Ridge Works" and mk["maker"]["url"] == "https://northridge.example"
    assert any("manufacturer" in q for q in asked)
    assert investigations.history() == []  # research does not log the source link again


def test_scan_a_listing_photo_by_url(monkeypatch, tmp_path):
    import io
    from PIL import Image
    import app.main as main
    from app import search
    monkeypatch.setattr(main, "DATA", tmp_path)  # the endpoint also saves each scan for replay: never into data/
    b = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(b, "JPEG")
    async def fetch_image(client, url, referer=None): return b.getvalue(), 200
    async def none(*a, **k): return []
    monkeypatch.setattr(net, "fetch_image", fetch_image)
    monkeypatch.setattr(search, "discover", none)
    monkeypatch.setattr(search, "reverse_candidates", none)
    c = TestClient(app)
    body = c.post("/api/scan", data={"brand": "Acme", "photo_url": "https://cdn.shop.example/shoe.jpg"}).text
    assert "event: done" in body
    assert c.post("/api/scan", data={"brand": "Acme"}).status_code == 422


def test_wording_matches_whole_words_only():
    assert listing._hits(("copy", "replica", "1:1", "% off"), "copyright 2009, all rights reserved") == []
    assert listing._hits(("copy", "replica", "1:1", "% off"), "1:1 replica, 30% off today") == ["replica", "1:1", "% off"]


def test_supplier_results_must_be_about_the_product():
    q = "Men's Tree Runner"
    assert listing.relevant(q, "Men Women designer running shoes treeperi https://www.dhgate.com/product/x")
    assert not listing.relevant(q, "Shoe trees wholesale https://www.dhgate.com/wholesale/shoe+trees.html")
    assert not listing.relevant(q, "Marathon runners https://www.dhgate.com/wholesale/marathon+runners.html")
    assert listing.relevant("Mug", "Ceramic mug wholesale") and not listing.relevant("Mug", "Tea cups")
    assert listing.relevant("Men's New", "anything")  # no distinctive words: nothing to require


def test_brave_titles_arrive_as_plain_text(monkeypatch):
    from app import search

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"web": {"results": [{"url": "https://x.example", "title": "Men&#039;s <strong>Tree</strong> Runner",
                                         "description": "a &amp; b"}]}}

    class Client:
        async def get(self, *a, **k): return Resp()
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    rows = asyncio.run(search.brave_web("q", [], client=Client()))
    assert rows == [{"page_url": "https://x.example", "title": "Men's Tree Runner", "description": "a & b"}]
    monkeypatch.delenv("BRAVE_API_KEY")
    assert asyncio.run(search.brave_web("q", [])) is None  # no key: the caller falls back to DuckDuckGo


def test_weak_words_count_only_in_titles():
    caption = {"account": "Rick Astley", "title": "Never Gonna Give You Up", "caption": "Get your copy of the album, cheap shipping"}
    r = listing.assess("https://www.youtube.com/watch?v=x", None, {}, [], [], social=caption)
    assert "High-risk sales language" not in _labels(r)
    fake = listing.assess("https://www.youtube.com/watch?v=y", None, {}, [], [],
                          social={"account": "x", "title": "Restock", "caption": "best 1:1 replica in stock"})
    assert _labels(fake)["High-risk sales language"]["detail"] == "Found: replica, 1:1."
    titled = listing.assess("https://shop.example/p", b"<title>Cheap outlet copy bags</title>", {"age_days": 900}, [], [])
    assert "copy" in _labels(titled)["High-risk sales language"]["detail"]


# --- step 2: three verdicts (Legit / Not legit / Couldn't check) and the web footprint ---------------------------

def test_one_cut_off_decides_legit_or_not(monkeypatch):
    """No middle band: the same evidence is Not legit at the cut-off and Legit one point below it."""
    url, html, domain = "https://young.example/p", PRODUCT_HTML, {"age_days": 100}
    score = listing.assess(url, html, domain, [], [])["score"]
    monkeypatch.setattr(listing, "NOT_LEGIT", score)
    assert listing.assess(url, html, domain, [], [])["verdict"] == "red"
    monkeypatch.setattr(listing, "NOT_LEGIT", score + 1)
    assert listing.assess(url, html, domain, [], [])["verdict"] == "green"


def test_the_owners_cut_off_is_35():
    assert listing.NOT_LEGIT == 35


def test_a_domain_under_30_days_old_is_not_legit_on_its_own():
    """Owner, 10.10: too new to tell a real company from a scam, even with a clean page and good signs."""
    clean = b"<title>Shoes</title>"
    for age in (0, 4, 29):
        r = listing.assess("https://freshshoes.example/p", clean, {"age_days": age}, [], [])
        assert r["verdict"] == "red" and r["headline"] == "Not legit" and "Very new domain" in _labels(r)
    following = {"accounts": [{"platform": "Instagram", "handle": "freshshoes", "url": "https://www.instagram.com/freshshoes/",
                               "followers": 90_000}], "reviews": [], "threads": []}
    assert listing.assess("https://freshshoes.example/p", None, {"age_days": 10}, [], [], footprint=following)["verdict"] == "red"
    assert listing.assess("https://freshshoes.example/p", clean, {"age_days": 30}, [], [])["verdict"] == "green"


def test_social_posts_are_not_forced_to_not_sure():
    post = listing.assess("https://www.instagram.com/p/abc/", None, {}, [], [],
                          social={"account": "@shop", "followers": 900, "title": "New drop", "caption": "Link in bio"})
    assert post["verdict"] == "green" and "Social media listing" in _labels(post)


def test_couldnt_check_only_with_no_evidence_at_all():
    url = "https://hidden.example/"
    nothing = listing.assess(url, None, {}, [], [])
    assert nothing["verdict"] == "yellow" and nothing["headline"] == "Couldn't check"
    assert listing.assess(url, None, {}, ["MALWARE"], [])["verdict"] == "red"  # a threat report is evidence
    assert listing.assess(url, None, {"age_days": 3}, [], [])["verdict"] == "red"  # so is the domain's age
    assert listing.assess(url, PRODUCT_HTML, {}, [], [])["verdict"] == "green"  # and the page itself
    seen = {"accounts": [{"platform": "Instagram", "handle": "hidden", "url": "https://www.instagram.com/hidden/",
                          "followers": 40_000}], "reviews": [], "threads": []}
    r = listing.assess(url, None, {}, [], [], footprint=seen)
    assert r["verdict"] == "green" and "Following elsewhere" in _labels(r)
    empty = {"accounts": [], "reviews": [], "threads": []}
    assert listing.assess(url, None, {}, [], [], footprint=empty)["verdict"] == "yellow"  # searched, found nothing


FOOTPRINT_ROWS = [
    {"page_url": "https://brightowls.com/", "title": "Bright Owls | Handmade Lamps", "description": "Lamps."},
    {"page_url": "https://www.instagram.com/brightowls/", "title": "BRIGHT OWLS (@brightowls)",
     "description": "351K followers, 17 following, 15 posts - BRIGHT OWLS (@brightowls) on Instagram"},
    {"page_url": "https://www.instagram.com/reel/DeEaSPrt3AB/", "title": "Bright Owls reel", "description": "9M seguidores"},
    {"page_url": "https://www.facebook.com/brightowlsco/videos/123/", "title": "Sale | Bright Owls",
     "description": "Bright Owls. 23,415 followers \u00b7 3,637 talking about this."},
    {"page_url": "https://www.facebook.com/OwlLovers/posts/1", "title": "Bright owls at night", "description": "12,000 followers"},
    {"page_url": "https://www.trustpilot.com/review/brightowls.com", "title": "Bright Owls Reviews",
     "description": "Considering 1,184 reviews, reviewers overwhelmingly had a great experience."},
    {"page_url": "https://www.reddit.com/r/Lamps/comments/1/bright_owls_a_good_buy/", "title": "[Bright Owls] A Good Buy?",
     "description": "130 votes, 304 comments."},
    {"page_url": "https://www.reddit.com/r/birds/comments/2/owls/", "title": "Two owls in bright light", "description": ""},
]


def test_web_footprint_counts_only_what_is_about_the_store():
    fp = listing.web_footprint(FOOTPRINT_ROWS, "brightowls.com")
    assert [(a["platform"], a["handle"], a["followers"]) for a in fp["accounts"]] == [
        ("Instagram", "brightowls", 351_000), ("Facebook", "brightowlsco", 23_415)]  # not the reel, not OwlLovers
    assert fp["reviews"] == [{"site": "trustpilot.com", "url": "https://www.trustpilot.com/review/brightowls.com", "count": 1184}]
    assert [t["title"] for t in fp["threads"]] == ["[Bright Owls] A Good Buy?"]  # not "Two owls in bright light"


def test_a_look_alike_does_not_borrow_the_real_stores_footprint():
    fp = listing.web_footprint(FOOTPRINT_ROWS, "brightowls-sale.com")
    assert fp["accounts"] == [] and fp["reviews"] == [] and fp["threads"] == []


def test_short_store_names_must_stand_as_a_word():
    rows = [{"page_url": "https://www.reddit.com/r/x/comments/1/a/", "title": "Is Kith legit?"},
            {"page_url": "https://www.reddit.com/r/x/comments/2/b/", "title": "Kitchen tips"},
            {"page_url": "https://www.reddit.com/r/x/comments/3/c/", "title": "kithnyc restock"}]
    assert [t["title"] for t in listing.web_footprint(rows, "kith.com")["threads"]] == ["Is Kith legit?"]


def test_follower_counts_are_read_in_any_format():
    assert listing._number("351", "K") == 351_000 and listing._number("1.2", "M") == 1_200_000
    assert listing._number("12,123,214", None) == 12_123_214


def test_an_unreadable_store_with_no_archive_is_judged_from_its_footprint(monkeypatch):
    _offline_check(monkeypatch, html=None)

    async def no_age(domain, client=None): return {}

    async def rows(domain):
        assert domain == "brightowls.com"
        return FOOTPRINT_ROWS
    monkeypatch.setattr(rdap, "lookup", no_age)
    monkeypatch.setattr(listing, "_web_rows", rows)
    r = asyncio.run(listing.check_url("https://brightowls.com/", log=False))
    assert r["verdict"] == "green" and r["footprint"]["accounts"][0]["followers"] == 351_000
    assert {"Following elsewhere", "Review-site page", "Discussed online", "Page not inspectable"} <= set(_labels(r))

    async def nothing(domain): return []
    monkeypatch.setattr(listing, "_web_rows", nothing)
    assert asyncio.run(listing.check_url("https://brightowls.com/", log=False))["verdict"] == "yellow"


def test_the_footprint_is_not_searched_when_the_page_can_be_read(monkeypatch):
    _offline_check(monkeypatch)

    async def rows(domain): raise AssertionError("no search needed")
    monkeypatch.setattr(listing, "_web_rows", rows)
    assert asyncio.run(listing.check_url("https://shop.example/trail-shoe", log=False))["footprint"] is None


# --- step 4: proof under each reason ---------------------------------------------------------------------------

def _proof(r, label):
    return _labels(r)[label].get("proof")


def test_domain_age_and_registrar_reasons_carry_the_registration_date():
    r = listing.assess("https://deal.example.xyz/p", PRODUCT_HTML,
                       {"age_days": 4, "created": "2026-10-06T12:00:00Z", "registrar": "CSC Corporate Domains"}, [], [])
    assert _proof(r, "Very new domain") == {"text": "Registered 2026-10-06", "url": "https://lookup.icann.org/en/lookup?name=example.xyz"}
    old = listing.assess("https://brand.example/p", PRODUCT_HTML, {"age_days": 9000, "created": "2001-08-01T00:00:00Z",
                                                                   "registrar": "CSC Corporate Domains"}, [], [])
    assert _proof(old, "Established domain")["text"] == "Registered 2001-08-01"
    assert _proof(old, "Brand-protection registrar")["text"] == "Registrar: CSC Corporate Domains"


def test_a_threat_report_links_to_googles_public_lookup():
    r = listing.assess("http://deal.example/win", PRODUCT_HTML, {"age_days": 900}, ["SOCIAL_ENGINEERING"], [])
    assert _proof(r, "Known threat warning") == {
        "text": "Safe Browsing: social engineering",
        "url": "https://transparencyreport.google.com/safe-browsing/search?url=http%3A%2F%2Fdeal.example%2Fwin"}


def test_wording_reasons_quote_where_the_words_were_seen():
    shot = listing.assess("https://www.instagram.com/p/x/", None, {}, [], [],
                          social={"account": "@kicks", "account_url": "https://www.instagram.com/kicks/"},
                          seen_text="Fresh pairs every week. DM to order, Zelle only. Ships fast")
    assert _proof(shot, "Payment off the platform") == {"text": '"Fresh pairs every week. DM to order, Zelle only. Ships fast" (from what you sent)',
                                                       "url": None}
    assert _proof(shot, "Posted by") == {"text": "@kicks", "url": "https://www.instagram.com/kicks/"}
    page = listing.assess("https://prize.example/win", b"<title>Pay with gift card to claim your prize</title>", {"age_days": 900}, [], [])
    assert _proof(page, "Scam-style language") == {"text": '"Pay with gift card to claim your prize"', "url": "https://prize.example/win"}


def test_quotes_keep_whole_words():
    text = "Fresh pairs. DM to order, Zelle only. 1:1 replica, ships fast and cheap all over the world today"
    assert listing._quote(text, "replica", 20) == "…order, Zelle only. 1:1 replica, ships fast and cheap…"


def test_servers_archives_and_footprint_link_to_their_source():
    server = {"ips": ["198.51.100.9"], "org": "Example Corp", "network": "EXAMPLECORP"}
    own = listing.assess("https://examplecorp.example/p", PRODUCT_HTML, {"age_days": 2000}, [], [], server=server)
    assert _proof(own, "Runs its own network") == {"text": "Server 198.51.100.9: Example Corp", "url": "https://bgp.he.net/ip/198.51.100.9"}
    flagged = [{"host": "fake-a.example", "same_ips": ["203.0.113.7"], "same_site": False, "same_url": False, "score": 80}]
    shared = listing.assess("https://fake-b.example/p", PRODUCT_HTML, {"age_days": 400}, [], flagged, server={"ips": ["203.0.113.7"]})
    assert _proof(shared, "Shares a server with flagged sites") == {"text": "Shared IP 203.0.113.7", "url": "https://bgp.he.net/ip/203.0.113.7"}
    copy = listing.assess("https://shop.example/p", PRODUCT_HTML, {"age_days": 900}, [], [], archived="2026-08-19T10:20:30+00:00")
    assert _proof(copy, "Read an archived copy") == {"text": "Wayback Machine copy from 2026-08-19",
                                                    "url": "https://web.archive.org/web/20260819102030/https://shop.example/p"}
    fp = listing.web_footprint(FOOTPRINT_ROWS, "brightowls.com")
    r = listing.assess("https://brightowls.com/", None, {}, [], [], footprint=fp)
    assert _proof(r, "Following elsewhere") == {"text": "351,000 followers on Instagram", "url": "https://www.instagram.com/brightowls/"}
    assert fp["accounts"][1]["url"] == "https://www.facebook.com/brightowlsco/"  # the profile, not the video the search found
    assert _proof(r, "Review-site page") == {"text": "trustpilot.com, 1,184 reviews", "url": "https://www.trustpilot.com/review/brightowls.com"}
    assert _proof(r, "Discussed online")["url"] == "https://www.reddit.com/r/Lamps/comments/1/bright_owls_a_good_buy/"


def test_earlier_findings_say_how_many_and_when():
    before = [{"host": "shop.example", "same_site": True, "same_url": False, "score": 80, "found_at": "2026-10-09T08:00:00",
               "verification": "verified"},
              {"host": "shop.example", "same_site": True, "same_url": False, "score": 70, "found_at": "2026-10-10T09:00:00",
               "verification": "verified"}]
    r = listing.assess("https://shop.example/p", PRODUCT_HTML, {"age_days": 900}, [], before)
    assert _proof(r, "Seen in prior investigations") == {"text": "2 earlier finding(s), latest 2026-10-10", "url": None}


def test_reasons_without_evidence_have_no_proof():
    r = listing.assess("https://shop.example/p", PRODUCT_HTML, {}, [], [])
    assert "proof" not in _labels(r)["Encrypted connection"] and "proof" not in _labels(r)["Domain age unavailable"]
