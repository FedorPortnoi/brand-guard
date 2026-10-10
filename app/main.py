import asyncio
import base64
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # before app imports; real env vars win

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import (archive_api, ingest, investigations_api, labels_api, listing_api, monitor, monitor_api, pipeline, report, risk, sellers_api,
               takedown_api, video_api)
from . import net
from .store import DATA, ROOT, SESSIONS, UA, add_session, original_for


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop the monitor scheduler."""
    monitor.start()
    yield
    await monitor.stop()


app = FastAPI(title="Brand Authenticity Guard", lifespan=lifespan)
for mod in (monitor_api, sellers_api, takedown_api, labels_api, archive_api, video_api, investigations_api, listing_api):
    app.include_router(mod.router)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/scan")
async def scan(image: UploadFile | None = File(None), brand: str = Form(...), keywords: str = Form(""),
               official_domains: str = Form(""), image_url: str = Form(""), inject_demo: str = Form(""),
               resellers: str = Form(""),
               semantic_search: str = Form(""), origin: str = Form("scan"), source_url: str = Form(""),
               photo_url: str = Form("")):
    """photo_url instead of a file: scan a listing's own product photo (from a link check) without saving it first."""
    if image is not None:
        data = await image.read()
    elif photo_url.strip():
        async with httpx.AsyncClient(timeout=15, headers=UA) as c:
            data, _ = await net.fetch_image(c, photo_url.strip())
        if not data:
            raise HTTPException(422, "Could not download that photo. Save it and upload the file instead.")
        image_url = image_url or photo_url.strip()
    else:
        raise HTTPException(422, "Choose an image or video to scan.")
    filename = image.filename if image is not None else "listing-photo"
    try:
        prepared = await asyncio.to_thread(ingest.prepare_upload, data, filename or "upload")
    except ingest.IngestError as exc:
        raise HTTPException(422, str(exc)) from exc
    data = prepared.data
    kws = [k.strip() for k in keywords.split(",") if k.strip()]
    doms = risk.parse_domains(official_domains)
    source_url = (source_url or image_url).strip()
    # Reverse-image providers need the media URL itself. Social post links are retained as provenance instead.
    reverse_url = ingest.direct_image_url(source_url) or photo_url.strip()  # a listing's photo is public: Lens can fetch it

    async def stream():
        events: list[tuple[str, dict]] = []
        scan_id = None
        yield _sse("status", {"message": prepared.note})
        async for ev, d in pipeline.run_scan(data, brand, kws, doms, reverse_url, bool(inject_demo), bool(semantic_search),
                                             risk.parse_domains(resellers),
                                             origin if origin in ("scan", "imessage") else "scan"):
            scan_id = d.get("scan_id", scan_id)
            if scan_id in SESSIONS and source_url:
                SESSIONS[scan_id]["source_url"] = source_url
            if ev != "progress":
                events.append((ev, d))
            yield _sse(ev, d)
        if scan_id in SESSIONS:
            _record(SESSIONS[scan_id], events)

    return StreamingResponse(stream(), media_type="text/event-stream")


SCAN_HISTORY = 20


def _record(sess: dict, events: list):
    """Persist the latest scan so the demo can be replayed offline.

    Also keeps a timestamped copy in data/scans/ (newest SCAN_HISTORY), so a new scan never destroys a good one.
    Replay one with name="scans/<file stem>".
    """
    blob = json.dumps({
        "brand": sess["brand"], "original": base64.b64encode(sess["original"]).decode(),
        "source_url": sess.get("source_url", ""),
        "official_domains": sess.get("official_domains", []), "resellers": sess.get("resellers", []),
        "frames": [base64.b64encode(f).decode() for f in sess.get("frames", [])],
        "events": events, "evidence": {i: it["evidence"] for i, it in sess["items"].items()},
    })
    (DATA / "last_scan.json").write_text(blob, encoding="utf8")
    hist = DATA / "scans"
    hist.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    brand = re.sub(r"[^a-z0-9]+", "-", sess["brand"].lower()).strip("-")[:30] or "scan"
    (hist / f"{stamp}-{brand}.json").write_text(blob, encoding="utf8")
    for old in sorted(hist.glob("*.json"))[:-SCAN_HISTORY]:
        old.unlink()


OWN = ("official", "authorised_reseller")
_recording_cache: dict[Path, tuple[float, dict]] = {}


def _summary(path: Path) -> dict:
    """Brand, date and findings of one recording, cached by file time (the files are up to a few MB)."""
    mtime = path.stat().st_mtime
    hit = _recording_cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    rec = json.loads(path.read_text(encoding="utf8"))
    found = [d for ev, d in rec["events"] if ev == "match" and d.get("forensics")
             and d["risk"]["category"] not in OWN]
    out = {"brand": rec["brand"], "recorded_at": datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="minutes"),
           "flagged": len(found), "high": sum(d["risk"]["score"] >= 65 for d in found),
           "demo": any(d.get("demo") for d in found)}
    _recording_cache[path] = (mtime, out)
    return out


@app.get("/api/recordings")
def recordings():
    """Scans /api/replay can play back: the demo recording, the last scan, then the history, newest first."""
    files = [("demo", DATA / "demo.json"), ("last_scan", DATA / "last_scan.json")]
    files += [(f"scans/{p.stem}", p) for p in sorted((DATA / "scans").glob("*.json"), reverse=True)]
    out = []
    for name, path in files:
        try:
            out.append({"name": name, **_summary(path)})
        except (OSError, ValueError, KeyError):
            continue
    return out


@app.post("/api/replay")
async def replay(name: str = Form("last_scan")):
    p = Path(name)
    path = DATA / "scans" / f"{p.name}.json" if p.parent.name == "scans" else DATA / f"{p.name}.json"
    if not path.exists():
        raise HTTPException(404, "No recorded scan with that name. Run a scan first, or copy one to data/demo.json")
    rec = json.loads(path.read_text(encoding="utf8"))
    scan_id = uuid.uuid4().hex[:12]
    items = {}
    for ev, d in rec["events"]:
        if ev == "match":
            items[d["idx"]] = {"match": d, "evidence": rec["evidence"].get(str(d["idx"]))}
    add_session(scan_id, {"brand": rec["brand"], "original": base64.b64decode(rec["original"]),
                          "source_url": rec.get("source_url", ""),
                          "official_domains": rec.get("official_domains", []), "resellers": rec.get("resellers", []),
                          "items": items,
                          "frames": [base64.b64decode(f) for f in rec.get("frames", [])]})

    async def stream():
        yield _sse("original", {"image": "data:image/jpeg;base64," + rec["original"], "scan_id": scan_id, "brand": rec["brand"]})
        for ev, d in rec["events"]:
            if ev in ("status", "done", "keyframes", "lookalikes"):
                d = {**d, "scan_id": scan_id}
            yield _sse(ev, d)
            await asyncio.sleep(0.25 if ev == "match" else 0.02)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/original/{scan_id}.jpg")
def get_original(scan_id: str):
    """The scanned original, so a reverse-search engine can fetch it when PUBLIC_BASE_URL points at this server."""
    sess = SESSIONS.get(scan_id)
    if not sess:
        raise HTTPException(404)
    return Response(sess["original"], media_type="image/jpeg")


@app.get("/api/report/{scan_id}/{idx}.pdf")
def get_report(scan_id: str, idx: int):
    sess = SESSIONS.get(scan_id)
    item = sess and sess["items"].get(idx)
    if not item:
        raise HTTPException(404, "Unknown scan or match (server restarted? run the scan again)")
    ev = base64.b64decode(item["evidence"]) if item["evidence"] else None
    pdf = report.build(sess["brand"], original_for(sess, item["match"]), ev, item["match"])
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="evidence-{item["match"]["risk"]["host"] or idx}.pdf"'})


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
