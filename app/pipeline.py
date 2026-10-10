"""The scan pipeline, independent of HTTP. Used by the /api/scan stream and by scheduled monitoring."""
import asyncio
import base64
import collections
import hashlib
import io
import os
import random
import uuid
from datetime import datetime, timezone

import httpx

from . import demo, forensics, llm, marketplaces, net, reputation, risk, search, semantic, video
from .store import SESSIONS, UA


def shrink(data: bytes, side=800) -> bytes:
    img = forensics.load(data)
    img.thumbnail((side, side))
    b = io.BytesIO(); img.save(b, "JPEG", quality=82)
    return b.getvalue()


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
                   resellers: list[str] | None = None):
    """Async generator of (event, payload). Events: status, progress, match, done, error.
    Registers the scan in store.SESSIONS so reports/takedowns work for it.
    semantic_search: also look for the same product in a different photo (CLIP; slower, needs the optional deps).
    data may be a video: its key frames become the references, and a candidate matching any frame is a match."""
    doms = official_domains
    resellers = resellers or []
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
    sess = SESSIONS[scan_id] = {"brand": brand, "original": original, "official_domains": doms, "resellers": resellers,
                                "items": {}}
    yield "status", {"message": "Searching the web…", "scan_id": scan_id}
    if is_vid:
        sess["frames"] = [await asyncio.to_thread(shrink, f["jpeg"]) for f in frames]
        yield "keyframes", {"scan_id": scan_id, "frames": [{"t": f["t"], "thumb": f["thumb"]} for f in frames]}
        yield "status", {"message": f"Video: checking copies of {len(frames)} key frames"
                                    + (". Same-product search works on photos only, so it is off for videos." if semantic_search else "")}
    if semantic_search and not use_sem and not is_vid:
        yield "status", {"message": "Same-product search is not installed (pip install -r requirements-clip.txt); "
                                    "running pixel matching only."}
    errors: list = []
    async with httpx.AsyncClient(timeout=10, headers=UA) as dclient:
        *found, phish = await asyncio.gather(search.discover(brand, keywords, doms, dclient, errors),
                                             search.reverse_candidates(public_image_url(scan_id, image_url), errors),
                                             marketplaces.ebay_image_candidates(original, errors),
                                             marketplaces.aliexpress_candidates(brand, keywords, errors),
                                             reputation.phishstats_reports(brand, doms, errors))
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
    yield "status", {"message": f"Verifying {len(cands)} candidates against your original…", "total": len(cands)}

    sem = asyncio.Semaphore(12)
    stats = {"verified": 0, "thumbnail_only": 0, "unverified": 0, "unrelated": 0, "semantic": 0}
    sources = collections.Counter(str(c.get("source") or "unknown").split(":")[0] for c in cands)
    unverified_pages: set[str] = set()  # one context-only row per page: its other images add nothing
    leftovers: list[tuple[dict, bytes, str]] = []  # downloaded but not a pixel copy: input to the semantic pass

    matched_pages: set[str] = set()

    def register(res: dict) -> dict:
        idx = len(sess["items"])
        ev = res.pop("_evidence", None)
        res["idx"] = idx
        res.setdefault("captured_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        sess["items"][idx] = {"match": res, "evidence": ev}
        if res.get("page_url"):
            matched_pages.add(res["page_url"])
        return res

    async with httpx.AsyncClient(timeout=10, headers=UA) as client:
        async def finish(c: dict, blob: bytes, f: dict, verification: str) -> dict:
            page = c["page_url"] = await marketplaces.canonical_page_url(client, c.get("page_url"))
            stats["verified" if verification == "verified" else "thumbnail_only"] += 1
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
                 "sha256": hashlib.sha256(blob).hexdigest(), "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            m["_evidence"] = base64.b64encode(await asyncio.to_thread(shrink, blob)).decode()
            return m

        async def work(c):
            async with sem:
                page = c.get("page_url")
                verification = "verified"
                blob = c.get("blob")
                if not blob:
                    blob, st = await net.fetch_image(client, c["image_url"], page)
                    if not blob and st in (429, 500, 502, 503, 504, -2):  # transient: one jittered retry
                        await asyncio.sleep(0.5 + random.random())
                        blob, st = await net.fetch_image(client, c["image_url"], page)
                    if not blob and st not in (401, 403):  # bare retry without Referer
                        blob, st = await net.fetch_image(client, c["image_url"])
                if not blob:
                    blob, _ = await net.fetch_image(client, c.get("thumbnail"))
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
                    return {**{k: v for k, v in c.items() if k != "blob"}, "forensics": None, "risk": ctx,
                            "verification": "unverified", "thumb": None}
                try:
                    f, fi = await asyncio.to_thread(forensics.compare_best, refs, blob)
                except Exception:
                    return None
                if f and frames:
                    c = {**c, "ref_frame": {"idx": fi, "t": frames[fi]["t"]}}
                if not f:
                    if use_sem:
                        leftovers.append((c, blob, verification))
                    else:
                        stats["unrelated"] += 1
                    return None
                return await finish(c, blob, f, verification)

        done = 0
        for fut in asyncio.as_completed([work(c) for c in cands]):
            res = await fut
            done += 1
            if res:
                yield "match", register(res)
            if done % 5 == 0:
                yield "progress", {"done": done, "total": len(cands)}

        if leftovers:  # one batched CLIP screen over everything that was not a pixel copy
            yield "status", {"message": f"Checking {len(leftovers)} other images for the same product in a different photo…"}
            try:
                ref_vec = await asyncio.to_thread(sem_ref.get_embedding)
                vecs = []
                for a in range(0, len(leftovers), 64):  # in chunks, so the page can show progress
                    vecs += await asyncio.to_thread(semantic.embed_many, [b for _, b, _ in leftovers[a:a + 64]])
                    yield "status", {"message": f"Same-product check: {len(vecs)}/{len(leftovers)} images…"}
            except Exception:
                ref_vec, vecs = None, [None] * len(leftovers)
            hits = []
            for (c, blob, verification), v in zip(leftovers, vecs):
                sim = float(ref_vec @ v) if v is not None and ref_vec is not None else 0.0
                if sim >= semantic.SEMANTIC_MIN:
                    hits.append((sim, c, blob, verification))
                else:
                    stats["unrelated"] += 1
            # best first; another photo of the same product on a page that already has a row adds nothing
            for sim, c, blob, verification in sorted(hits, key=lambda h: -h[0]):
                if c.get("page_url") and c["page_url"] in matched_pages:
                    continue
                f = await asyncio.to_thread(semantic.compare, sem_ref, blob)  # real evidence fields for the few hits
                if not f:
                    stats["unrelated"] += 1
                    continue
                stats["semantic"] += 1
                yield "match", register(await finish(c, blob, f, verification))
    yield "done", {"scanned": len(cands), "scan_id": scan_id, **stats, "sources": dict(sources.most_common())}
