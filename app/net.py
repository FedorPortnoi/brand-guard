"""SSRF-safe fetching with manual, validated redirects."""
import asyncio
import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import httpx

MAX_IMG = 8_000_000
MAX_HTML = 2_000_000
# DNS lookups get their own threads, so they never queue behind the scan's image comparisons on the shared pool
_DNS = ThreadPoolExecutor(max_workers=32, thread_name_prefix="dns")
_SAFE_TTL = 300
_safe_hosts: dict[str, tuple[float, bool]] = {}  # hostname -> (checked at, safe); a scan hits the same CDNs often


def safe_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    return p.scheme in ("http", "https") and bool(p.hostname) and _safe_host(p.hostname)


def _safe_host(host: str) -> bool:
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


async def safe_url_async(url: str) -> bool:
    """safe_url without blocking the event loop, remembering each hostname's answer for a few minutes."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host, now = p.hostname.lower(), time.monotonic()
    hit = _safe_hosts.get(host)
    if hit and now - hit[0] < _SAFE_TTL:
        return hit[1]
    ok = await asyncio.get_running_loop().run_in_executor(_DNS, _safe_host, host)
    if len(_safe_hosts) > 5000:
        _safe_hosts.clear()
    _safe_hosts[host] = (now, ok)
    return ok


async def _get(client: httpx.AsyncClient, url: str, headers: dict, limit: int, want: str):
    """Return (bytes|None, status). Follows up to 3 redirects, validating every hop."""
    status = 0
    for _ in range(4):
        if not url or not await safe_url_async(url):
            return None, status or -1
        try:
            async with client.stream("GET", url, headers=headers, follow_redirects=False) as r:
                status = r.status_code
                if status in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if status != 200 or want not in r.headers.get("content-type", ""):
                    return None, status
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > limit:
                        return None, status
                return bytes(buf), status
        except Exception:
            return None, status or -2
    return None, status


async def fetch_image(client, url, referer=None):
    h = {"Accept": "image/*,*/*;q=0.5"}
    if referer:
        h["Referer"] = referer
    return await _get(client, url, h, MAX_IMG, "image")


async def fetch_html(client, url):
    data, _ = await _get(client, url, {"Accept": "text/html"}, MAX_HTML, "html")
    return data
