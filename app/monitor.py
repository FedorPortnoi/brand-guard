"""Scheduled re-scanning with alerts on new copies."""
import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .net import safe_url
from .pipeline import run_scan
from .store import DATA

MON_DIR = DATA / "monitor"
MON_DIR.mkdir(exist_ok=True)

_lock = asyncio.Lock()
_scheduler_task = None


def _atomic_write(path: Path, data: dict | list) -> None:
    """Write JSON atomically: temp file, then replace."""
    temp = path.parent / f"{path.name}.tmp"
    temp.write_text(json.dumps(data, default=str), encoding="utf8")
    os.replace(temp, path)


async def _load_file(path: Path) -> dict | list:
    """Load JSON, return {} or [] on missing file."""
    if not path.exists():
        return {} if path.name.endswith("watches.json") else []
    return json.loads(await asyncio.to_thread(path.read_text, encoding="utf8"))


def fingerprint(match: dict) -> str:
    """SHA256 if present, else page_url|image_url."""
    if match.get("sha256"):
        return match["sha256"]
    page_url = match.get("page_url", "")
    image_url = match.get("image_url", "")
    return hashlib.sha256(f"{page_url}|{image_url}".encode()).hexdigest()


def _pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Return singular or plural form based on count."""
    if count == 1:
        return singular
    if plural is None:
        # Handle common cases
        if singular == "match":
            plural = "matches"
        elif singular == "copy":
            plural = "copies"
        else:
            plural = singular + "s"
    return plural


async def run_watch(watch_id: str) -> dict:
    """Run a single watch: scan, detect new matches, update storage, alert.
    Returns {watch_id, scan_id, total, new, alerted} or {watch_id, skipped: "already running"}."""
    async with _lock:
        watches = await _load_file(MON_DIR / "watches.json")
        watch = watches.get(watch_id)
        if not watch:
            return {"watch_id": watch_id, "error": "watch not found"}
        if watch.get("running"):
            return {"watch_id": watch_id, "skipped": "already running"}

        watch["running"] = True
        _atomic_write(MON_DIR / "watches.json", watches)

    try:
        # Load the image
        img_path = MON_DIR / f"{watch_id}.img"
        if not img_path.exists():
            raise ValueError("image file not found")
        image_bytes = await asyncio.to_thread(img_path.read_bytes)

        # Run the scan
        brand = watch["brand"]
        keywords = watch["keywords"]
        official_domains = watch["official_domains"]
        image_url = watch.get("image_url", "")
        min_score = watch.get("min_score", 40)
        is_first_run = not watch.get("last_run_at")

        matches = []
        total = 0
        scan_id = None

        async for event, payload in run_scan(image_bytes, brand, keywords, official_domains, image_url, inject_demo=False,
                                             resellers=watch.get("resellers", [])):
            if event == "done":
                total = payload.get("scanned", 0)
                scan_id = payload.get("scan_id", "")
            elif event == "match":
                if payload.get("demo"):
                    continue
                if payload.get("risk", {}).get("category") in ("official", "authorised_reseller"):
                    continue
                matches.append(payload)

        if not scan_id:
            raise ValueError("no scan_id returned")

        # Check for new matches
        seen = set(watch.get("seen", []))
        new_matches = []
        for match in matches:
            fp = fingerprint(match)
            if fp not in seen:
                new_matches.append(match)
                seen.add(fp)

        # Filter by min_score for alerting
        high_new = [m for m in new_matches if m.get("risk", {}).get("score", 0) >= min_score]

        # Create alert(s)
        alerted = False
        new_alert = None

        if is_first_run:
            # Baseline alert
            new_alert = {
                "id": str(uuid.uuid4())[:12],
                "watch_id": watch_id,
                "brand": brand,
                "type": "baseline",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message": f"Baseline recorded: {len(matches)} {_pluralize(len(matches), 'match')}",
                "items": [],
                "read": False,
            }
            alerted = True
        elif high_new:
            # New matches alert
            items = []
            for match in high_new:
                thumb = match.get("thumb", "")
                label = "unverified"
                if match.get("forensics"):
                    label = match["forensics"].get("label", "unverified")
                items.append({
                    "idx": match.get("idx"),
                    "scan_id": scan_id,
                    "host": match.get("risk", {}).get("host", ""),
                    "page_url": match.get("page_url", ""),
                    "image_url": match.get("image_url", ""),
                    "title": match.get("title", ""),
                    "score": match.get("risk", {}).get("score", 0),
                    "category": match.get("risk", {}).get("category", "unknown"),
                    "label": label,
                    "thumb": thumb,
                })
            last_run = watch.get("last_run_at")
            since = last_run[:10] if last_run else "unknown"
            copy_text = _pluralize(len(high_new), "copy", "copies")
            new_alert = {
                "id": str(uuid.uuid4())[:12],
                "watch_id": watch_id,
                "brand": brand,
                "type": "new_matches",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message": f"{len(high_new)} new likely {copy_text} of {brand} since {since}",
                "items": items,
                "read": False,
            }
            alerted = True

            # Send webhook if configured
            if watch.get("webhook_url"):
                webhook_url = watch["webhook_url"]
                if await asyncio.to_thread(safe_url, webhook_url):
                    msg = new_alert["message"]
                    try:
                        async with httpx.AsyncClient(timeout=10) as client:
                            await client.post(webhook_url, json={"text": msg, "content": msg})
                    except Exception:
                        pass  # swallow webhook failures

        # Update history
        history = watch.get("history", [])
        history.append({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": len(matches),
            "new": len(new_matches),
            "high": len(high_new),
        })
        if len(history) > 50:
            history = history[-50:]

        # Update watch with fresh load
        now = datetime.now(timezone.utc)
        interval_hours = watch.get("interval_hours", 24)
        next_run_at = datetime.fromtimestamp(now.timestamp() + interval_hours * 3600, tz=timezone.utc)

        async with _lock:
            # Reload alerts
            alerts = await _load_file(MON_DIR / "alerts.json")
            if new_alert:
                alerts.append(new_alert)
            if len(alerts) > 200:
                alerts = alerts[-200:]
            _atomic_write(MON_DIR / "alerts.json", alerts)

            # Reload watches and update only if it still exists
            watches = await _load_file(MON_DIR / "watches.json")
            if watch_id in watches:
                watch = watches[watch_id]
                watch["seen"] = list(seen)
                watch["last_run_at"] = now.isoformat(timespec="seconds")
                watch["next_run_at"] = next_run_at.isoformat(timespec="seconds")
                watch["last_scan_id"] = scan_id
                watch["history"] = history
                watch["running"] = False
                _atomic_write(MON_DIR / "watches.json", watches)

        return {
            "watch_id": watch_id,
            "scan_id": scan_id,
            "total": len(matches),
            "new": len(new_matches),
            "alerted": alerted,
        }

    except Exception as e:
        # Record error alert
        async with _lock:
            alerts = await _load_file(MON_DIR / "alerts.json")
            alerts.append({
                "id": str(uuid.uuid4())[:12],
                "watch_id": watch_id,
                "brand": watch.get("brand", "unknown"),
                "type": "error",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message": f"Scan failed: {str(e)[:100]}",
                "items": [],
                "read": False,
            })
            if len(alerts) > 200:
                alerts = alerts[-200:]
            _atomic_write(MON_DIR / "alerts.json", alerts)

            watches = await _load_file(MON_DIR / "watches.json")
            if watch_id in watches:
                watches[watch_id]["running"] = False
                _atomic_write(MON_DIR / "watches.json", watches)

        return {"watch_id": watch_id, "error": str(e)}


async def scheduler_loop(stop: asyncio.Event, tick: float = 60) -> None:
    """Run due watches sequentially. Survives exceptions."""
    while not stop.is_set():
        try:
            now = datetime.now(timezone.utc).timestamp()
            watches = await _load_file(MON_DIR / "watches.json")
            for watch_id, watch in list(watches.items()):
                if stop.is_set():
                    break
                next_run = watch.get("next_run_at")
                if isinstance(next_run, str):
                    try:
                        next_run = datetime.fromisoformat(next_run).timestamp()
                    except Exception:
                        next_run = float("inf")
                else:
                    next_run = float("inf")
                running = watch.get("running", False)
                if next_run <= now and not running:
                    await run_watch(watch_id)
        except Exception:
            pass

        try:
            await asyncio.wait_for(stop.wait(), timeout=tick)
            break
        except asyncio.TimeoutError:
            pass


def start() -> asyncio.Task | None:
    """Start the scheduler. Returns None if BG_MONITOR=0."""
    if os.getenv("BG_MONITOR") == "0":
        return None
    global _scheduler_task
    stop = asyncio.Event()
    _scheduler_task = asyncio.create_task(scheduler_loop(stop))
    _scheduler_task._stop_event = stop  # type: ignore
    # Clear any stale running flags
    try:
        watches = json.loads((MON_DIR / "watches.json").read_text(encoding="utf8"))
        for w in watches.values():
            w["running"] = False
        _atomic_write(MON_DIR / "watches.json", watches)
    except Exception:
        pass
    return _scheduler_task


async def stop() -> None:
    """Stop the scheduler (waits up to 5s, cancels if still running)."""
    global _scheduler_task
    if _scheduler_task and hasattr(_scheduler_task, "_stop_event"):
        _scheduler_task._stop_event.set()  # type: ignore
        try:
            await asyncio.wait_for(_scheduler_task, timeout=5)
        except asyncio.TimeoutError:
            _scheduler_task.cancel()
            try:
                await _scheduler_task
            except asyncio.CancelledError:
                pass


# API helpers - use _lock to prevent lost writes
async def add_watch(watch_id: str, watch: dict) -> None:
    """Add a watch to storage."""
    async with _lock:
        watches = await _load_file(MON_DIR / "watches.json")
        watches[watch_id] = watch
        _atomic_write(MON_DIR / "watches.json", watches)


async def delete_watch(watch_id: str) -> bool:
    """Delete a watch. Returns True if found, False otherwise."""
    async with _lock:
        watches = await _load_file(MON_DIR / "watches.json")
        if watch_id not in watches:
            return False
        del watches[watch_id]
        _atomic_write(MON_DIR / "watches.json", watches)
    return True


async def mark_alerts_read(alert_ids: set[str] | None = None, mark_all: bool = False) -> None:
    """Mark alerts as read. If mark_all, mark all alerts."""
    async with _lock:
        alerts = await _load_file(MON_DIR / "alerts.json")
        if mark_all:
            for a in alerts:
                a["read"] = True
        elif alert_ids:
            for a in alerts:
                if a["id"] in alert_ids:
                    a["read"] = True
        _atomic_write(MON_DIR / "alerts.json", alerts)
