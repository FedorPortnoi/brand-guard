"""Step 8: links from social sites beyond Instagram (Facebook, TikTok, X, Threads, Pinterest, ...)."""
import asyncio
import sys
import types

import httpx

from app import infra, listing, net, rdap


def test_more_social_sites_are_recognised_by_name():
    for url, name in (("https://www.threads.net/@kith/post/DAEQ9dguXLA", "Threads"), ("https://www.threads.com/@kith", "Threads"),
                      ("https://www.snapchat.com/add/kicks", "Snapchat"), ("https://www.pinterest.com/pin/1/", "Pinterest"),
                      ("https://www.linkedin.com/company/kith", "LinkedIn"), ("https://twitter.com/kith", "X")):
        assert listing._platform(listing.risk.host_of(url)) == ("social", name), url


def test_profile_links_name_an_account_and_posts_do_not():
    assert listing.profile_handle("https://www.instagram.com/luckyfours/") == "luckyfours"
    assert listing.profile_handle("https://www.tiktok.com/@temu") == "temu"
    assert listing.profile_handle("https://www.facebook.com/shoptemu/") == "shoptemu"
    for post in ("https://www.instagram.com/p/C1a2B3c4D5e/", "https://www.tiktok.com/@kith/video/1",
                 "https://www.facebook.com/marketplace/item/1/", "https://www.instagram.com/reels/", "https://www.facebook.com/"):
        assert listing.profile_handle(post) == "", post


def test_follower_counts_written_without_spaces():
    m = listing._FOLLOWERS.search("232Following · 4.2MFollowers · 32.4MLikes")
    assert listing._number(m.group(1), m.group(2)) == 4_200_000


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def test_oembed_reads_who_posted_and_the_caption(monkeypatch):
    calls = []

    def get(url, **kw):
        calls.append(url)
        if url.startswith("https://www.tiktok.com/oembed"):
            return _Resp(200, {"author_name": "Kith", "author_url": "https://www.tiktok.com/@kith", "title": "Kith x adidas, available now"})
        return _Resp(200, {"author_name": "Kith", "author_url": "https://x.com/KITH",
                           "html": '<blockquote><p lang="en">Restock Friday 11am</p>&mdash; Kith</blockquote>'})
    monkeypatch.setattr(listing.httpx, "get", get)
    tt = listing.oembed_info("https://www.tiktok.com/@kith/video/7643814342432935199")
    assert (tt["account"], tt["account_url"], tt["caption"]) == ("Kith", "https://www.tiktok.com/@kith", "Kith x adidas, available now")
    assert calls[0] == "https://www.tiktok.com/oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40kith%2Fvideo%2F7643814342432935199"
    x = listing.oembed_info("https://x.com/KITH/status/1801249955643838780")
    assert x["caption"] == "Restock Friday 11am"
    assert listing.oembed_info("https://www.instagram.com/p/x/") is None  # no public oEmbed: nothing is fetched
    assert len(calls) == 2


def test_social_info_falls_back_to_oembed_when_yt_dlp_is_refused(monkeypatch):
    class Refused:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, *a, **k): raise RuntimeError("Unexpected response from webpage request")
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=Refused))
    monkeypatch.setattr(listing, "oembed_info", lambda url: {"account": "Kith", "title": "from oEmbed"})
    assert listing.social_info("https://www.tiktok.com/@kith/video/1") == {"account": "Kith", "title": "from oEmbed"}


ROWS = [{"page_url": "https://www.tiktok.com/@temu", "title": "TikTok - Make Your Day",
         "description": "232Following · 4.2MFollowers · 32.4MLikes · SHOP LIKE A BILLIONAIRE"},
        {"page_url": "https://www.tiktok.com/@temu.fans", "title": "fans", "description": "9M Followers"},
        {"page_url": "https://www.instagram.com/temu/", "title": "Temu", "description": "12M followers"}]


def test_a_profile_behind_a_login_is_read_from_search(monkeypatch):
    async def rows(query):
        assert query == "TikTok temu"
        return ROWS
    monkeypatch.setattr(listing, "_web_rows", rows)
    found = asyncio.run(listing.profile_from_search("https://www.tiktok.com/@temu", "TikTok"))
    assert (found["account"], found["followers"]) == ("@temu", 4_200_000)  # not the fan account, not Instagram
    assert asyncio.run(listing.profile_from_search("https://www.tiktok.com/@kith/video/1", "TikTok")) is None


def test_redirects_to_a_login_page_are_not_followed(monkeypatch):
    def handler(request):
        if request.url.path == "/luckyfours/":
            return httpx.Response(302, headers={"location": "https://www.instagram.com/accounts/login/?next=%2Fluckyfours%2F"})
        return httpx.Response(200)

    async def safe(url): return True
    monkeypatch.setattr(net, "safe_url_async", safe)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await listing.final_url(c, "https://www.instagram.com/luckyfours/")
    assert asyncio.run(go()) == "https://www.instagram.com/luckyfours/"
    assert listing.LOGIN_PATH.search("/login.php") and not listing.LOGIN_PATH.search("/blog/login-tips")


def test_an_app_shell_never_supplies_the_posts_title(monkeypatch):
    shell = b"<html><head><title>TikTok - Make Your Day</title></head><body></body></html>"

    async def fetch_html(client, url): return shell
    async def lookup(domain, client=None): return {}
    async def describe(host, client):
        return {"ips": [], "network": None, "org": None, "range": None, "shared": None, "platform": True}
    async def safe(url): return True
    async def final(client, url): return url
    monkeypatch.setattr(net, "fetch_html", fetch_html)
    monkeypatch.setattr(net, "safe_url_async", safe)
    monkeypatch.setattr(rdap, "lookup", lookup)
    monkeypatch.setattr(infra, "describe", describe)
    monkeypatch.setattr(listing, "final_url", final)
    monkeypatch.setattr(listing, "social_info", lambda url: {"account": "Kith", "title": "Kith x adidas, available now"})
    r = asyncio.run(listing.check_url("https://www.tiktok.com/@kith/video/1", log=False))
    assert r["facts"]["title"] == "Kith x adidas, available now" and r["verdict"] == "green"


def test_an_accounts_big_following_is_a_reason_with_proof():
    social = {"account": "@shoptemu", "account_url": "https://www.facebook.com/shoptemu/", "followers": 12_123_214}
    r = listing.assess("https://www.facebook.com/shoptemu/", None, {}, [], [], social=social)
    acct = {s["label"]: s for s in r["signals"]}["The account"]
    assert acct["tone"] == "good" and acct["detail"] == "@shoptemu, 12,123,214 followers."
    assert acct["proof"] == {"text": "@shoptemu", "url": "https://www.facebook.com/shoptemu/"}
    post = listing.assess("https://www.tiktok.com/@kith/video/1", None, {}, [], [], social={**social, "followers": 900})
    assert {s["label"]: s for s in post["signals"]}["Posted by"]["tone"] == "neutral"  # a small following proves nothing


def test_a_posts_author_gets_followers_from_their_profile(monkeypatch):
    searched = []

    async def found(url, platform):
        searched.append(url)
        return {"account": "@kith", "account_url": "https://www.tiktok.com/@kith/", "followers": 3_100_000}
    _offline_social(monkeypatch, {"account": "Kith", "account_url": "https://www.tiktok.com/@kith", "title": "Kith x adidas"})
    monkeypatch.setattr(listing, "profile_from_search", found)
    r = asyncio.run(listing.check_url("https://www.tiktok.com/@kith/video/1", log=False))
    assert searched == ["https://www.tiktok.com/@kith"]
    assert r["facts"]["followers"] == 3_100_000 and r["facts"]["account"] == "Kith"  # what the post said wins


def _offline_social(monkeypatch, social):
    async def fetch_html(client, url): return None
    async def lookup(domain, client=None): return {}
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


def test_a_social_link_is_named_by_its_account_and_an_account_link_is_not_a_product(monkeypatch):
    from app import ask
    _offline_social(monkeypatch, {"account": "@shoptemu", "account_url": "https://www.facebook.com/shoptemu/", "followers": 12_123_214})

    async def none(*a, **k): return None
    monkeypatch.setattr(ask, "real_brand", none)
    r = asyncio.run(ask.ask("https://www.facebook.com/shoptemu/"))
    assert r["found"]["what"] == "@shoptemu on Facebook"
    assert r["alternatives"] == [] and r["alternatives_note"] == ask.AN_ACCOUNT
