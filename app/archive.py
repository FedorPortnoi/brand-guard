"""Wayback Machine snapshots of listing pages, so takedown evidence survives if the seller deletes the page.

Saving is anonymous by default (rate-limited by archive.org). With IA_ACCESS_KEY and IA_SECRET_KEY
(free archive.org account) it uses the authenticated Save Page Now API, which allows more saves.
If a save fails, the newest existing snapshot is used instead and marked as such.
Saving publishes a public copy of the page on archive.org; it only runs when the user asks.
"""
import asyncio
import os
import re
from datetime import datetime, timezone

import httpx

WAYBACK = "https://web.archive.org"
_SNAPSHOT = re.compile(r"/web/(\d{14})/")


def _when(ts: str) -> str:
    try:
        return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return ts


def _result(state: str, ts: str = "", original: str = "", error: str = "") -> dict:
    out = {"state": state}
    if ts:
        out.update(url=f"{WAYBACK}/web/{ts}/{original}", captured_at=_when(ts))
    if error:
        out["error"] = error
    return out


async def _save_anonymous(c: httpx.AsyncClient, url: str) -> dict:
    r = await c.get(f"{WAYBACK}/save/{url}", follow_redirects=False, timeout=120)
    m = _SNAPSHOT.search(r.headers.get("location", "") or r.headers.get("content-location", ""))
    if m:
        return _result("saved", m.group(1), url)
    return _result("failed", error=f"HTTP {r.status_code}")


async def _save_authenticated(c: httpx.AsyncClient, url: str, poll_every: float = 3, max_polls: int = 40) -> dict:
    h = {"Authorization": f"LOW {os.environ['IA_ACCESS_KEY']}:{os.environ['IA_SECRET_KEY']}", "Accept": "application/json"}
    r = await c.post(f"{WAYBACK}/save", data={"url": url}, headers=h, timeout=60)
    r.raise_for_status()
    job = r.json().get("job_id")
    if not job:
        return _result("failed", error=r.json().get("message", "no job id")[:80])
    for _ in range(max_polls):
        await asyncio.sleep(poll_every)
        s = (await c.get(f"{WAYBACK}/save/status/{job}", headers=h, timeout=30)).json()
        if s.get("status") == "success":
            return _result("saved", s.get("timestamp", ""), s.get("original_url") or url)
        if s.get("status") == "error":
            return _result("failed", error=(s.get("message") or s.get("status_ext") or "error")[:80])
    return _result("failed", error="timed out")


async def latest_snapshot(c: httpx.AsyncClient, url: str) -> dict | None:
    """Newest existing successful snapshot of url, from the CDX index."""
    r = await c.get(f"{WAYBACK}/cdx/search/cdx", timeout=30,
                    params={"url": url, "output": "json", "limit": -1, "fl": "timestamp,original", "filter": "statuscode:200"})
    r.raise_for_status()
    rows = r.json()
    if len(rows) > 1:
        ts, original = rows[-1][0], rows[-1][1]
        return _result("existing", ts, original)
    return None


async def snapshot(url: str, client: httpx.AsyncClient | None = None) -> dict:
    """Save url to the Wayback Machine. Returns {state: saved|existing|failed, url?, captured_at?, error?}."""
    own = client is None
    c = client or httpx.AsyncClient(timeout=60)
    try:
        try:
            auth = os.getenv("IA_ACCESS_KEY") and os.getenv("IA_SECRET_KEY")
            res = await (_save_authenticated(c, url) if auth else _save_anonymous(c, url))
        except Exception as e:
            res = _result("failed", error=type(e).__name__)
        if res["state"] == "saved":
            return res
        try:
            old = await latest_snapshot(c, url)
        except Exception:
            old = None
        if old:
            old["error"] = f"new save failed ({res.get('error', 'unknown')}); using the newest existing snapshot"
            return old
        return res
    finally:
        if own:
            await c.aclose()
