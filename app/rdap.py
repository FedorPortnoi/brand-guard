"""Free domain registration lookups over RDAP (no key). Cached on disk; any failure returns None fields."""
import asyncio
import json
import time
from datetime import datetime, timezone

import httpx

from .store import DATA

CACHE = DATA / "rdap_cache.json"
TTL = 7 * 86400
_lock = asyncio.Lock()


def _load() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf8"))
    except Exception:
        return {}


def _parse(doc: dict) -> dict:
    created = registrar = abuse = None
    for ev in doc.get("events", []):
        if ev.get("eventAction") == "registration":
            created = ev.get("eventDate")
    for ent in doc.get("entities", []):
        roles = ent.get("roles", [])
        vcard = (ent.get("vcardArray") or [None, []])[1]
        name = next((v[3] for v in vcard if v[0] == "fn"), None)
        if "registrar" in roles:
            registrar = name
        for sub in [ent] + ent.get("entities", []):
            if "abuse" in sub.get("roles", []):
                card = (sub.get("vcardArray") or [None, []])[1]
                abuse = abuse or next((v[3] for v in card if v[0] == "email"), None)
    age = None
    if created:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(created.replace("Z", "+00:00"))).days
        except ValueError:
            pass
    return {"created": created, "age_days": age, "registrar": registrar, "abuse_email": abuse}


async def lookup(domain: str, client: httpx.AsyncClient | None = None) -> dict:
    """Return {created, age_days, registrar, abuse_email} for a registrable domain (e.g. 'example.co.uk')."""
    empty = {"created": None, "age_days": None, "registrar": None, "abuse_email": None}
    if not domain:
        return empty
    cache = _load()
    hit = cache.get(domain)
    if hit and time.time() - hit.get("_t", 0) < TTL:
        return {k: v for k, v in hit.items() if k != "_t"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=10, follow_redirects=True)
    try:
        # rdap.org always redirects to the authoritative registry, so follow redirects even on a caller's client
        r = await client.get(f"https://rdap.org/domain/{domain}", headers={"Accept": "application/rdap+json"}, follow_redirects=True)
        info = _parse(r.json()) if r.status_code == 200 else empty
    except Exception:
        info = empty
    finally:
        if own:
            await client.aclose()
    async with _lock:
        cache = _load()
        cache[domain] = {**info, "_t": time.time()}
        CACHE.write_text(json.dumps(cache), encoding="utf8")
    return info
