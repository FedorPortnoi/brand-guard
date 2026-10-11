"""URL-first listing checks and trust-first comparison research.

The output is deliberately phrased as risk signals, not a guarantee that a seller is safe.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup

from . import archive, infra, investigations, net, paid, rdap, reputation, risk, search
from .sellers import registrable_domain
from .store import UA

# how a social account sells outside the platform's checkout (no buyer protection), common with fake goods
OFF_PLATFORM = ("dm to order", "dm for price", "dm for prices", "dm me to order", "inbox for price", "order on whatsapp",
                "whatsapp", "telegram", "wechat", "cash app", "cashapp", "zelle", "venmo", "paypal friends")
# unambiguous counterfeit terms, checked in descriptions and captions too (weaker words cause false alarms there)
COUNTERFEIT_TERMS = ("replica", "fake", "1:1", "knockoff", "super copy", "mirror quality", "aaa quality", "aaa+", "dupe")
# registrars that mostly serve large companies protecting their brands; fake shops rarely use them
BRAND_REGISTRARS = ("csc corporate", "markmonitor", "com laude", "safenames", "corsearch", "brandsight", "nom-iq")
# wholesale and factory marketplaces: where copies are sourced, and where white-label products are listed by their makers
SUPPLIER_SITES = {"alibaba.com": "Alibaba", "1688.com": "1688", "made-in-china.com": "Made-in-China",
                  "dhgate.com": "DHgate", "globalsources.com": "Global Sources", "indiamart.com": "IndiaMART"}


PLATFORM_NAMES = {
    "amazon": "Amazon", "ebay": "eBay", "etsy": "Etsy", "aliexpress": "AliExpress",
    "alibaba": "Alibaba", "temu": "Temu", "wish.com": "Wish", "shein": "SHEIN",
    "facebook": "Facebook", "instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube",
    "reddit": "Reddit", "x.com": "X", "twitter": "X", "pinterest": "Pinterest", "threads": "Threads",
    "snapchat": "Snapchat", "linkedin": "LinkedIn", "tumblr": "Tumblr", "twitch": "Twitch",
}
# public oEmbed endpoints (no key): who posted and the caption, when the page itself needs a login or blocks reading
OEMBED = {"tiktok": "https://www.tiktok.com/oembed?url=", "x.com": "https://publish.twitter.com/oembed?url=",
          "twitter": "https://publish.twitter.com/oembed?url="}


ARTICLE_PATH = re.compile(r"/(blogs?|posts?|news|articles?|guides?|reviews?|magazine|journal|stories)/", re.I)
PRODUCT_PATH = re.compile(r"/(dp|gp/product|itm|p|product|products|item|items|listing|listings)/", re.I)


class ListingError(ValueError):
    pass


def normalize_url(value: str) -> str:
    value = (value or "").strip()
    if value and "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or re.search(r"\s", parsed.hostname):
        raise ListingError("Enter a complete public listing URL.")
    return value


def _name(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _name(value.get("name"))
    return None


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _money(value) -> float | None:
    try:
        return round(float(str(value).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def page_facts(html: bytes | None, page_url: str) -> dict:
    """Extract only facts exposed by the page, especially Product JSON-LD."""
    empty = {"title": None, "description": None, "site_name": None, "product": None, "brand": None, "manufacturer": None,
             "manufacturer_url": None, "seller": None, "price": None, "currency": None, "availability": None, "image": None}
    if not html:
        return empty
    soup = BeautifulSoup(html, "lxml")
    title = soup.select_one('meta[property="og:title"]')
    desc = soup.select_one('meta[property="og:description"], meta[name="description"]')
    site_name = soup.select_one('meta[property="og:site_name"], meta[name="application-name"]')
    image = soup.select_one('meta[property="og:image"], meta[name="twitter:image"]')
    facts = {**empty,
             "image": (image.get("content") or "").strip() or None if image else None,
             "title": ((title.get("content") if title else None) or (soup.title.string if soup.title else None) or "").strip() or None,
             "description": (desc.get("content") or "").strip() if desc else None,
             "site_name": (site_name.get("content") or "").strip() if site_name else None}
    docs = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            docs.append(json.loads(tag.string or tag.get_text() or ""))
        except (json.JSONDecodeError, TypeError):
            continue
    def types(node: dict) -> list:
        value = node.get("@type")
        return [value] if isinstance(value, str) else value if isinstance(value, list) else []

    product = next((node for doc in docs for node in _walk_json(doc) if "Product" in types(node)), None)
    if product:
        pic = product.get("image")
        pic = pic[0] if isinstance(pic, list) and pic else pic
        pic = pic.get("url") if isinstance(pic, dict) else pic
        if isinstance(pic, str) and not facts["image"]:
            facts["image"] = pic
        facts["product"] = _name(product.get("name"))
        facts["brand"] = _name(product.get("brand"))
        facts["manufacturer"] = _name(product.get("manufacturer"))
        maker = product.get("manufacturer")
        if isinstance(maker, dict):
            facts["manufacturer_url"] = maker.get("url") or maker.get("sameAs")
        offer = product.get("offers")
        if isinstance(offer, list):
            offer = next((x for x in offer if isinstance(x, dict)), None)
        if isinstance(offer, dict):
            facts["price"] = _money(offer.get("price") or offer.get("lowPrice"))
            facts["currency"] = offer.get("priceCurrency")
            facts["availability"] = (offer.get("availability") or "").rsplit("/", 1)[-1] or None
            facts["seller"] = _name(offer.get("seller"))
    if facts["price"] is None:
        amount = soup.select_one('meta[property="product:price:amount"], meta[itemprop="price"]')
        currency = soup.select_one('meta[property="product:price:currency"], meta[itemprop="priceCurrency"]')
        facts["price"] = _money(amount.get("content")) if amount else None
        facts["currency"] = (currency.get("content") or "").strip() if currency else facts["currency"]
    return facts


def _platform(host: str) -> tuple[str | None, str | None]:
    for value, name in PLATFORM_NAMES.items():
        if risk.host_matches(host, value):
            kind = "social" if value in risk.SOCIAL else "marketplace"
            return kind, name
    for value in risk.SOCIAL + risk.MARKETPLACES:
        if risk.host_matches(host, value):
            return ("social" if value in risk.SOCIAL else "marketplace"), registrable_domain(host)
    return None, None


def shows_post(html: bytes | None) -> bool:
    """A social site's page says something about the post only when it carries the post's own metadata (og: or
    twitter: title/description). Without a login, Instagram and others send the same app shell for every post, real
    or not, titled only with the platform's name."""
    if not html:
        return False
    soup = BeautifulSoup(html, "lxml")
    return any((m.get("content") or "").strip() for m in soup.select(
        'meta[property="og:title"], meta[property="og:description"], meta[name="twitter:title"], meta[name="twitter:description"]'))


STORE_WORDS = ("add to cart", "add to bag", "add to basket", "buy now", "checkout", "check out", "shopping cart",
               "shopping bag", "in stock", "out of stock", "sold out", "shop now", "free shipping", "free returns")
STORE_MARKS = ("/cart", "/checkout", "cdn.shopify.com", "woocommerce", "bigcommerce", "/products/", "/collections/")


def sells(html: bytes | None, facts: dict) -> bool | None:
    """Whether a page sells anything: a product or price, shop words, a cart or checkout, a shop platform's marks,
    or a payment method. None when there is no page to tell from."""
    if not html:
        return None
    if facts.get("product") or facts.get("price") is not None:
        return True
    raw = html.decode("utf-8", "ignore").lower()
    if any(m in raw for m in STORE_MARKS):
        return True
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    if _hits(STORE_WORDS, soup.get_text(" ").lower()):
        return True
    return bool(paid.methods(html))


def lens_url(image_url: str | None) -> str | None:
    """Google Lens for an image, opened in the user's own browser: free and needs no key."""
    return f"https://lens.google.com/uploadbyurl?url={quote(image_url, safe='')}" if image_url else None


def social_info(url: str) -> dict | None:
    """Account and caption of a public post (TikTok, YouTube, Instagram, X...), read by yt-dlp without downloading
    the video. None when the platform refuses (Instagram usually wants a login)."""
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15,
                               "noplaylist": True, "extract_flat": "in_playlist"}) as ydl:
            i = ydl.extract_info(url, download=False) or {}
    except Exception:
        return oembed_info(url)
    ts = i.get("timestamp")
    account = i.get("uploader") or i.get("channel") or i.get("uploader_id")
    if not (account or i.get("title")):
        return oembed_info(url)
    return {"account": account, "account_url": i.get("uploader_url") or i.get("channel_url"),
            "followers": i.get("channel_follower_count"), "verified": i.get("channel_is_verified"),
            "title": i.get("title"), "caption": (i.get("description") or "")[:2000], "image": i.get("thumbnail"),
            "posted": datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if ts else None}


def oembed_info(url: str) -> dict | None:
    """Who posted and the caption, from the platform's public oEmbed endpoint (TikTok, X). None when it has none."""
    host = risk.host_of(url)
    api = next((v for k, v in OEMBED.items() if risk.host_matches(host, k)), None)
    if not api:
        return None
    try:
        r = httpx.get(api + quote(url, safe=""), timeout=12, headers=UA, follow_redirects=True)
        j = r.json() if r.status_code == 200 else {}
    except Exception:
        return None
    caption = j.get("title") or ""
    if not caption and j.get("html"):  # X puts the post's text in its embed HTML
        p = BeautifulSoup(j["html"], "lxml").find("p")
        caption = p.get_text(" ", strip=True) if p else ""
    if not (j.get("author_name") or caption):
        return None
    return {"account": j.get("author_name"), "account_url": j.get("author_url"), "followers": None, "verified": None,
            "title": caption[:200] or None, "caption": caption[:2000], "image": j.get("thumbnail_url"), "posted": None}


def profile_handle(url: str) -> str:
    """The account a social profile link names (instagram.com/kith, tiktok.com/@temu, facebook.com/shoptemu);
    empty for a post, a video or a platform page."""
    path = [p for p in urlparse(url).path.split("/") if p]
    if len(path) != 1:
        return ""
    handle = path[0].lstrip("@")
    return "" if handle.lower() in _NOT_AN_ACCOUNT or "." in handle and handle.endswith((".php", ".html")) else handle


async def profile_from_search(url: str, platform: str | None) -> dict | None:
    """A login-walled profile's name and followers, from how a web search shows that profile page
    ("Temu. 12,123,214 followers"). BG_FOOTPRINT=0 turns it off (tests do)."""
    handle = profile_handle(url)
    if not handle or len(_squash(handle)) < 3:
        return None
    rows = await _web_rows(f"{platform or ''} {handle}".strip())
    if not rows:
        return None
    host = risk.host_of(url)
    mine = [r for r in rows if registrable_domain(risk.host_of(r.get("page_url") or "")) == registrable_domain(host)]
    found = web_footprint(mine, handle, name=handle)["accounts"]
    same = next((a for a in found if a["handle"].lower() == handle.lower()), None)
    if not same:
        return None
    row = next((r for r in mine if profile_handle(r.get("page_url") or "").lower() == handle.lower()), None) or {}
    return {"account": f"@{handle}", "account_url": same["url"], "followers": same["followers"], "verified": None,
            "title": (row.get("title") or "")[:200] or None, "caption": (row.get("description") or "")[:2000],
            "image": None, "posted": None}


LOGIN_PATH = re.compile(r"/(?:accounts/)?(?:log-?in|sign-?in|signup|checkpoint|auth)(?:/|\.php|$)", re.I)


async def final_url(client: httpx.AsyncClient, url: str) -> str:
    """Follow short links (bit.ly, t.co, vm.tiktok.com) to the page they point to, checking every hop."""
    for _ in range(5):
        try:
            r = await client.head(url, follow_redirects=False)
        except Exception:
            return url
        loc = r.headers.get("location")
        if r.status_code not in (301, 302, 303, 307, 308) or not loc:
            return url
        nxt = str(httpx.URL(url).join(loc))
        if LOGIN_PATH.search(urlparse(nxt).path) or not await net.safe_url_async(nxt):
            return url  # a login wall is not where the link goes: keep the link the shopper sent
        url = nxt
    return url


def _hits(words, text: str) -> list[str]:
    """Whole words only: "copy" in "copyright" or "aaa" in a model number is not counterfeit wording."""
    def pattern(w: str) -> str:  # a boundary only where the phrase starts / ends with a letter or digit ("% off")
        return (r"(?<![a-z0-9])" if w[0].isalnum() else "") + re.escape(w) + (r"(?![a-z0-9])" if w[-1].isalnum() else "")
    return [w for w in words if re.search(pattern(w), text)]


def _signal(label: str, detail: str, tone: str, impact: int = 0) -> dict:
    return {"label": label, "detail": detail, "tone": tone, "impact": impact}


def _quote(text: str, word: str, width: int = 40) -> str:
    """The words around `word` in text, as the shopper would see them: '…DM to order, Zelle only…'."""
    m = re.search(re.escape(word), text, re.I)
    if not m:
        return ""
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    while 0 < a < m.start() and not text[a - 1].isspace():  # whole words only
        a -= 1
    while m.end() < b < len(text) and not text[b].isspace():
        b += 1
    return ("…" if a else "") + re.sub(r"\s+", " ", text[a:b]).strip() + ("…" if b < len(text) else "")


def _proofs(url: str, host: str, domain: dict, threats: list[str], server: dict, social: dict | None,
            footprint: dict | None, archived: str, page_text: str, seen_text: str, hits: dict[str, list[str]],
            linked_ips: list[str], earlier: list[dict]) -> dict[str, dict]:
    """What backs each reason, by its label: {text, url}. A date, a link anyone can open, or a quote."""
    out: dict[str, dict] = {}
    site = registrable_domain(host)
    registry = f"https://lookup.icann.org/en/lookup?name={site}"
    if domain.get("created"):
        for label in ("Very new domain", "New domain", "Young domain", "Established domain"):
            out[label] = {"text": f"Registered {domain['created'][:10]}", "url": registry}
    if domain.get("registrar"):
        out["Brand-protection registrar"] = {"text": f"Registrar: {domain['registrar']}", "url": registry}
    if threats:
        out["Known threat warning"] = {"text": "Safe Browsing: " + ", ".join(t.replace("_", " ").lower() for t in threats),
                                       "url": f"https://transparencyreport.google.com/safe-browsing/search?url={quote(url, safe='')}"}
    ips = server.get("ips") or []
    if ips:
        out["Runs its own network"] = {"text": f"Server {ips[0]}: {server.get('org') or server.get('network')}",
                                       "url": f"https://bgp.he.net/ip/{ips[0]}"}
    if linked_ips:
        out["Shares a server with flagged sites"] = {"text": f"Shared IP {linked_ips[0]}", "url": f"https://bgp.he.net/ip/{linked_ips[0]}"}
    for label, words in hits.items():
        for w in words:
            if q := _quote(seen_text, w):
                out[label] = {"text": f"\"{q}\" (from your screenshot)", "url": None}
            elif q := _quote(page_text, w):
                out[label] = {"text": f"\"{q}\"", "url": url}
            elif w.lower() in url.lower():
                out[label] = {"text": f"In the address: {url}", "url": None}
            else:
                continue
            break
    if archived:
        stamp = re.sub(r"\D", "", archived)[:14]
        out["Read an archived copy"] = {"text": f"Wayback Machine copy from {archived[:10]}" if stamp else "Wayback Machine copy",
                                        "url": f"https://web.archive.org/web/{stamp or '*'}/{url}"}
    if social and social.get("account_url"):
        out["Posted by"] = {"text": social.get("account") or "The account", "url": social["account_url"]}
    if footprint:
        accounts = footprint.get("accounts") or []
        known = [a for a in accounts if a.get("followers")]
        if known:
            out["Following elsewhere"] = {"text": f"{known[0]['followers']:,} followers on {known[0]['platform']}", "url": known[0]["url"]}
        if accounts:
            out["Accounts elsewhere"] = {"text": f"@{accounts[0]['handle']} on {accounts[0]['platform']}", "url": accounts[0]["url"]}
        if footprint.get("reviews"):
            r = footprint["reviews"][0]
            out["Review-site page"] = {"text": f"{r['site']}" + (f", {r['count']:,} reviews" if r.get("count") else ""), "url": r["url"]}
        if footprint.get("threads"):
            t = footprint["threads"][0]
            out["Discussed online"] = {"text": t["title"][:80], "url": t["url"]}
    if earlier:
        dates = sorted(d for x in earlier if (d := x.get("found_at") or x.get("started_at")))
        text = f"{len(earlier)} earlier finding(s)" + (f", latest {dates[-1][:10]}" if dates else "")
        out["Seen in prior investigations"] = out["Seen before"] = {"text": text, "url": None}
    return out


NOT_LEGIT = 35  # the one cut-off: a score this high is Not legit, lower is Legit (owner, 10.10; tune from real results)
VERY_NEW_DOMAIN = 50  # registered under 30 days ago (owner, 10.10)
REVIEW_SITES = ("trustpilot.com", "sitejabber.com", "bbb.org", "reviews.io", "resellerratings.com")
DISCUSSION_SITES = ("reddit.com", "quora.com")
BIG_FOLLOWING = 10_000
_FOLLOWERS = re.compile(r"(\d[\d.,]*)\s*([kmb])?\s*(?:followers|follower|seguidores|abonnés|abonnenten|подписчик\w*)", re.I)  # "4.2MFollowers" too
_REVIEW_COUNT = re.compile(r"(\d[\d,]*)\s+reviews?\b", re.I)
_NOT_AN_ACCOUNT = {"reel", "reels", "p", "tv", "watch", "videos", "video", "posts", "groups", "pages", "hashtag", "explore",
                   "shorts", "status", "search", "share", "stories", "events", "marketplace", "pin", "results"}


def _number(digits: str, suffix: str | None) -> int:
    """351K -> 351000, 1.2M -> 1200000, 12,123,214 -> 12123214."""
    if suffix:
        return int(float(digits.replace(",", ".")) * {"k": 1e3, "m": 1e6, "b": 1e9}[suffix.lower()])
    return int(re.sub(r"[.,]", "", digits))


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def web_footprint(rows: list[dict], domain: str, name: str = "") -> dict:
    """What the rest of the web shows about a store whose own page can't be read, from a search for its domain:
    its own social accounts and their followers, review-site pages about it, discussion threads naming it.
    A row counts only when it is about this store: an account named after it, a review page or thread naming it."""
    name = _squash(name or domain.split(".")[0])
    out = {"accounts": [], "reviews": [], "threads": []}
    if len(name) < 3:
        return out

    def names_it(text: str) -> bool:  # short names must stand as a word ("kith", not "kitchen")
        return name in _squash(text) if len(name) >= 6 else bool(re.search(rf"(?<![a-z0-9]){name}(?![a-z0-9])", text.lower()))

    for row in rows:
        url = row.get("page_url") or ""
        host = risk.host_of(url)
        path = [p for p in urlparse(url).path.split("/") if p]
        text = f"{row.get('title') or ''} {row.get('description') or ''}"
        if any(risk.host_matches(host, s) for s in REVIEW_SITES):
            if domain in url.lower() or names_it(" ".join(path).replace("-", " ")):
                count = _REVIEW_COUNT.search(text)
                out["reviews"].append({"site": registrable_domain(host), "url": url,
                                       "count": _number(count.group(1), None) if count else None})
        elif any(risk.host_matches(host, s) for s in DISCUSSION_SITES):
            if names_it(row.get("title") or ""):
                out["threads"].append({"site": registrable_domain(host), "url": url, "title": row.get("title") or ""})
        elif any(risk.host_matches(host, s) for s in risk.SOCIAL) and path:
            handle = path[0].lstrip("@")
            if handle.lower() in _NOT_AN_ACCOUNT or name not in _squash(handle):
                continue
            m = _FOLLOWERS.search(text)
            followers = _number(m.group(1), m.group(2)) if m else None
            platform = _platform(host)[1]
            same = next((a for a in out["accounts"] if a["platform"] == platform and a["handle"].lower() == handle.lower()), None)
            if same:
                same["followers"] = max(same["followers"] or 0, followers or 0) or None
            else:
                # the profile itself, even when the row was one of its videos or posts
                out["accounts"].append({"platform": platform, "handle": handle, "url": f"https://{urlparse(url).netloc}/{path[0]}/", "followers": followers})
    out["accounts"].sort(key=lambda a: -(a["followers"] or 0))
    return out


def _footprint_signals(fp: dict) -> tuple[list[dict], int]:
    signals, score = [], 0
    known = [a for a in fp.get("accounts") or [] if a["followers"]]
    if known:
        top = known[0]
        big = top["followers"] >= BIG_FOLLOWING
        detail = ", ".join(f"@{a['handle']} on {a['platform']} has {a['followers']:,} followers" for a in known[:2]) + "."
        signals.append(_signal("Following elsewhere", "Its own accounts: " + detail, "good" if big else "neutral", -6 if big else 0))
        score -= 6 if big else 0
    elif fp.get("accounts"):
        a = fp["accounts"][0]
        signals.append(_signal("Accounts elsewhere", f"It has its own account on {a['platform']} (@{a['handle']}).", "neutral"))
    for r in (fp.get("reviews") or [])[:1]:
        signals.append(_signal("Review-site page", f"{r['site']} has a page about it" + (f" with {r['count']:,} reviews." if r["count"] else "."), "neutral"))
    if fp.get("threads"):
        t = fp["threads"]
        signals.append(_signal("Discussed online", f"{len(t)} discussion thread(s) about it, e.g. \"{t[0]['title'][:80]}\" on {t[0]['site']}.", "neutral"))
    return signals, score


def assess(url: str, html: bytes | None, domain: dict, threats: list[str], history: list[dict], *,
           server: dict | None = None, social: dict | None = None, brand: str = "", official: list[str] = (),
           resellers: list[str] = (), claimed_brand: str | None = None, seen_text: str = "", archived: str = "",
           footprint: dict | None = None) -> dict:
    """The verdict from everything gathered. brand / official / resellers are the user's optional brand settings;
    claimed_brand is the brand the page itself names; seen_text is what the shopper saw (text read from their
    screenshot), checked for the same wording as a caption; footprint is web_footprint() for a store whose page
    can't be read. Three verdicts: green = Legit, red = Not legit (score >= NOT_LEGIT), yellow = Couldn't check
    (no page, live or archived, and no evidence from anywhere else). Pure, so every rule is testable."""
    host = risk.host_of(url)
    kind, platform = _platform(host)
    server = server or {}
    # Whole-page text contains unrelated footer, legal and navigation language. Restrict wording signals to
    # listing-facing metadata so a legitimate returns policy cannot make an entire store look fraudulent.
    if html:
        soup = BeautifulSoup(html, "lxml")
        meta = soup.select_one('meta[property="og:title"], meta[name="twitter:title"]')
        desc = soup.select_one('meta[property="og:description"], meta[name="description"]')
        visible_title = (meta.get("content") if meta else None) or (soup.title.get_text(" ", strip=True) if soup.title else "")
        visible_desc = desc.get("content") if desc else ""
    else:
        visible_title = visible_desc = ""
    if social:
        visible_title = f"{visible_title} {social.get('title') or ''}"
        visible_desc = f"{visible_desc} {social.get('caption') or ''}"
    text = f"{url.lower()} {visible_title} {visible_desc} {seen_text}".lower()
    head = f"{url} {visible_title}".lower()  # weak words ("copy", "cheap", "outlet") only count in a title or address
    signals, score = [], 0
    vouched = False  # strong evidence the site belongs to the brand: settles a verdict the page alone cannot

    if official and risk.on_domains(host, official):
        vouched = True
        signals.append(_signal("The brand's own site", "This address is on the official domains in your brand settings.", "good", -60))
    elif resellers and risk.on_domains(host, resellers):
        vouched = True
        signals.append(_signal("Authorised reseller", "This address is on the reseller list in your brand settings.", "good", -45))

    if urlparse(url).scheme == "https":
        signals.append(_signal("Encrypted connection", "The page uses HTTPS.", "good", -2))
    else:
        score += 18
        signals.append(_signal("No HTTPS", "Payment or personal details could be exposed in transit.", "bad", 18))

    if threats:
        score += 70
        names = ", ".join(x.replace("_", " ").lower() for x in threats)
        signals.append(_signal("Known threat warning", f"Google Safe Browsing reports: {names}.", "bad", 70))

    age = domain.get("age_days") if not kind else None  # a marketplace's or social site's age says nothing about the seller
    if kind:
        pass
    elif age is not None and age < 30:  # on its own enough for Not legit: too new to tell a company from a scam (owner, 10.10)
        score += VERY_NEW_DOMAIN
        signals.append(_signal("Very new domain", f"The domain was registered {age} days ago.", "bad", VERY_NEW_DOMAIN))
    elif age is not None and age < 180:
        score += 22
        signals.append(_signal("New domain", f"The domain is about {max(1, age // 30)} months old.", "warn", 22))
    elif age is not None and age < 365:
        score += 10
        signals.append(_signal("Young domain", "The domain is less than one year old.", "warn", 10))
    elif age is not None:
        years = max(1, age // 365)
        signals.append(_signal("Established domain", f"The domain has existed for about {years} years.", "good", -4))
    else:
        signals.append(_signal("Domain age unavailable", "Registration history could not be confirmed.", "neutral"))

    label = registrable_domain(host).split(".")[0].replace("-", "") if not kind else ""
    registrar = (domain.get("registrar") or "").lower()
    if label and any(r in registrar for r in BRAND_REGISTRARS):
        score -= 8
        if age is not None and age > 3 * 365:
            vouched = vouched or not official
        signals.append(_signal("Brand-protection registrar", f"Registered through {domain['registrar']}, a registrar large brands use to protect their names.", "good", -8))
    owner = f"{server.get('org') or ''} {server.get('network') or ''}".lower().replace("-", "").replace(" ", "")
    if label and len(label) >= 4 and label in owner:
        score -= 8
        vouched = vouched or (age is not None and age > 3 * 365 and not official)
        signals.append(_signal("Runs its own network", f"The site's server belongs to {server.get('org') or server.get('network')}, the company itself, not a rented shop platform.", "good", -8))
    elif server.get("shared"):
        signals.append(_signal("Shared hosting", f"Hosted on {server['shared']}, which also hosts many unrelated sites. That is normal and neither good nor bad.", "neutral"))

    claimed = brand or claimed_brand or ""
    token = re.sub(r"[^a-z0-9]", "", claimed.lower())
    if label and len(token) >= 4 and token in label and label != token and not risk.on_domains(host, official):
        score += 30
        signals.append(_signal("Brand name in a longer address", f"The address adds words to \"{claimed}\" ({registrable_domain(host)}), a common pattern for fake stores"
                               + (" and it is not on the brand's official list." if official else "."), "bad", 30))

    scam_hits = _hits(risk.SCAM_WORDS, text)
    suspect_hits = _hits(risk.SUSPICIOUS_WORDS, head) + [w for w in _hits(COUNTERFEIT_TERMS, text) if w not in head]
    if scam_hits:
        score += 35
        signals.append(_signal("Scam-style language", "Found: " + ", ".join(scam_hits[:3]) + ".", "bad", 35))
    if suspect_hits:
        score += 20
        signals.append(_signal("High-risk sales language", "Found: " + ", ".join(suspect_hits[:3]) + ".", "warn", 20))
    if host.endswith(risk.CHEAP_TLDS):
        score += 12
        signals.append(_signal("Higher-risk domain ending", f"The .{host.rsplit('.', 1)[-1]} ending needs added scrutiny.", "warn", 12))

    if kind == "marketplace":
        score += 10
        signals.append(_signal("Third-party marketplace", f"{platform} hosts independent sellers. Platform history does not verify this seller.", "warn", 10))
    elif kind == "social":
        score += 15
        signals.append(_signal("Social media listing", f"{platform} identity and posts can change quickly. Verify the seller outside the platform.", "warn", 15))
        if social and social.get("account"):
            fol = social.get("followers")
            detail = f"{social['account']}" + (f", {fol:,} followers" if isinstance(fol, int) else "") + (
                ", verified account" if social.get("verified") else "") + (f", posted {social['posted']}" if social.get("posted") else "")
            signals.append(_signal("Posted by", detail + ".", "good" if social.get("verified") else "neutral",
                                   -10 if social.get("verified") else 0))
            score -= 10 if social.get("verified") else 0
    off: list[str] = []
    if kind == "social" or seen_text:
        off = _hits(OFF_PLATFORM, text)
        if off:
            score += 20
            signals.append(_signal("Payment off the platform", "Asks buyers to pay or talk elsewhere: " + ", ".join(off[:3]) + ". That removes buyer protection.", "bad", 20))

    if html or social:
        if html and archived:
            signals.append(_signal("Read an archived copy", "The live site didn't answer us, so BrandGuard read the Wayback Machine's copy"
                                   + (f" from {archived[:10]}." if archived else "."), "neutral"))
        else:
            signals.append(_signal("Page reached", "BrandGuard could inspect the live page." if html else "BrandGuard read the post's details.", "good", -2))
    else:
        score += 12
        signals.append(_signal("Page not inspectable", f"{platform} didn't show us this post without a login." if kind == "social" else
                               "The site didn't answer or blocked reading, and there is no archived copy to read instead.", "warn", 12))
    seen_elsewhere = False
    if footprint:
        found, change = _footprint_signals(footprint)
        signals += found
        score += change
        seen_elsewhere = bool(found)

    linked = sorted({x["host"] for x in history if x.get("same_ips") and not x.get("same_site") and not x.get("same_url")
                     and (x.get("score") or 0) >= 40})
    linked_ips = [ip for x in history if x.get("host") in linked for ip in x.get("same_ips") or []]
    if linked and not server.get("shared"):
        score += 20
        signals.append(_signal("Shares a server with flagged sites", "Same IP address as " + ", ".join(linked[:3]) + ", flagged in earlier BrandGuard investigations.", "bad", 20))
    history = [x for x in history if x.get("same_site", True) or x.get("same_url")]  # the site itself, not its neighbours
    # being looked at before is not a warning: an earlier link check only counts if it came out red
    history = [x for x in history if x.get("verification") != "link-check" or (x.get("score") or 0) >= NOT_LEGIT]
    previous_high = [x for x in history if (x.get("score") or 0) >= 65 and x.get("verification") != "unverified"]
    if previous_high:
        score += 24
        signals.append(_signal("Seen in prior investigations", f"This site has {len(previous_high)} earlier high-risk finding(s) in BrandGuard.", "bad", 24))
    elif history:
        score += 6
        signals.append(_signal("Seen before", "This site or listing appeared in an earlier BrandGuard investigation.", "warn", 6))

    proofs = _proofs(url, host, domain, threats, server, social, footprint, archived,
                     f"{visible_title} {visible_desc}", seen_text,
                     {"Scam-style language": scam_hits, "High-risk sales language": suspect_hits, "Payment off the platform": off},
                     linked_ips, previous_high or history)
    for s in signals:
        if s["label"] in proofs:
            s["proof"] = proofs[s["label"]]

    score = max(0, min(100, score))
    # Couldn't check: nothing to judge by. No page (live or archived), no screenshot text, no domain age,
    # no threat report, nothing about the store elsewhere.
    insufficient = not html and not social and not seen_text and age is None and not threats and not seen_elsewhere
    if vouched and score < 55:  # the brand's own site (settings, or long-held brand-protection signs): only strong risk overrides
        verdict = "green"
    elif insufficient:
        verdict = "yellow"
    else:
        verdict = "red" if score >= NOT_LEGIT else "green"
    labels = {
        "green": ("Legit", "Nothing we checked points to a scam. That is not a guarantee."),
        "yellow": ("Couldn't check", "We couldn't read the site and found nothing about it elsewhere, so there is no verdict."),
        "red": ("Not legit", "Do not pay or share personal details until the seller is independently verified."),
    }
    return {"verdict": verdict, "headline": labels[verdict][0], "summary": labels[verdict][1],
            "score": score, "signals": sorted(signals, key=lambda x: -abs(x["impact"]))[:10], "platform": platform, "host": host,
            "checked": {"page": bool(html), "domain_age": age is not None,
                        "safe_browsing": reputation.safe_browsing_enabled(), "history": True}}


async def check_url(value: str, brand: str = "", official: list[str] = (), resellers: list[str] = (),
                    log: bool = True, seen_text: str = "") -> dict:
    url = normalize_url(value)
    if not await net.safe_url_async(url):
        raise ListingError("That URL is not a reachable public internet address.")
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=12, headers=UA) as client:
        url = await final_url(client, url)
        host = risk.host_of(url)
        kind, _ = _platform(host)
        html_job = net.fetch_html(client, url)
        age_job = rdap.lookup(registrable_domain(host), client) if not kind else asyncio.sleep(0, {})
        safe_job = reputation.ScanReputation([]).safe_browsing(url, client, errors)
        social_job = asyncio.to_thread(social_info, url) if kind == "social" else asyncio.sleep(0, None)
        html, domain, threats, social, server = await asyncio.gather(html_job, age_job, safe_job, social_job,
                                                                     infra.describe(host, client))
        archived, footprint = "", None
        if kind == "social" and not shows_post(html):
            html = None  # a login wall or app shell ("TikTok - Make Your Day"): it says nothing about this post
        if kind == "social" and profile_handle(url) and not (social or {}).get("followers"):
            found = await profile_from_search(url, _platform(host)[1])  # a profile's followers, from search
            social = {**found, **{k: v for k, v in (social or {}).items() if v}} if found else social
        if html is None and kind != "social":  # unreachable from here (blocked network, bot wall): read the archived copy,
            # and, for a store's own site, look for it elsewhere on the web in case there is no copy either
            copy, rows = await asyncio.gather(archive.archived_page(client, url, net.MAX_HTML),
                                              _web_rows(registrable_domain(host)) if not kind else asyncio.sleep(0, None))
            if copy:
                html, archived = copy[0], copy[1] or "an earlier date"
            elif rows is not None:
                footprint = web_footprint(rows, registrable_domain(host))
    check_id = "link-" + uuid.uuid4().hex[:10]
    history = await asyncio.to_thread(_safe, investigations.earlier, check_id, host, url, server["ips"], server["platform"]) or []
    facts = page_facts(html, url)
    if social:
        facts.update({k: v for k, v in (("title", social.get("title")), ("image", social.get("image"))) if v and not facts.get(k)})
        facts.update({"account": social.get("account"), "account_url": social.get("account_url"),
                      "followers": social.get("followers"), "posted": social.get("posted")})
    result = assess(url, html, domain or {}, threats, history, server=server, social=social, brand=brand,
                    official=list(official), resellers=list(resellers), claimed_brand=facts.get("brand"), seen_text=seen_text, archived=archived,
                    footprint=footprint)
    image = facts.get("image") if (facts.get("image") or "").startswith("http") else None
    result.update({"id": check_id, "url": url, "archived": archived, "footprint": footprint, "domain": registrable_domain(host), "facts": facts, "errors": errors,
                   "image_url": image, "lens_url": lens_url(image), "brand_settings": bool(brand or official or resellers),
                   "server": {"ips": server["ips"][:4], "network": server.get("network"), "org": server.get("org"),
                              "shared": server.get("shared"), "platform": server.get("platform")}})
    red = result["verdict"] == "red"  # "Already paid?" steps by the payment methods the shopper could see
    result["already_paid"] = paid.steps(result["verdict"], paid.methods(html, seen_text, (social or {}).get("caption") or "") if red else [],
                                        result["platform"], kind)
    result["sells"] = sells(html, facts) if kind != "social" else None  # a post's page is an app shell: can't tell
    # a post we couldn't see: ask for a screenshot of it (unless the shopper already sent one)
    result["send_screenshot"] = kind == "social" and result["verdict"] == "yellow" and not seen_text
    result["deep_action"] = "Find safer options" if result["verdict"] == "red" else (
        "Compare trusted options" if result["verdict"] == "yellow" else "Trace maker and prices")
    if log:
        _log(result, brand, list(official), list(resellers))
    # the crowd signal: how often shoppers checked this site this week (this check included) and how often it was flagged
    result["crowd"] = await asyncio.to_thread(_safe, investigations.crowd, host, url, bool(kind)) or {}
    return result


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def _log(result: dict, brand: str, official: list[str], resellers: list[str]) -> None:
    """A checked link is a one-listing investigation, so it counts as 'seen before' in later checks and scans."""
    facts = result["facts"]
    _safe(investigations.start, result["id"], brand or facts.get("brand") or result["host"], [], official, resellers, "", "link")
    category = {"green": "low_concern", "yellow": "unauthorized_use", "red": "likely_counterfeit"}[result["verdict"]]
    listing = {"idx": 0, "page_url": result["url"], "title": facts.get("product") or facts.get("title") or "",
               "verification": "link-check", "forensics": {"label": f"Link check: {result['headline']}"},
               "risk": {"host": result["host"], "score": result["score"], "category": category}}
    _safe(investigations.add_listing, result["id"], listing, result["server"])
    _safe(investigations.finish, result["id"], 1)


async def _web_rows(domain: str) -> list[dict] | None:
    """One web search for a store's domain, for web_footprint(). BG_FOOTPRINT=0 turns it off (tests do)."""
    if os.getenv("BG_FOOTPRINT", "1") == "0":
        return None
    return await web_search(domain, [], 20)


def _search_rows(query: str, errors: list[str]) -> list[dict]:
    return search._text(query, 10, errors)


async def web_search(query: str, errors: list[str], count: int = 10) -> list[dict]:
    """Brave when it has a key (paid, reliable), DuckDuckGo otherwise (free, often blocked)."""
    rows = await search.brave_web(query, errors, count)
    return rows if rows is not None else await asyncio.to_thread(_search_rows, query, errors)


def _query_name(result: dict) -> str:
    facts = result["facts"]
    name = facts.get("product") or facts.get("title") or ""
    name = re.sub(r"\s*[|\-]\s*[^|\-]{2,40}$", "", name).strip()
    return name[:140]


def rank_alternatives(items: list[dict]) -> list[dict]:
    """Trust tier always wins; price breaks ties only inside the same tier."""
    order = {"green": 0, "yellow": 1, "red": 2}
    return sorted(items, key=lambda x: (order.get(x.get("verdict"), 3), x.get("price") is None,
                                        x.get("price") if x.get("price") is not None else float("inf")))


async def deep_research(value: str, source: dict | None = None) -> dict:
    """Trusted places to buy the same item. source: a link check already done for value (saves checking it twice)."""
    source = source or await check_url(value, log=False)
    facts = source["facts"]
    product = _query_name(source)
    maker = facts.get("manufacturer") or facts.get("brand")
    maker_status = "Named in the listing. Confirm the domain before purchase."
    if not maker and not source.get("platform"):
        maker = facts.get("site_name")
        maker_status = "Site identity only. Confirm that this is the maker and not a retailer."
    if not product and not maker:
        raise ListingError("BrandGuard could not identify the item from this page. Open the evidence lab to add a source file.")
    errors: list[str] = []
    queries = [f'"{product}" buy']
    if maker:
        queries.append(f'"{maker}" "{product}" official')
    lens_job = search.reverse_candidates(source.get("image_url") or "", errors)
    *groups, lens = await asyncio.gather(*[web_search(q, errors) for q in queries], lens_job)
    rows = [row for group in groups for row in group]
    unique, seen = [], set()
    for row in rows:
        try:
            url = normalize_url(row.get("page_url") or "")
        except ListingError:
            continue
        host = risk.host_of(url)
        key = registrable_domain(host)
        if not key or key in seen or url == source["url"] or key == source.get("domain"):  # not the shop itself
            continue
        seen.add(key)
        unique.append({"url": url, "search_title": row.get("title") or ""})
        if len(unique) >= 8:
            break

    async with httpx.AsyncClient(timeout=10) as client:
        async def inspect(row: dict) -> dict | None:
            if not await net.safe_url_async(row["url"]):
                return None
            host = risk.host_of(row["url"])
            html, domain = await asyncio.gather(net.fetch_html(client, row["url"]),
                                                rdap.lookup(registrable_domain(host), client))
            item_facts = page_facts(html, row["url"])
            # judged against the brand being bought, so a look-alike shop (kiths.us.com for Kith) is caught
            snapshot = assess(row["url"], html, domain, [], [], claimed_brand=maker)
            title = item_facts.get("product") or row["search_title"] or host
            low_title = title.lower()
            article_words = ("countries", "guide", "review", "how to", "news", "wiki", "forum")
            commerce_words = ("buy", "shop", "sale", "price", "deal", "order", "store")
            # big retailers block page reads, so a known marketplace or a product-page address also counts as a listing
            product_page = _platform(host)[0] == "marketplace" or bool(PRODUCT_PATH.search(urlparse(row["url"]).path))
            if ARTICLE_PATH.search(urlparse(row["url"]).path) or _platform(host)[0] == "social":
                return None  # a blog post, guide or social thread about the item, not a place to buy it
            if not (item_facts.get("product") or item_facts.get("price") is not None or item_facts.get("seller") or
                    ((product_page or any(word in low_title for word in commerce_words))
                     and not any(word in low_title for word in article_words))):
                return None
            return {"url": row["url"], "host": host, "title": title,
                    "price": item_facts.get("price"), "currency": item_facts.get("currency"),
                    "seller": item_facts.get("seller"), "verdict": snapshot["verdict"],
                    "headline": snapshot["headline"], "signals": snapshot["signals"][:3]}
        alternatives = [x for x in await asyncio.gather(*[inspect(row) for row in unique]) if x]

    alternatives = rank_alternatives(alternatives)
    original_price, currency = facts.get("price"), facts.get("currency")
    for item in alternatives:
        item["cheaper"] = bool(original_price is not None and item["price"] is not None and
                               currency and item["currency"] == currency and item["price"] < original_price)

    manufacturer = None
    maker_url = facts.get("manufacturer_url")
    if maker:
        maker_token = re.sub(r"[^a-z0-9]", "", maker.lower())
        lead = next((x for x in alternatives if maker_token and maker_token in re.sub(r"[^a-z0-9]", "", x["host"].lower())), None)
        manufacturer = {"name": maker, "url": maker_url if isinstance(maker_url, str) and maker_url.startswith("http") else (lead or {}).get("url"),
                        "status": maker_status}
    same_photo = [{"url": c["page_url"], "host": risk.host_of(c["page_url"]), "title": c.get("title") or "",
                   "exact": c["source"] == "lens-exact"} for c in lens if c.get("page_url")
                  and registrable_domain(risk.host_of(c["page_url"])) != source["domain"]][:10]
    return {"source": source, "product": product or maker, "manufacturer": manufacturer,
            "alternatives": alternatives[:6], "errors": errors, "same_photo": same_photo,
            "lens_url": source.get("lens_url"), "lens_automatic": bool(lens) or bool(os.getenv("SERPAPI_KEY")),
            "ranking_note": "Trust tier is ranked before price. No result is guaranteed scam-free."}


def _supplier_site(host: str) -> str | None:
    return next((name for dom, name in SUPPLIER_SITES.items() if risk.on_domains(host, (dom,))), None)


def supplier_lead(url: str, title: str) -> dict:
    """What a supplier-site result is: one supplier's product or shop page (a lead to act on) or a search /
    category page listing many suppliers (a place to look)."""
    host, path = risk.host_of(url), urlparse(url).path.lower()
    specific = any(k in path for k in ("/product-detail/", "/prod_", "/offer/", "/product/", "/products/", "/proddetail"))
    store = host.split(".")[0] if host.endswith((".en.alibaba.com", ".made-in-china.com")) and host.split(".")[0] not in (
        "www", "m", "en") else None
    listing_page = any(k in path for k in ("/wholesale/", "/showroom/", "/countrysearch/", "/products-search/",
                                           "/manufacturers/", "/trade/search", "/hot-china-products/"))
    return {"url": url, "title": re.sub(r"\s+", " ", title or "").strip()[:160], "site": _supplier_site(host), "host": host,
            "kind": "supplier" if (specific or store) and not listing_page else "search page", "supplier": store}


GENERIC_WORDS = {"men", "mens", "women", "womens", "unisex", "kids", "new", "the", "and", "for", "with", "size", "sale"}


def relevant(product: str, text: str) -> bool:
    """A supplier result is about this product when it shares most of the product's distinctive words. Words are
    compared on their first four letters, so "runner" matches "running" but "tree" alone is not a tree runner."""
    words = {w[:4] for w in re.findall(r"[a-z0-9]+", product.lower().replace("'", "")) if len(w) >= 3 and w not in GENERIC_WORDS}
    if not words:
        return True
    hay = re.sub(r"[^a-z0-9]+", "", text.lower())
    return sum(w in hay for w in words) >= min(2, len(words))


def _strip_brand(text: str, brand: str | None) -> str:
    out = text or ""
    for word in re.findall(r"[A-Za-z0-9&'-]+", brand or ""):
        if len(word) >= 3:
            out = re.sub(rf"\b{re.escape(word)}\b", " ", out, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"[|\-–:,()\[\]]+", " ", out)).strip()


async def suppliers(value: str, mode: str = "source") -> dict:
    """Layer 3, two ways, chosen by the user:
    source: wholesale suppliers selling the same product (where copies are bought), searched without the brand name,
            since copies are usually listed unbranded.
    maker:  who makes the product: the maker the page names, plus factories listing it under its own name, which
            shows a white-label product when the 'brand' is just a reseller label."""
    if mode not in ("source", "maker"):
        raise ListingError("Choose source or maker.")
    source = await check_url(value, log=False)
    facts = source["facts"]
    product = _query_name(source)
    brand = facts.get("brand") or facts.get("manufacturer")
    generic = " ".join(_strip_brand(product, brand).split()[:8])
    if not generic:
        raise ListingError("BrandGuard could not read a product name from this page, so there is nothing to search for.")
    sites = " OR ".join(f"site:{d}" for d in SUPPLIER_SITES)
    queries = ([f"{generic} wholesale {sites}", f"{generic} factory {sites}"] if mode == "source" else
               [f"{generic} manufacturer {sites}", f'"{product}" OEM {sites}'])
    errors: list[str] = []
    rows = [r for group in await asyncio.gather(*[web_search(q, errors, 15) for q in queries]) for r in group]
    leads, seen = [], set()
    for r in rows:
        url = r.get("page_url") or ""
        if url in seen or not _supplier_site(risk.host_of(url)) or not relevant(generic, f"{r.get('title') or ''} {url}"):
            continue
        seen.add(url)
        leads.append(supplier_lead(url, r.get("title") or ""))
    leads.sort(key=lambda x: x["kind"] != "supplier")
    maker = None
    if mode == "maker":
        named = facts.get("manufacturer") or facts.get("brand")
        site = facts.get("site_name") if not source.get("platform") else None
        url = facts.get("manufacturer_url")
        maker = {"name": named or site, "url": url if isinstance(url, str) and url.startswith("http") else None,
                 "status": "Named on the listing itself." if named else
                           "The site's own name: confirm it is the maker and not a retailer." if site else "The listing names no maker."}
    note = ("Wholesale listings for the same kind of product. Copies are usually sold without the brand name, so these "
            "are leads to check against your photos, not proof." if mode == "source" else
            "Factories that list this product. If they show the same item with no brand, the 'brand' may be a "
            "white-label product bought wholesale.")
    return {"mode": mode, "product": product, "query": generic, "maker": maker, "leads": leads[:15],
            "note": note, "errors": errors, "source": {"url": source["url"], "host": source["host"]}}
