"""Marketplace-native search: eBay image search (official Browse API) and AliExpress (via OneFindMe).

Both return candidates in the discovery format plus seller and price, so the Sellers tab can group
listings by store without scraping. Errors are appended as short tags and never contain keys.
"""
import base64
import os
import re
import time
from urllib.parse import urlparse

import httpx

EBAY_API = "https://api.ebay.com"
EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope"
_ebay_token = {"value": None, "expires": 0.0}


def ebay_enabled() -> bool:
    return bool(os.getenv("EBAY_CLIENT_ID") and os.getenv("EBAY_CLIENT_SECRET"))


async def _ebay_access_token(c: httpx.AsyncClient) -> str:
    """Application token (client-credentials grant), cached until shortly before it expires."""
    if _ebay_token["value"] and time.time() < _ebay_token["expires"] - 60:
        return _ebay_token["value"]
    r = await c.post(f"{EBAY_API}/identity/v1/oauth2/token",
                     auth=(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"]),
                     data={"grant_type": "client_credentials", "scope": EBAY_SCOPE})
    r.raise_for_status()
    j = r.json()
    _ebay_token.update(value=j["access_token"], expires=time.time() + int(j.get("expires_in", 7200)))
    return _ebay_token["value"]


def _price(value, currency) -> str | None:
    return f"{value} {currency}".strip() if value not in (None, "") else None


async def ebay_image_candidates(image: bytes, errors: list, client: httpx.AsyncClient | None = None,
                                limit: int = 50) -> list[dict]:
    """eBay listings that look like `image` (Browse API search_by_image). Needs EBAY_CLIENT_ID and EBAY_CLIENT_SECRET."""
    if not ebay_enabled() or not image:
        return []
    own = client is None
    c = client or httpx.AsyncClient(timeout=30)
    try:
        token = await _ebay_access_token(c)
        r = await c.post(f"{EBAY_API}/buy/browse/v1/item_summary/search_by_image", params={"limit": limit},
                         json={"image": base64.b64encode(image).decode()},
                         headers={"Authorization": f"Bearer {token}",
                                  "X-EBAY-C-MARKETPLACE-ID": os.getenv("EBAY_MARKETPLACE", "EBAY_US")})
        if r.status_code == 401:
            _ebay_token["value"] = None
        r.raise_for_status()
        out = []
        for x in r.json().get("itemSummaries") or []:
            img, page = (x.get("image") or {}).get("imageUrl"), x.get("itemWebUrl")
            if not img or not page:
                continue
            thumbs = x.get("thumbnailImages") or []
            price = x.get("price") or {}
            out.append({"image_url": img, "thumbnail": thumbs[0].get("imageUrl") if thumbs else None,
                        "page_url": page.split("?")[0], "title": x.get("title", ""), "source": "ebay-image",
                        "reverse": True, "seller": (x.get("seller") or {}).get("username"),
                        "price": _price(price.get("value"), price.get("currency"))})
        return out
    except httpx.HTTPStatusError as e:
        errors.append(f"ebay: HTTP {e.response.status_code}")
    except Exception as e:
        errors.append(f"ebay: {type(e).__name__}")
    finally:
        if own:
            await c.aclose()
    return []


ONEFINDME = "https://onefindme.com/api/search"
AFFILIATE_HOST = "s.click.aliexpress.com"
_ITEM_ID = re.compile(r"/item/(\d+)\.html")


def aliexpress_enabled() -> bool:
    return os.getenv("BG_ALIEXPRESS", "1") != "0"


async def aliexpress_candidates(brand: str, keywords: list[str], errors: list,
                                client: httpx.AsyncClient | None = None) -> list[dict]:
    """AliExpress listings for the brand via OneFindMe (free, no key; set BG_ALIEXPRESS=0 to turn off).

    Listing links are affiliate short links; pipeline.finish swaps them for the plain item URL with
    canonical_page_url, only for listings that turn out to match.
    """
    if not aliexpress_enabled():
        return []
    queries = [brand] + [f"{brand} {k}" for k in keywords[:2]]
    country = os.getenv("BG_ALIEXPRESS_COUNTRY", "US")
    own = client is None
    c = client or httpx.AsyncClient(timeout=30)
    out, seen = [], set()
    try:
        for q in queries:
            try:
                r = await c.get(ONEFINDME, params={"query": q, "country": country, "max_results": 20})
                r.raise_for_status()
                for x in r.json().get("results") or []:
                    if not x.get("image") or not x.get("url") or x["image"] in seen:
                        continue
                    seen.add(x["image"])
                    out.append({"image_url": x["image"], "thumbnail": None, "page_url": x["url"],
                                "title": x.get("title", ""), "source": f"aliexpress:{q}", "seller": x.get("store"),
                                "price": _price(x.get("price"), x.get("currency")), "orders": x.get("orders")})
            except Exception as e:
                errors.append(f"aliexpress: HTTP {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError)
                              else f"aliexpress: {type(e).__name__}")
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                    break
    finally:
        if own:
            await c.aclose()
    return out


async def canonical_page_url(client: httpx.AsyncClient, url: str | None) -> str | None:
    """Plain https://www.aliexpress.com/item/<id>.html for an AliExpress affiliate short link; anything else unchanged.

    Reads one redirect without following it. Falls back to the original link if that fails.
    """
    if not url or (urlparse(url).hostname or "").lower() != AFFILIATE_HOST:
        return url
    try:
        r = await client.get(url, follow_redirects=False, timeout=10)
        m = _ITEM_ID.search(r.headers.get("location", ""))
        if m:
            return f"https://www.aliexpress.com/item/{m.group(1)}.html"
    except Exception:
        pass
    return url
