"""FastAPI endpoints for takedown draft generation."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from . import takedown
from .store import SESSIONS

router = APIRouter()


@router.post("/api/takedown/{scan_id}")
async def create_drafts(scan_id: str, request: Request):
    """Generate takedown drafts for selected matches.

    POST body: {"idxs": [int, ...], "rights_holder": {name?, company?, email?, ...}}

    Returns: {"drafts": [draft, ...]}
    """
    sess = SESSIONS.get(scan_id)
    if not sess:
        raise HTTPException(404, "Unknown scan")

    body = await request.json()
    idxs = body.get("idxs", [])
    rights_holder = body.get("rights_holder", {})

    if not idxs:
        raise HTTPException(400, "idxs list is empty")

    # Validate all idxs exist
    for idx in idxs:
        if idx not in sess["items"]:
            raise HTTPException(404, f"Unknown match index: {idx}")

    base_url = str(request.base_url).rstrip("/")
    drafts = await takedown.build_drafts(scan_id, idxs, rights_holder, base_url)

    return {"drafts": drafts}


@router.get("/api/takedown/{scan_id}/{idx}")
async def get_single_draft(scan_id: str, idx: int, request: Request):
    """Get draft for a single match with an empty rights_holder.

    Returns: {"drafts": [draft]}
    """
    sess = SESSIONS.get(scan_id)
    if not sess:
        raise HTTPException(404, "Unknown scan")

    if idx not in sess["items"]:
        raise HTTPException(404, "Unknown match index")

    base_url = str(request.base_url).rstrip("/")
    drafts = await takedown.build_drafts(scan_id, [idx], {}, base_url)

    return {"drafts": drafts}


@router.post("/api/takedown/{scan_id}/bundle.zip")
async def create_bundle(scan_id: str, request: Request):
    """Generate a ZIP bundle with notices and evidence PDFs.

    POST body: {"idxs": [int, ...], "rights_holder": {name?, company?, email?, ...}}

    Returns: application/zip file
    """
    sess = SESSIONS.get(scan_id)
    if not sess:
        raise HTTPException(404, "Unknown scan")

    body = await request.json()
    idxs = body.get("idxs", [])
    rights_holder = body.get("rights_holder", {})

    if not idxs:
        raise HTTPException(400, "idxs list is empty")

    # Validate all idxs exist
    for idx in idxs:
        if idx not in sess["items"]:
            raise HTTPException(404, f"Unknown match index: {idx}")

    base_url = str(request.base_url).rstrip("/")
    drafts = await takedown.build_drafts(scan_id, idxs, rights_holder, base_url)
    bundle = takedown.bundle_zip(scan_id, idxs, drafts)

    return Response(
        bundle,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="takedown-{scan_id}.zip"'}
    )
