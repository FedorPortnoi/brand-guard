"""FastAPI endpoint for saving listing pages to the Wayback Machine."""
import asyncio

import httpx
from fastapi import APIRouter, HTTPException, Request

from . import archive
from .store import SESSIONS

router = APIRouter()
MAX_PER_CALL = 20


@router.post("/api/archive/{scan_id}")
async def archive_pages(scan_id: str, request: Request):
    """Snapshot the source pages of the given matches.

    POST body: {"idxs": [int, ...]}. Returns {"results": {idx: archive-result}}.
    Each result is also stored on the match, so drafts, PDFs and the ZIP include the archived link.
    """
    sess = SESSIONS.get(scan_id)
    if not sess:
        raise HTTPException(404, "Unknown scan")
    idxs = (await request.json()).get("idxs", [])
    if not idxs:
        raise HTTPException(400, "idxs list is empty")
    if len(idxs) > MAX_PER_CALL:
        raise HTTPException(400, f"At most {MAX_PER_CALL} pages per request")
    for idx in idxs:
        if idx not in sess["items"]:
            raise HTTPException(404, f"Unknown match index: {idx}")

    sem = asyncio.Semaphore(2)  # archive.org rate-limits saves

    async def one(idx: int, c: httpx.AsyncClient):
        match = sess["items"][idx]["match"]
        page = match.get("page_url")
        if not page or match.get("demo"):
            return idx, {"state": "skipped", "error": "no source page" if not page else "demo sample"}
        if (match.get("archive") or {}).get("state") == "saved":
            return idx, match["archive"]
        async with sem:
            res = await archive.snapshot(page, c)
        if res.get("url"):
            match["archive"] = res
        return idx, res

    async with httpx.AsyncClient(timeout=60) as c:
        results = dict(await asyncio.gather(*(one(i, c) for i in idxs)))
    return {"results": results}
