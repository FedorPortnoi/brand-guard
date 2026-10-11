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
from . import listing, risk
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
                     r"trustworthy|a|an|or|not|check|please|can you|tell me|if)\b|[?!.,:;\"']", re.I)

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


_SLUG_JUNK = re.compile(r"^(?:[a-z]{0,3}\d[\w-]*|p|dp|products?|items?|itm|shop|us|en|en-us|collections?|[a-z]{2}(?:-[a-z]{2})?)$", re.I)


def product_from_url(url: str) -> str:
    """A product name from the address, for pages that block reading (big brands often do):
    adidas.com/us/samba-og-shoes/B75806 -> "samba og shoes". Empty when the address has no words."""
    path = re.sub(r"[?#].*$", "", url.split("://", 1)[-1]).split("/")[1:]
    words = [re.sub(r"\.(html?|php|aspx?)$", "", seg, flags=re.I) for seg in path]
    words = [w for w in words if w and not _SLUG_JUNK.match(w) and re.search(r"[a-z]{3}", w, re.I)]
    best = max(words, key=lambda w: len(re.findall(r"[-_+]", w)), default="")
    return re.sub(r"[-_+]+", " ", best).strip() if re.search(r"[-_+]", best) else ""


async def alternatives(result: dict) -> tuple[dict, list[dict]]:
    """Trusted places to buy the same item, whatever the verdict (none rated red), and the maker deep research named."""
    facts = result.get("facts") or {}
    if not (facts.get("product") or facts.get("title") or facts.get("brand")):
        slug = product_from_url(result.get("url") or "")
        if not slug:
            return {}, []
        # the page could not be read: search by the address's product words and the site's name
        result = {**result, "facts": {**facts, "product": slug, "site_name": (result.get("domain") or "").split(".")[0]}}
    try:
        deep = await listing.deep_research(result["url"], source=result)
    except (ListingError, KeyError):
        return {}, []
    return deep, [a for a in deep.get("alternatives", []) if a.get("verdict") != "red"]


async def extras(result: dict) -> dict:
    """What every answer adds to the verdict: the real brand and other places to buy (run side by side)."""
    errors: list[str] = []
    brand, (deep, alts) = await asyncio.gather(real_brand(result, errors), alternatives(result))
    maker = deep.get("manufacturer") or {}
    if not brand and maker.get("url") and registrable_domain(risk.host_of(maker["url"])) != result.get("domain"):
        brand = {"name": maker["name"], "url": maker["url"], "this_site": False}
    return {"real_brand": brand, "alternatives": alts[:3],
            "alternatives_note": "" if alts else NO_PRODUCT if not deep else "I didn't find other trusted places selling it.",
            "same_photo": deep.get("same_photo", [])[:3]}


async def ask(text: str = "", image: bytes | None = None) -> dict:
    """Check whatever the shopper sent. The result is listing.check_url's, plus `found`: how the address was
    found ("link", "screenshot", "account" or "name") and what was checked, in words."""
    text = (text or "").strip()
    lines = await asyncio.to_thread(read_text, image) if image else []
    seen = " ".join(lines)
    target, how, what, name = None, "", "", ""

    if value := pick_address(addresses(text)):
        target, how, what = value, "link", risk.host_of(listing.normalize_url(value))
    elif lines and (value := pick_address(addresses(seen))):
        target, how, what = value, "screenshot", risk.host_of(listing.normalize_url(value))
    elif lines and (acct := account_url(lines)):
        (target, what), how = acct, "account"
    else:
        name = store_name(text)
        if name:
            errors: list[str] = []
            target = await find_store(name, errors)
            how, what = "name", name
            if not target:
                raise ListingError(f"I couldn't find a website for \"{name}\". Send me the link instead.")
    if not target:
        raise ListingError("I couldn't find a web address or an @account in that screenshot. "
                           "Send me the link, or the store's name.")

    result = await listing.check_url(target, brand=name, seen_text=seen)
    result["found"] = {"how": how, "what": what if how != "name" else f"{what}: {result['domain']}", "read": lines[:40]}
    result.update(await extras(result))
    return result
