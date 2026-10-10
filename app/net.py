"""SSRF-safe fetching with manual, validated redirects."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

MAX_IMG = 8_000_000
MAX_HTML = 2_000_000


def safe_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


async def _get(client: httpx.AsyncClient, url: str, headers: dict, limit: int, want: str):
    """Return (bytes|None, status). Follows up to 3 redirects, validating every hop."""
    status = 0
    for _ in range(4):
        if not url or not await asyncio.to_thread(safe_url, url):
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
