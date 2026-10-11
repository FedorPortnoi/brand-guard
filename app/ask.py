"""A shopper's question, "is this legit?", from whatever they have: a link, a screenshot of what they are looking at,
or a store's name. Finds the address to check and runs the link check on it (listing.check_url).

Screenshots are read with RapidOCR (free, runs locally, no key)."""
from __future__ import annotations

import asyncio
import io
import re

import numpy as np
from PIL import Image

from . import forensics  # noqa: F401  registers the HEIC opener: iPhone screenshots sent by iMessage
from . import listing, risk, video
from .listing import ListingError
from .sellers import registrable_domain

_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
# a bare address such as adidas-outlet.shop/samba; not part of an email (x@y.com) or a longer word
_DOMAIN = re.compile(r"(?<![@\w.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,18}))(/[^\s<>\"']*)?(?![\w-])", re.I)
_HANDLE = re.compile(r"(?<![\w.@])@([a-z0-9_](?:[a-z0-9_.]{0,28}[a-z0-9_])?)", re.I)
# words after a dot that are file names or abbreviations, not web addresses
_NOT_TLD = {"jpg", "jpeg", "png", "gif", "webp", "heic", "pdf", "mp4", "mov", "html", "htm", "php", "js", "css", "txt",
            "doc", "docx", "zip", "eg", "ie", "etc", "am", "pm", "vs", "mr", "ms", "dr", "jr", "st", "no"}
# where a name search lands that is not the store itself
_NOT_THE_STORE = ("trustpilot", "sitejabber", "scamadviser", "scam-detector", "bbb.org", "wikipedia", "quora",
                  "glassdoor", "indeed", "linkedin", "yelp", "medium.com", "bloomberg", "crunchbase")
# words around a name in "is Lucky Fours legit?"
_FILLER = re.compile(r"\b(is|are|this|that|the|site|store|shop|website|legit|legitimate|real|fake|scam|safe|"
                     r"trustworthy|a|an|or|not|check|please|can you|tell me|if|and|also|what about|how about|"
                     r"for|me|my|it|its|one|these|those|them|of|on|to|from|with|about|should i|buy|buying|here|too)\b"
                     r"|[?!.,:;\"']", re.I)
LOCAL_PATH = re.compile(r"""^["']?(?:[a-zA-Z]:[\\/]|\\\\|~/|/(?:Users|home|tmp|var|private)/)""")  # C:\..., \\server, ~/, /Users/...
MAX_ITEMS = 3  # things checked from one message ("is Lucky Fours legit? and kith.com")
PDF_PAGES = 5  # pages read from a PDF (a receipt, an invoice, an order confirmation)
# services a receipt links to that are not the seller: payment, shop platforms, the PDF's own tooling
NOT_THE_SELLER = ("paypal.com", "stripe.com", "squareup.com", "square.site", "shopify.com", "myshopify.com", "klarna.com",
                  "afterpay.com", "affirm.com", "zelle.com", "venmo.com", "cash.app", "google.com", "apple.com",
                  "microsoft.com", "adobe.com", "w3.org", "schema.org", "bit.ly")
VIDEO_FRAMES = 8  # distinct frames read from a video (a screen recording, a saved reel)
VIDEO_SIZE = 1600  # long side of a frame read for text: phone screen text is small

_ocr = None


def _reader():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def read_text(image: bytes) -> list[str]:
    """The lines of text in a screenshot, top to bottom."""
    try:
        img = Image.open(io.BytesIO(image)).convert("RGB")
    except Exception as exc:
        raise ListingError("Could not open that image.") from exc
    return _read(img)


def video_frames(data: bytes, limit: int = VIDEO_FRAMES) -> list[Image.Image]:
    """The distinct, non-blank frames of a video, about one a second, evenly spread when there are more than limit:
    a screen recording scrolls through a shop, a reel shows its caption and handle."""
    try:
        frames, _, _ = video._sample(data, 1.0, VIDEO_SIZE)
    except video.VideoError as exc:
        raise ListingError(f"{exc}. Send a screenshot or the link instead.") from exc
    picked, seen = [], []
    for _, img in frames:
        if not video._informative(img):
            continue
        small = np.asarray(img.convert("L").resize((240, round(240 * img.height / img.width))), dtype=np.int16)
        if any(x.shape == small.shape and _same_screen(x, small) for x in seen):  # the same screen held still
            continue
        seen.append(small)
        picked.append(img)
    if len(picked) > limit:
        picked = [picked[round(i * (len(picked) - 1) / (limit - 1))] for i in range(limit)]
    return picked


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def pdf_text(data: bytes, pages: int = PDF_PAGES) -> list[str]:
    """The lines of a PDF's first pages and the addresses its links point to (a receipt's "Track your order" is a
    link, not text). A page with no text layer (a scan, a photo saved as PDF) is read like a screenshot."""
    try:
        import pymupdf
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ListingError("Could not read that PDF. Send a screenshot or the link instead.") from exc
    lines: list[str] = []
    with doc:
        if doc.needs_pass:
            raise ListingError("That PDF is password protected. Send a screenshot of it instead.")
        for page in list(doc)[:pages]:
            text = [ln.strip() for ln in page.get_text().splitlines() if ln.strip()]
            if sum(len(t) for t in text) < 20:  # no text layer: render it and read it
                pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csRGB, alpha=False)
                text = _read(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
            lines += text
            lines += [link["uri"] for link in page.get_links() if (link.get("uri") or "").startswith(("http://", "https://"))]
    return list(dict.fromkeys(lines))


def seller_address(lines: list[str]) -> str | None:
    """The seller's address in a document: the site it names most often (text and links), first-named on a tie,
    leaving out platforms and the payment and shop-platform services every receipt links to."""
    counts: dict[str, list] = {}
    for i, value in enumerate(addresses(" ".join(lines))):
        if _platform_site(value):
            continue
        site = registrable_domain(risk.host_of(listing.normalize_url(value)))
        if not site or site in NOT_THE_SELLER:
            continue
        entry = counts.setdefault(site, [0, i, value])
        entry[0] += 1
    if not counts:
        return None
    return min(counts.values(), key=lambda e: (-e[0], e[1]))[2]


def _same_screen(a: np.ndarray, b: np.ndarray) -> bool:
    """Two frames show the same screen when almost no pixel clearly changed. Compression noise stays under the
    threshold; one new line of text (an address, a handle) does not, unlike a perceptual hash."""
    return float((np.abs(a - b) > 48).mean()) < 0.0005


def video_text(data: bytes) -> list[str]:
    """The lines of text across a video's frames, in order, each once."""
    return list(dict.fromkeys(line for img in video_frames(data) for line in _read(img)))


def _read(img: Image.Image) -> list[str]:
    if max(img.size) > 2600:
        img.thumbnail((2600, 2600))
    result, _ = _reader()(np.asarray(img)[:, :, ::-1])  # RapidOCR expects BGR, as OpenCV loads it
    return [line[1].strip() for line in result or [] if line[1].strip()]


def addresses(text: str) -> list[str]:
    """Web addresses in free text, as written: full links first, then bare domains (adidas-outlet.shop/samba)."""
    found = [u.rstrip(").,;]") for u in _URL.findall(text)]
    rest = _URL.sub(" ", text)
    for m in _DOMAIN.finditer(rest):
        if m.group(2).lower() in _NOT_TLD or m.group(1).split(".")[0].isdigit():
            continue
        found.append((m.group(1) + (m.group(3) or "")).rstrip(").,;]"))
    return list(dict.fromkeys(found))


def _platform_site(value: str) -> bool:
    host = risk.host_of(listing.normalize_url(value))
    return any(risk.host_matches(host, p) for p in risk.SOCIAL + risk.MARKETPLACES)


def _social_site(value: str) -> bool:
    host = risk.host_of(listing.normalize_url(value))
    return any(risk.host_matches(host, p) for p in risk.SOCIAL)


def pick_address(found: list[str]) -> str | None:
    """A shop's own address beats a bare platform name ("instagram.com" in a footer); a platform link with a path
    (a listing or a post) beats nothing."""
    for value in found:
        if not _platform_site(value):
            return value
    for value in found:
        if "/" in value.split("://", 1)[-1].strip("/"):
            return value
    return None


def account_url(lines: list[str]) -> tuple[str, str] | None:
    """An @account in a screenshot, on the platform the screenshot names (Instagram when it names none)."""
    text = " ".join(lines)
    handle = next((h for h in _HANDLE.findall(text) if not h.isdigit()), None)
    if not handle:
        return None
    if re.search(r"tiktok", text, re.I):
        return f"https://www.tiktok.com/@{handle}", f"@{handle} on TikTok"
    if re.search(r"\bfacebook\b", text, re.I):
        return f"https://www.facebook.com/{handle}", f"@{handle} on Facebook"
    return f"https://www.instagram.com/{handle}/", f"@{handle} on Instagram"


def store_name(text: str) -> str:
    return re.sub(r"\s+", " ", _FILLER.sub(" ", text)).strip()


async def find_store(name: str, errors: list[str], query: str = "", skip_domain: str = "") -> str | None:
    """The store's own site for a name, from a web search: the first result that is not a social network,
    a marketplace, a review site or skip_domain (the site being checked)."""
    for row in await listing.web_search(query or name, errors, 10):
        url = row.get("page_url") or ""
        host = risk.host_of(url)
        if not host or any(risk.host_matches(host, p) for p in risk.SOCIAL + risk.MARKETPLACES + _NOT_THE_STORE):
            continue
        if skip_domain and registrable_domain(host) == skip_domain:
            continue
        return url
    return None


def _token(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


async def real_brand(result: dict, errors: list[str]) -> dict | None:
    """The brand the page claims to sell and its own site. this_site: the checked site is the brand's own."""
    brand = (result.get("facts") or {}).get("brand") or ""
    domain = result.get("domain") or ""
    if len(_token(brand)) < 3 or not domain:
        return None
    if domain.split(".")[0].replace("-", "") == _token(brand):
        return {"name": brand, "url": f"https://{domain}", "this_site": True}
    url = await find_store(brand, errors, query=f"{brand} official site", skip_domain=domain)
    return {"name": brand, "url": url, "this_site": False} if url else None


NO_PRODUCT = "I couldn't tell which product this is, so I can't suggest other places to buy it. Send me the product page."
NOT_A_STORE = "That page doesn't sell anything, so there's nothing to compare it with."
AN_ACCOUNT = "That's an account, not a product. Send me the post or product link and I'll find other places to buy it."


_SLUG_JUNK = re.compile(r"^(?:[a-z]{0,3}\d[\w-]*|p|dp|products?|items?|itm|shop|us|en|en-us|collections?|[a-z]{2}(?:-[a-z]{2})?)$", re.I)


def product_from_url(url: str) -> str:
    """A product name from the address, for pages that block reading (big brands often do):
    adidas.com/us/samba-og-shoes/B75806 -> "samba og shoes". Empty when the address has no words."""
    path = re.sub(r"[?#].*$", "", url.split("://", 1)[-1]).split("/")[1:]
    words = [re.sub(r"\.(html?|php|aspx?)$", "", seg, flags=re.I) for seg in path]
    words = [w for w in words if w and not _SLUG_JUNK.match(w) and re.search(r"[a-z]{3}", w, re.I)]
    best = max(words, key=lambda w: len(re.findall(r"[-_+]", w)), default="")
    return re.sub(r"[-_+]+", " ", best).strip() if re.search(r"[-_+]", best) else ""


_TITLE_PARTS = re.compile(r"\s+[|–—:]\s+|\s+-\s+")
_TITLE_FLUFF = re.compile(r"#\s*1\b|\b(?:no\.?\s*1|top[- ]rated|best[- ]selling|rated|official(?: site| store)?|online|"
                          r"shop|store|home(?:page)?|welcome(?: to)?)\b|[®™©]", re.I)


def store_category(title: str, store: str) -> str:
    """What a store sells, from its search title: "Lucky Fours® | #1 Rated Affordable Luxury Watches" ->
    "Affordable Luxury Watches". Empty when the title only names the store."""
    token = _token(store)
    parts = [p for p in _TITLE_PARTS.split(title or "") if p.strip() and not (token and token in _token(p))]
    best = max((re.sub(r"\s+", " ", _TITLE_FLUFF.sub(" ", p)).strip(" -,.") for p in parts), key=len, default="")
    return best if len(re.findall(r"[a-z]{3,}", best, re.I)) >= 1 else ""


async def describe_store(result: dict, errors: list[str]) -> str:
    """What the checked store sells, from how a web search describes its own site (for sites that block reading)."""
    domain = result.get("domain") or ""
    if not domain:
        return ""
    rows = [r for r in await listing.web_search(domain, errors, 10)
            if registrable_domain(risk.host_of(r.get("page_url") or "")) == domain]
    rows.sort(key=lambda r: len(listing.normalize_url(r["page_url"]).split("://", 1)[-1].rstrip("/")))  # home page first
    return next((c for r in rows if (c := store_category(r.get("title") or "", domain.split(".")[0]))), "")


async def alternatives(result: dict) -> tuple[dict, list[dict], dict]:
    """Trusted places to buy the same item (or, when only the store is known, trusted stores selling what it sells),
    whatever the verdict (only ones rated Legit); the maker deep research named; and what the alternatives are for."""
    if result.get("sells") is False:  # a page with nothing for sale (a phishing page, an article): nothing to compare
        return {}, [], {}
    facts = result.get("facts") or {}
    if result.get("url") and _social_site(result["url"]) and not facts.get("product"):
        return {}, [], {}  # a post's title ("Video by ...") is not a product to compare
    known = facts.get("product") or facts.get("title") or facts.get("brand")
    wanted = {"what": known or "", "kind": "product"}
    if known and not facts.get("product") and _is_home_page(result.get("url") or ""):
        # a store's home page names the store, not an item: look for stores selling what it sells
        category = store_category(facts.get("title") or "", (result.get("domain") or "").split(".")[0])
        known = ""
        facts = {k: v for k, v in facts.items() if k not in ("title", "brand")}
        result = {**result, "facts": facts}
        if category:
            return await _search_alternatives(result, {**facts, "product": category}, {"what": category, "kind": "category"})
    if not known:
        slug = product_from_url(result.get("url") or "")
        if slug:  # the page could not be read: search by the address's product words and the site's name
            wanted = {"what": slug, "kind": "product"}
            result = {**result, "facts": {**facts, "product": slug, "site_name": (result.get("domain") or "").split(".")[0]}}
        elif result.get("url") and _platform_site(result["url"]):
            return {}, [], {}  # a marketplace's or social site's own description is not what this seller sells
        else:  # only the store is known: stores selling the same kind of thing
            category = await describe_store(result, [])
            if not category:
                return {}, [], {}
            wanted = {"what": category, "kind": "category"}
            result = {**result, "facts": {**facts, "product": category}}
    return await _search_alternatives(result, result.get("facts") or {}, wanted)


def _is_home_page(url: str) -> bool:
    return listing.normalize_url(url).split("://", 1)[-1].split("?")[0].rstrip("/").count("/") == 0


async def _search_alternatives(result: dict, facts: dict, wanted: dict) -> tuple[dict, list[dict], dict]:
    try:
        deep = await listing.deep_research(result["url"], source={**result, "facts": facts})
    except (ListingError, KeyError):
        return {}, [], {}
    # only shops rated Legit, and none with a serious warning (e.g. the brand's name padded in its address)
    safe = [a for a in deep.get("alternatives", [])
            if a.get("verdict") == "green" and not any(s.get("tone") == "bad" for s in a.get("signals") or [])]
    return deep, safe, wanted


async def extras(result: dict) -> dict:
    """What every answer adds to the verdict: the real brand and other places to buy (run side by side)."""
    errors: list[str] = []
    brand, (deep, alts, wanted) = await asyncio.gather(real_brand(result, errors), alternatives(result))
    maker = deep.get("manufacturer") or {}
    if not brand and maker.get("url") and registrable_domain(risk.host_of(maker["url"])) != result.get("domain"):
        brand = {"name": maker["name"], "url": maker["url"], "this_site": False}
    return {"real_brand": brand, "alternatives": alts[:3], "alternatives_for": wanted if alts else {},
            "alternatives_note": "" if alts else NOT_A_STORE if result.get("sells") is False
            else AN_ACCOUNT if listing.profile_handle(result.get("url") or "") and _social_site(result.get("url") or "")
            else NO_PRODUCT if not deep
            else "I didn't find other trusted places selling it.",
            "same_photo": deep.get("same_photo", [])[:3]}


async def _check(target: str, how: str, what: str, seen: str, lines: list[str], name: str = "") -> dict:
    result = await listing.check_url(target, brand=name, seen_text=seen)
    facts = result.get("facts") or {}
    if how == "link" and facts.get("account") and _social_site(result.get("url") or ""):  # "@shoptemu on Facebook", not "facebook.com"
        what = f"{facts['account']} on {result.get('platform')}"
    result["found"] = {"how": how, "what": what if how != "name" else f"{what}: {result['domain']}", "read": lines[:40]}
    result.update(await extras(result))
    return result


async def _check_name(name: str, seen: str, lines: list[str]) -> dict:
    target = await find_store(name, [])
    if not target:
        raise ListingError(f"I couldn't find a website for \"{name}\". Send me the link instead.")
    return await _check(target, "name", name, seen, lines, name=name)


async def _answer(job, how: str, what: str) -> dict:
    """One item's answer; a failure becomes that item's message, so the others in the message still get checked."""
    try:
        return await job
    except ListingError as exc:
        return {"error": str(exc), "found": {"how": how, "what": what}}


def _position(text: str, words: str) -> int:
    i = text.lower().find(words.split()[0].lower())
    return i if i >= 0 else len(text)


async def ask(text: str = "", image: bytes | None = None) -> dict:
    """Check whatever the shopper sent. The result is listing.check_url's, plus `found`: how the address was
    found ("link", "screenshot", "account" or "name") and what was checked, in words.

    A message can name several things ("is Lucky Fours legit? and kith.com"): every link, and the store name left
    once the links are taken out, are checked side by side, in the order written, up to MAX_ITEMS. The first that
    could be checked is the result; the rest are in `others` (each a result, or {"error", "found"})."""
    text = (text or "").strip()
    if not image and LOCAL_PATH.match(text):
        raise ListingError("That's the path of a file on your computer, not a link. Send me the file itself.")
    kind = ("pdf" if is_pdf(image) else "video" if video.is_video(image) else "screenshot") if image else ""
    reader = {"pdf": pdf_text, "video": video_text, "screenshot": read_text}.get(kind)
    lines = await asyncio.to_thread(reader, image) if image else []
    seen = " ".join(lines)
    shot = kind or "screenshot"
    shot_name = "PDF" if shot == "pdf" else shot

    links = [v for v in addresses(text) if pick_address([v])]
    rest = text
    for v in links:
        rest = rest.replace(v, " ")
    name = store_name(rest)
    if links or name:
        items = [(_position(text, v), lambda v=v: _answer(
            _check(v, "link", risk.host_of(listing.normalize_url(v)), seen, lines), "link", v)) for v in links]
        if name:
            items.append((_position(text, name), lambda: _answer(_check_name(name, seen, lines), "name", name)))
        jobs = [start() for _, start in sorted(items, key=lambda x: x[0])[:MAX_ITEMS]]
    elif lines and (value := seller_address(lines) if shot == "pdf" else pick_address(addresses(seen))):
        jobs = [_check(value, shot, risk.host_of(listing.normalize_url(value)), seen, lines)]
    elif lines and (acct := account_url(lines)):
        jobs = [_check(acct[0], "account", acct[1], seen, lines)]
    else:
        raise ListingError(f"I couldn't find a web address or an @account in that {shot_name}. "
                           "Send me the link, or the store's name.")

    results = await asyncio.gather(*jobs)
    answered = [r for r in results if "error" not in r]
    if not answered:  # nothing could be checked: say why, as a single answer always did
        raise ListingError(results[0]["error"])
    result = answered[0]
    result["others"] = [r for r in results if r is not result]
    return result
