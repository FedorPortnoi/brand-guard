"""API endpoints for monitoring: watches and alerts."""
import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from .monitor import MON_DIR, add_watch, delete_watch, mark_alerts_read, run_watch, _load_file
from . import risk
from .net import safe_url

router = APIRouter()

_pending_tasks = set()


@router.post("/api/watches")
async def create_watch(
    image: UploadFile = File(...),
    brand: str = Form(...),
    keywords: str = Form(""),
    official_domains: str = Form(""),
    resellers: str = Form(""),
    image_url: str = Form(""),
    interval_hours: str = Form("24"),
    min_score: str = Form("40"),
    webhook_url: str = Form(""),
    run_now: str = Query(""),  # Query parameter: ?run_now=1
):
    """Create a watch with an uploaded image. Optionally run immediately with ?run_now=1."""
    try:
        interval = float(interval_hours)
        if interval < 1:
            raise ValueError("interval_hours must be >= 1")
    except ValueError:
        raise HTTPException(400, "Invalid interval_hours (must be a number >= 1)")

    try:
        min_score_int = int(min_score)
    except ValueError:
        raise HTTPException(400, "Invalid min_score (must be an integer)")

    if webhook_url:
        safe = await asyncio.to_thread(safe_url, webhook_url)
        if not safe or not webhook_url.startswith("https://"):
            raise HTTPException(400, "webhook_url must be https:// and not a private IP")

    watch_id = str(uuid.uuid4())[:12]
    kws = [k.strip() for k in keywords.split(",") if k.strip()]
    doms = risk.parse_domains(official_domains)

    # Save image
    image_bytes = await image.read()
    img_path = MON_DIR / f"{watch_id}.img"
    await asyncio.to_thread(img_path.write_bytes, image_bytes)

    # Create watch record
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    watch = {
        "id": watch_id,
        "brand": brand,
        "keywords": kws,
        "official_domains": doms,
        "resellers": risk.parse_domains(resellers),
        "image_url": image_url,
        "interval_hours": interval,
        "min_score": min_score_int,
        "webhook_url": webhook_url,
        "created_at": now,
        "last_run_at": None,
        "next_run_at": now,
        "last_scan_id": None,
        "running": False,
        "seen": [],
        "history": [],
    }

    # Save watch
    await add_watch(watch_id, watch)

    result = {k: v for k, v in watch.items() if k != "seen"}

    # Start run if requested
    if run_now:
        task = asyncio.create_task(run_watch(watch_id))
        _pending_tasks.add(task)
        task.add_done_callback(_pending_tasks.discard)

    return result


@router.get("/api/watches")
async def list_watches():
    """List all watches without seen list, with seen_count."""
    watches = await _load_file(MON_DIR / "watches.json")
    result = []
    for wid, watch in watches.items():
        item = {k: v for k, v in watch.items() if k != "seen"}
        item["seen_count"] = len(watch.get("seen", []))
        result.append(item)
    return result


@router.delete("/api/watches/{watch_id}")
async def delete_watch_endpoint(watch_id: str):
    """Delete a watch and its image file."""
    deleted = await delete_watch(watch_id)
    if not deleted:
        raise HTTPException(404, "Watch not found")

    # Remove image file
    img_path = MON_DIR / f"{watch_id}.img"
    try:
        await asyncio.to_thread(img_path.unlink)
    except Exception:
        pass

    return {"deleted": True}


@router.post("/api/watches/{watch_id}/run")
async def run_watch_now(watch_id: str):
    """Start a watch run immediately. Returns 409 if already running."""
    watches = await _load_file(MON_DIR / "watches.json")
    if watch_id not in watches:
        raise HTTPException(404, "Watch not found")

    if watches[watch_id].get("running"):
        raise HTTPException(409, "Watch is already running")

    task = asyncio.create_task(run_watch(watch_id))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)

    return JSONResponse(status_code=202, content={"started": True})


@router.get("/api/alerts")
async def list_alerts(unread: int = 0):
    """List alerts, newest first. Filter to unread if unread=1."""
    alerts = await _load_file(MON_DIR / "alerts.json")
    if unread:
        alerts = [a for a in alerts if not a.get("read")]
    return list(reversed(alerts))


@router.post("/api/alerts/read")
async def mark_read(body: dict):
    """Mark alerts as read. Body: {"ids": [...]} or {"all": true}."""
    if body.get("all"):
        await mark_alerts_read(mark_all=True)
    else:
        ids = set(body.get("ids", []))
        await mark_alerts_read(alert_ids=ids)
    return {"marked": True}
