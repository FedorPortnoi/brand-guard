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


async def find_store(name: str, errors: list[str]) -> str | None:
    """The store's own site for a name, from a web search: the first result that is not a social network,
    a marketplace or a review site."""
    for row in await listing.web_search(name, errors, 10):
        url = row.get("page_url") or ""
        host = risk.host_of(url)
        if not host or any(risk.host_matches(host, p) for p in risk.SOCIAL + risk.MARKETPLACES + _NOT_THE_STORE):
            continue
        return url
    return None


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
    return result
