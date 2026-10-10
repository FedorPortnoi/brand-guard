"""The scan pipeline, independent of HTTP. Used by the /api/scan stream and by scheduled monitoring."""
import asyncio
import base64
import collections
import contextlib
import hashlib
import io
import os
import random
import time
import uuid
from datetime import datetime, timezone

import httpx

from . import demo, forensics, infra, investigations, llm, marketplaces, net, reputation, risk, search, semantic, video
from .store import UA, add_session


def shrink(data: bytes, side=800) -> bytes:
    return _jpeg(forensics.load(data), side)


def _jpeg(img, side=800) -> bytes:
    img.thumbnail((side, side))
    b = io.BytesIO(); img.save(b, "JPEG", quality=82)
    return b.getvalue()


def small_copy(blob: bytes) -> tuple[bytes, dict]:
    """A candidate parked for the same-product pass: its evidence-size copy (same bytes as shrink(blob)) plus what that
    pass and the evidence need from the full-size image, so the original need not stay in memory until the scan ends."""
    img = forensics.load(blob)
    orig = {**semantic.fingerprint(blob, img), "sha256": hashlib.sha256(blob).hexdigest()}
    return _jpeg(img), orig


# reverse-image hits first, then marketplaces and crawled pages, generic image search last; the semaphores serve
# candidates in this order, so the strongest evidence arrives first while every candidate is still checked
SOURCE_RANK = {"lens-exact": 0, "yandex-reverse": 1, "lens-visual": 1, "ebay-image": 2, "aliexpress": 2,
               "DEMO SAMPLE": 2, "page-crawl": 3}


def source_rank(c: dict) -> int:
    return SOURCE_RANK.get(str(c.get("source") or "").split(":")[0], 4)


# Seconds one candidate may spend downloading: its full-size image (with retries), then its thumbnail. A few slow
# hosts used to hold the scan open for a minute after everything else was done.
DOWNLOAD_BUDGET, THUMB_BUDGET = 15, 8
SLOW_HOST = 3  # timeouts after which a host's remaining images are not downloaded


async def within(seconds: float, aw):
    """aw's result, or None if it takes longer than seconds."""
    try:
        async with asyncio.timeout(seconds):
            return await aw
    except TimeoutError:
        return None


def thumb_uri(data: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(shrink(data, 160)).decode()


def public_image_url(scan_id: str, image_url: str) -> str:
    """URL a reverse-search engine can fetch: the user's public URL, else our own endpoint if PUBLIC_BASE_URL is set."""
    if image_url:
        return image_url
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/api/original/{scan_id}.jpg" if base else ""


async def run_scan(data: bytes, brand: str, keywords: list[str], official_domains: list[str],
                   image_url: str = "", inject_demo: bool = False, semantic_search: bool = False,
                   resellers: list[str] | None = None, origin: str = "scan"):
    """Async generator of (event, payload). Events: status, progress, match, done, error.
    Registers the scan in store.SESSIONS so reports/takedowns work for it.
    semantic_search: also look for the same product in a different photo (CLIP; slower, needs the optional deps).
    data may be a video: its key frames become the references, and a candidate matching any frame is a match.
    origin: what started the scan (scan, watch, imessage), kept in the investigation log."""
    doms = official_domains
    resellers = resellers or []
    t_start = time.perf_counter()
    timings: collections.Counter = collections.Counter()  # seconds per stage, reported with "done"

    @contextlib.contextmanager
    def timed(stage: str):
        t = time.perf_counter()
        try:
            yield
        finally:
            timings[stage] += time.perf_counter() - t
    is_vid = video.is_video(data)
    use_sem = semantic_search and semantic.enabled() and not is_vid
    frames: list[dict] = []
    try:
        if is_vid:
            frames = await asyncio.to_thread(video.key_frames, data)
            image = frames[0]["jpeg"]
            refs = [await asyncio.to_thread(forensics.Reference, f["jpeg"]) for f in frames]
            refs[0].c2pa = "unknown"
        else:
            image = data
            refs = [await asyncio.to_thread(forensics.Reference, data)]
            refs[0].c2pa = (await asyncio.to_thread(forensics.c2pa_status, data))["state"]
        original = await asyncio.to_thread(shrink, image)
        sem_ref = await asyncio.to_thread(semantic.Embedding, data) if use_sem else None
    except video.VideoError as e:
        yield "error", {"message": str(e)}
        return
    except Exception:
        yield "error", {"message": "Could not read that video." if is_vid else "Could not read that image."}
        return
    ref = refs[0]
    scan_id = uuid.uuid4().hex[:12]
    sess = add_session(scan_id, {"brand": brand, "original": original, "official_domains": doms, "resellers": resellers,
                                 "items": {}})
    errors: list = []

    def logged(fn, *args):
        """Write to the investigation log; a failing log never stops the scan."""
        try:
            return fn(*args)
        except Exception as e:
            errors.append(f"investigation log: {type(e).__name__}")
            return None

    logged(investigations.start, scan_id, brand, keywords, doms, resellers, hashlib.sha256(data).hexdigest(), origin)
    yield "status", {"message": "Searching the web…", "scan_id": scan_id}
    if is_vid:
        sess["frames"] = [await asyncio.to_thread(shrink, f["jpeg"]) for f in frames]
        yield "keyframes", {"scan_id": scan_id, "frames": [{"t": f["t"], "thumb": f["thumb"]} for f in frames]}
        yield "status", {"message": f"Video: checking copies of {len(frames)} key frames"
                                    + (". Same-product search works on photos only, so it is off for videos." if semantic_search else "")}
    if semantic_search and not use_sem and not is_vid:
        yield "status", {"message": "Same-product search is not installed (pip install -r requirements-clip.txt); "
                                    "running pixel matching only."}
    t_search = time.perf_counter()
    async with httpx.AsyncClient(timeout=10, headers=UA) as dclient:
        *found, phish = await asyncio.gather(search.discover(brand, keywords, doms, dclient, errors),
                                             search.reverse_candidates(public_image_url(scan_id, image_url), errors),
                                             marketplaces.ebay_image_candidates(original, errors),
                                             marketplaces.aliexpress_candidates(brand, keywords, errors),
                                             reputation.phishstats_reports(brand, doms, errors))
    timings["search"] = time.perf_counter() - t_search
    cands = [c for group in found for c in group]
    rep = reputation.ScanReputation(phish)
    lookalikes = [h for h in phish if h["brand_in_domain"]]
    if lookalikes:
        sess["lookalikes"] = lookalikes
        yield "lookalikes", {"scan_id": scan_id, "domains": lookalikes[:30]}
    if errors:
        yield "status", {"message": "Some search backends failed: " + ", ".join(sorted(set(errors))[:4])}
    if inject_demo:
        cands += demo.doctored_copies(image)
    sources = collections.Counter(str(c.get("source") or "unknown").split(":")[0] for c in cands)
    # the same image on the same page from several sources is checked once, by the strongest source
    seen: set[tuple] = set()
    n_found = len(cands)
    cands = [c for c in sorted(cands, key=source_rank)
             if (k := (c["image_url"], c.get("page_url"))) not in seen and not seen.add(k)]
    dedup = {"duplicate_candidates": n_found - len(cands), "same_bytes": 0}
    # pages on the brand's own sites and resellers are never flagged, so their images are not downloaded at all
    # (a live scan spent most of its time waiting on 116 images from the brand's own shop, which throttled us)
    own = [c for c in cands if not c.get("demo")
           and risk.on_domains(risk.host_of(c.get("page_url") or c["image_url"]), doms + resellers)]
    own_ids = {id(c) for c in own}
    cands = [c for c in cands if id(c) not in own_ids]
    yield "status", {"message": f"Verifying {len(cands)} candidates against your original"
                                f"{f' ({len(own)} on your own sites skipped)' if own else ''}…", "total": len(cands)}

    # downloads mostly wait on the network, comparisons on the CPU: separate limits, so a slow site never holds
    # a comparison slot. A download slot is held until its image is compared, which bounds the images in memory.
    dl_sem, cpu_sem = asyncio.Semaphore(32), asyncio.Semaphore(os.cpu_count() or 4)
    verdicts: dict[str, asyncio.Future] = {}  # sha256 -> comparison, so identical bytes at another address reuse it
    stats = {"verified": 0, "thumbnail_only": 0, "unverified": 0, "unrelated": 0, "semantic": 0}
    unverified_pages: set[str] = set()  # one context-only row per page: its other images add nothing
    leftovers: list[tuple[dict, bytes, str, dict]] = []  # not a pixel copy: small_copy() input to the semantic pass

    matched_pages: set[str] = set()
    timeouts: collections.Counter = collections.Counter()  # image host -> failed downloads; see fetch_original

    def register(res: dict) -> dict:
        idx = len(sess["items"])
        ev = res.pop("_evidence", None)
        res["idx"] = idx
        res.setdefault("captured_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        sess["items"][idx] = {"match": res, "evidence": ev}
        if res.get("page_url"):
            matched_pages.add(res["page_url"])
        return res

    def emit(res: dict) -> dict:
        res = register(res)
        if not res.get("demo"):
            logged(investigations.add_listing, scan_id, res, res.get("server") or {})
        return res

    async with httpx.AsyncClient(timeout=10, headers=UA) as client:
        async def trace(m: dict) -> dict:
            """Who hosts the listing, and whether its server, site or URL is already in the investigation log."""
            if m.get("demo"):
                return m
            host = m["risk"].get("host") or ""
            with timed("server"):
                server = await infra.describe(host, client)
            seen = logged(investigations.earlier, scan_id, host, m.get("page_url") or "", server["ips"],
                          server["platform"]) or []
            m["server"] = {**server, "seen_before": seen[:12], "seen_count": len(seen)}
            m["risk"]["reasons"] += investigations.link_reasons(server, seen)
            return m

        async def finish(c: dict, blob: bytes, f: dict, verification: str, orig: dict | None = None) -> dict:
            page = c["page_url"] = await marketplaces.canonical_page_url(client, c.get("page_url"))
            r = risk.assess(page or c["image_url"], c.get("title", ""), doms, f, ref.c2pa, resellers)
            await rep.apply(r, page or c["image_url"], client, errors)
            if llm.enabled() and page and not c.get("demo") and 25 <= r["score"] <= 75:  # only ambiguous cases
                html = await net.fetch_html(client, page)
                verdict = html and await llm.classify(brand, r["host"], llm.page_context(html))
                if verdict:
                    r["llm"] = verdict
                    r["reasons"].append(f"Page-context classifier: {verdict['category'].replace('_', ' ')} "
                                        f"({verdict['confidence']:.0%}) - {verdict['explanation']}")
                    adj = {"authorised_reseller": -15, "official": -25, "fan_or_editorial": -15, "likely_counterfeit": 20}
                    if verdict["confidence"] >= 0.6:
                        r["score"] = max(0, min(100, r["score"] + adj.get(verdict["category"], 0)))
                        if verdict["category"] == "likely_counterfeit":
                            r["category"] = "likely_counterfeit"
            m = {**{k: v for k, v in c.items() if k != "blob"}, "forensics": f, "risk": r,
                 "verification": verification, "thumb": thumb_uri(blob),
                 "sha256": orig["sha256"] if orig else hashlib.sha256(blob).hexdigest(), "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            # with orig, blob is already small_copy()'s evidence-size copy
            m["_evidence"] = base64.b64encode(blob if orig else await asyncio.to_thread(shrink, blob)).decode()
            return await trace(m)

        async def compare_once(blob: bytes):
            key = hashlib.sha256(blob).hexdigest()
            if key not in verdicts:
                async def run():
                    async with cpu_sem:
                        with timed("compare"):
                            return await asyncio.to_thread(forensics.compare_best, refs, blob)
                verdicts[key] = asyncio.ensure_future(run())
            else:
                dedup["same_bytes"] += 1
            f, fi = await verdicts[key]
            return (dict(f) if f else None), fi  # a copy: each match row owns its forensics dict

        async def fetch_original(url: str, page: str | None) -> bytes | None:
            """The full-size image, with retries. A host that has timed out SLOW_HOST times is not tried again in
            this scan (its candidates fall back to their thumbnails), so one stalled site cannot slow every scan."""
            host = risk.host_of(url)
            if timeouts[host] >= SLOW_HOST:
                return None
            try:
                blob, st = await net.fetch_image(client, url, page)
                if not blob and st in (429, 500, 502, 503, 504, -2):  # transient: one jittered retry
                    timeouts[host] += st == -2
                    await asyncio.sleep(0.5 + random.random())
                    blob, st = await net.fetch_image(client, url, page)
                if not blob and st not in (401, 403):  # bare retry without Referer
                    blob, st = await net.fetch_image(client, url)
                timeouts[host] += not blob and st == -2
                return blob
            except asyncio.CancelledError:  # the download budget ran out
                timeouts[host] += 1
                raise

        async def work(c):
            async with dl_sem:
                page = c.get("page_url")
                verification = "verified"
                blob = c.get("blob")
                with timed("download"):
                    if not blob:
                        blob = await within(DOWNLOAD_BUDGET, fetch_original(c["image_url"], page))
                    if not blob:
                        blob = await within(THUMB_BUDGET, net.fetch_image(client, c.get("thumbnail")))
                        blob = blob and blob[0]
                        verification = "thumbnail-only (original blocked download; low-res comparison)"
                if not blob:
                    ctx = risk.context_only(page or c["image_url"], c.get("title", ""), doms, resellers)
                    stats["unverified"] += 1
                    if not ctx or (page and page in unverified_pages):
                        return None
                    if page:
                        unverified_pages.add(page)
                    page = c["page_url"] = await marketplaces.canonical_page_url(client, page)
                    await rep.apply(ctx, page or c["image_url"], client, errors)
                    return await trace({**{k: v for k, v in c.items() if k != "blob"}, "forensics": None, "risk": ctx,
                                        "verification": "unverified", "thumb": None})
                try:
                    f, fi = await compare_once(blob)
                except Exception:
                    return None
                if f and frames:
                    c = {**c, "ref_frame": {"idx": fi, "t": frames[fi]["t"]}}
                if not f:
                    if use_sem:
                        try:
                            async with cpu_sem:
                                small, orig = await asyncio.to_thread(small_copy, blob)
                            leftovers.append((c, small, verification, orig))
                        except Exception:
                            stats["unrelated"] += 1
                    else:
                        stats["unrelated"] += 1
                    return None
                stats["verified" if verification == "verified" else "thumbnail_only"] += 1  # pixel copies only
                with timed("evidence"):
                    return await finish(c, blob, f, verification)

        done = 0
        t_verify = time.perf_counter()
        for fut in asyncio.as_completed([work(c) for c in cands]):
            res = await fut
            done += 1
            if res:
                if "first_match" not in timings:
                    timings["first_match"] = time.perf_counter() - t_start
                yield "match", emit(res)
            if done % 5 == 0:
                yield "progress", {"done": done, "total": len(cands)}
        timings["verify"] = time.perf_counter() - t_verify

        t_same = time.perf_counter()
        if leftovers:  # one batched CLIP screen over everything that was not a pixel copy
            yield "status", {"message": f"Checking {len(leftovers)} other images for the same product in a different photo…"}
            try:
                ref_vec = await asyncio.to_thread(sem_ref.get_embedding)
                vecs = []
                for a in range(0, len(leftovers), 64):  # in chunks, so the page can show progress
                    vecs += await asyncio.to_thread(semantic.embed_many, [b for _, b, _, _ in leftovers[a:a + 64]])
                    yield "status", {"message": f"Same-product check: {len(vecs)}/{len(leftovers)} images…"}
            except Exception:
                ref_vec, vecs = None, [None] * len(leftovers)
            hits = []
            for (c, blob, verification, orig), v in zip(leftovers, vecs):
                sim = float(ref_vec @ v) if v is not None and ref_vec is not None else 0.0
                if sim >= semantic.SEMANTIC_MIN:
                    hits.append((sim, c, blob, verification, orig))
                else:
                    stats["unrelated"] += 1
            # best first; another photo of the same product on a page that already has a row adds nothing
            for sim, c, blob, verification, orig in sorted(hits, key=lambda h: -h[0]):
                if c.get("page_url") and c["page_url"] in matched_pages:
                    continue
                f = await asyncio.to_thread(semantic.compare, sem_ref, blob, orig)  # real evidence fields for the few hits
                if not f:
                    stats["unrelated"] += 1
                    continue
                stats["semantic"] += 1
                yield "match", emit(await finish(c, blob, f, verification, orig))
            timings["same_product"] = time.perf_counter() - t_same
    timings["total"] = time.perf_counter() - t_start
    logged(investigations.finish, scan_id, len(cands))
    # search/verify/same_product/total/first_match are wall-clock; download/compare/evidence are summed over the
    # parallel workers, so they show where the work goes rather than how long the user waited
    yield "done", {"scanned": len(cands), "scan_id": scan_id, **stats, "own_site": len(own),
                   "slow_hosts": sorted(h for h, n in timeouts.items() if n >= SLOW_HOST), "sources": dict(sources.most_common()),
                   "dedup": dedup, "timings": {k: round(v, 2) for k, v in timings.items()}}
