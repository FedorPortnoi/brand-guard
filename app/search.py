"""Candidate discovery (all free, no keys).

1. Image search through several ddgs backends x an expanded query set.
2. Page crawl: ordinary web results are fetched and their og:image / twitter:image / <img> tags become candidates
   (this is how product pages on shops and marketplaces surface).
3. Optional APIs: Brave (BRAVE_API_KEY), You.com web search (YDC_API_KEY), Google Lens via SERPAPI_KEY.
Every candidate is later VERIFIED visually; discovery only has to be generous, not precise.
"""
import asyncio
import os
import re
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS

from . import net

IMAGE_BACKENDS = ("bing", "brave")  # duckduckgo dropped: it raised DDGSException on every live scan


def expand_queries(brand: str, keywords: list[str], official: list[str]) -> list[str]:
    base = [f'"{brand}"', f'"{brand}" logo', f'"{brand}" product', f'"{brand}" replica', f'"{brand}" counterfeit',
            f'"{brand}" cheap buy', f'"{brand}" wholesale', f'"{brand}" fake vs real'] + [f'"{brand}" {k}' for k in keywords]
    out = list(dict.fromkeys(base))[:12]
    if official:  # push results away from the brand's own site
        out += [f"{q} -site:{official[0]}" for q in out[:4]]
    return out


def _images(query: str, backend: str, n: int, errors: list) -> list[dict]:
    try:
        rows = DDGS(timeout=12).images(query, backend=backend, max_results=n)
        return [{"image_url": r.get("image"), "thumbnail": r.get("thumbnail"), "page_url": r.get("url"),
                 "title": r.get("title", ""), "source": f"{backend}:{query}"} for r in rows if r.get("image")]
    except Exception as e:
        errors.append(f"{backend}: {type(e).__name__}")
        return []


def _text(query: str, n: int, errors: list) -> list[dict]:
    for backend in ("brave", "auto"):  # Brave's index differs from Bing's; fall back if it returns nothing
        try:
            rows = DDGS(timeout=12).text(query, backend=backend, max_results=n)
            out = [{"page_url": r.get("href"), "title": r.get("title", ""), "description": r.get("body", "")}
                   for r in rows if r.get("href")]
            if out:
                return out
        except Exception as e:
            if backend == "auto":
                errors.append(f"text: {type(e).__name__}")
    return []


BRAVE_API = "https://api.search.brave.com/res/v1"


async def brave_api(queries: list[str], errors: list) -> tuple[list[dict], list[dict]]:
    """Official Brave Search API (optional: needs BRAVE_API_KEY). Returns (image candidates, page results)."""
    key = os.getenv("BRAVE_API_KEY")
    if not key:
        return [], []
    headers = {"X-Subscription-Token": key, "Accept": "application/json"}
    imgs, pages = [], []
    async with httpx.AsyncClient(timeout=15, headers=headers) as c:
        for q in queries[:6]:  # stay well inside free-tier quotas
            try:
                r = await c.get(f"{BRAVE_API}/images/search", params={"q": q, "count": 30})
                r.raise_for_status()
                for x in r.json().get("results", []):
                    src = (x.get("properties") or {}).get("url") or (x.get("thumbnail") or {}).get("src")
                    if src:
                        imgs.append({"image_url": src, "thumbnail": (x.get("thumbnail") or {}).get("src"), "page_url": x.get("url"),
                                     "title": x.get("title", ""), "source": f"brave-api:{q}"})
                r = await c.get(f"{BRAVE_API}/web/search", params={"q": q, "count": 10})
                r.raise_for_status()
                pages += [{"page_url": x["url"], "title": x.get("title", "")} for x in r.json().get("web", {}).get("results", []) if x.get("url")]
            except Exception as e:
                code = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
                errors.append("brave-api: usage limit reached (raise it in the Brave API dashboard)" if code == 402
                              else f"brave-api: {type(e).__name__}{f' {code}' if code else ''}")
                if code in (401, 402, 403, 429):  # the rest of the queries would fail the same way
                    break
    return imgs, pages


async def brave_web(query: str, errors: list, count: int = 10, client: httpx.AsyncClient | None = None) -> list[dict] | None:
    """One Brave web search: [{page_url, title, description}], or None when there is no key (the caller falls back)."""
    key = os.getenv("BRAVE_API_KEY")
    if not key:
        return None
    own = client is None
    c = client or httpx.AsyncClient(timeout=15)
    try:
        r = await c.get(f"{BRAVE_API}/web/search", params={"q": query, "count": count},
                        headers={"X-Subscription-Token": key, "Accept": "application/json"})
        r.raise_for_status()
        clean = lambda t: unescape(re.sub(r"<[^>]+>", "", t or ""))  # noqa: E731  (Brave sends HTML: &#039;, <strong>)
        return [{"page_url": x["url"], "title": clean(x.get("title")), "description": clean(x.get("description"))}
                for x in r.json().get("web", {}).get("results", []) if x.get("url")]
    except Exception as e:
        code = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
        errors.append("brave-api: usage limit reached (raise it in the Brave API dashboard)" if code == 402
                      else f"brave-api: {type(e).__name__}{f' {code}' if code else ''}")
        return []
    finally:
        if own:
            await c.aclose()


YOU_API = "https://ydc-index.io/v1/search"


async def you_api(queries: list[str], errors: list, client: httpx.AsyncClient | None = None) -> tuple[list[dict], list[dict]]:
    """You.com web search (optional: needs YDC_API_KEY). Web only, no image search.

    Returns (image candidates from result thumbnails, page results for the crawl).
    """
    key = os.getenv("YDC_API_KEY")
    if not key:
        return [], []
    own = client is None
    c = client or httpx.AsyncClient(timeout=20)
    imgs, pages = [], []
    try:
        for q in queries[:6]:  # same budget as Brave
            try:
                r = await c.get(YOU_API, params={"query": q, "count": 10}, headers={"X-API-Key": key})
                r.raise_for_status()
                for x in (r.json().get("results") or {}).get("web", []):
                    if not x.get("url"):
                        continue
                    pages.append({"page_url": x["url"], "title": x.get("title", "")})
                    thumb = x.get("original_thumbnail_url") or x.get("thumbnail_url")
                    if thumb:
                        imgs.append({"image_url": unescape(thumb), "thumbnail": unescape(x.get("thumbnail_url") or thumb),
                                     "page_url": x["url"], "title": x.get("title", ""), "source": f"you-api:{q}"})
            except Exception as e:
                errors.append(f"you-api: {type(e).__name__}")
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403, 429):
                    break
    finally:
        if own:
            await c.aclose()
    return imgs, pages


def extract_images(html: bytes, base_url: str, max_n: int = 6) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for sel, attr in (('meta[property="og:image"]', "content"), ('meta[name="twitter:image"]', "content")):
        urls += [t.get(attr) for t in soup.select(sel)]
    for img in soup.select("img"):
        for a in ("src", "data-src", "data-original"):
            if img.get(a):
                urls.append(img[a])
        if img.get("srcset"):
            urls.append(img["srcset"].split(",")[-1].strip().split(" ")[0])
    out = []
    for u in urls:
        if not u or u.startswith(("data:", "javascript:")):
            continue
        u = urljoin(base_url, u)
        if urlparse(u).scheme in ("http", "https") and u not in out and not u.lower().endswith((".svg", ".gif")):
            out.append(u)
    return out[:max_n]


async def discover(brand: str, keywords: list[str], official: list[str], client: httpx.AsyncClient, errors: list) -> list[dict]:
    queries = expand_queries(brand, keywords, official)
    img_jobs = [asyncio.to_thread(_images, q, b, 20, errors) for q in queries for b in IMAGE_BACKENDS]
    page_queries = ([f'"{brand}" replica', f'"{brand}" buy cheap', f'"{brand}" wholesale'] + [f'"{brand}" {k} buy' for k in keywords[:2]]
                    + [f'"{brand}" giveaway', f'"{brand}" job offer'])  # brand impersonation scams
    text_jobs = [asyncio.to_thread(_text, q, 10, errors) for q in page_queries]
    *results, (api_imgs, api_pages), (you_imgs, you_pages) = await asyncio.gather(
        *img_jobs, *text_jobs, brave_api(queries + page_queries, errors), you_api(page_queries + queries, errors))
    imgs = [c for r in results[:len(img_jobs)] for c in r] + api_imgs + you_imgs
    pages = {p["page_url"]: p for r in results[len(img_jobs):] for p in r}
    pages.update({p["page_url"]: p for p in api_pages + you_pages})
    per_host, chosen = {}, []  # at most 3 pages per host
    for url, p in pages.items():
        h = urlparse(url).hostname or ""
        per_host[h] = per_host.get(h, 0) + 1
        if per_host[h] <= 3:
            chosen.append(p)

    sem = asyncio.Semaphore(8)

    async def crawl(p):
        async with sem:
            html = await net.fetch_html(client, p["page_url"])
            if not html:
                return []
            try:
                return [{"image_url": u, "thumbnail": None, "page_url": p["page_url"], "title": p["title"], "source": "page-crawl"}
                        for u in extract_images(html, p["page_url"])]
            except Exception:
                return []

    crawled = [c for r in await asyncio.gather(*(crawl(p) for p in chosen[:30])) for c in r]
    seen, out = set(), []
    for c in imgs + crawled:
        if c["image_url"] and c["image_url"] not in seen:
            seen.add(c["image_url"])
            out.append(c)
    return out


async def reverse_candidates(image_url: str, errors: list, client: httpx.AsyncClient | None = None) -> list[dict]:
    """Reverse image search via SerpApi: Google Lens exact+visual matches and Yandex.

    Returns candidates with keys: image_url, thumbnail, page_url, title, source, reverse.
    Errors are appended with short tags (no API keys); candidates from working engines are returned even if one fails.
    """
    key = os.getenv("SERPAPI_KEY")
    if not key or not image_url:
        return []

    engines = os.getenv("SERPAPI_ENGINES", "lens_exact,lens_visual,yandex").split(",")
    engines = [e.strip() for e in engines if e.strip()]

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30)

    try:
        async def fetch_engine(engine_name: str, engine_param: str, **params) -> list[dict]:
            try:
                r = await client.get("https://serpapi.com/search.json", params={**params, "engine": engine_param, "api_key": key})
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    errors.append(f"{engine_name}: {data['error'][:30]}")
                    return []

                cands = []
                if engine_name == "lens-exact":
                    for m in data.get("exact_matches", []):
                        img_url = m.get("image") or m.get("thumbnail")
                        if img_url:
                            cands.append({"image_url": img_url, "thumbnail": m.get("thumbnail"),
                                        "page_url": m.get("link"), "title": m.get("title", ""),
                                        "source": "lens-exact", "reverse": True})
                elif engine_name == "lens-visual":
                    for m in data.get("visual_matches", [])[:40]:
                        img_url = m.get("image") or m.get("thumbnail")
                        if img_url:
                            cands.append({"image_url": img_url, "thumbnail": m.get("thumbnail"),
                                        "page_url": m.get("link"), "title": m.get("title", ""),
                                        "source": "lens-visual", "reverse": True})
                elif engine_name == "yandex":
                    for m in data.get("images_results", [])[:40]:
                        img_url = m.get("original") or m.get("thumbnail")
                        if img_url:
                            cands.append({"image_url": img_url, "thumbnail": m.get("thumbnail"),
                                        "page_url": m.get("link"), "title": m.get("title", ""),
                                        "source": "yandex-reverse", "reverse": True})
                return cands
            except httpx.TimeoutException:
                errors.append(f"{engine_name}: timeout")
                return []
            except httpx.HTTPStatusError as e:
                errors.append(f"{engine_name}: HTTP {e.response.status_code}")
                return []
            except Exception as e:
                errors.append(f"{engine_name}: {type(e).__name__}")
                return []

        jobs = []
        if "lens_exact" in engines:
            jobs.append(("lens-exact", fetch_engine("lens-exact", "google_lens", url=image_url, type="exact_matches")))
        if "lens_visual" in engines:
            jobs.append(("lens-visual", fetch_engine("lens-visual", "google_lens", url=image_url, type="visual_matches")))
        if "yandex" in engines:
            jobs.append(("yandex", fetch_engine("yandex", "yandex_images", url=image_url)))

        results = await asyncio.gather(*[j[1] for j in jobs])

        # Order: exact, Yandex, visual; deduplicate by image_url
        seen, out = set(), []
        order_map = {"lens-exact": 0, "yandex-reverse": 1, "lens-visual": 2}
        all_cands = [c for r in results for c in r]
        all_cands.sort(key=lambda c: order_map.get(c["source"], 3))

        for c in all_cands:
            if c["image_url"] and c["image_url"] not in seen:
                seen.add(c["image_url"])
                out.append(c)

        return out
    finally:
        if own_client:
            await client.aclose()
