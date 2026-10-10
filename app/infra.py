"""Where a listing is hosted: its site's IP addresses and who runs the network they belong to (free RDAP, no key).

A shared IP only links two sites when the server is not a platform that hosts thousands of unrelated sites: every
Shopify shop resolves to a few Shopify addresses, and Cloudflare fronts millions of sites. Such networks are marked
"shared", so the investigation log can tell a real link (two shops on one rented server) from a coincidence.
"""
import asyncio
import json
import os
import socket
import time

import httpx

from . import net, risk
from .store import DATA

CACHE = DATA / "ipnet_cache.json"
TTL = 30 * 86400
_lock = asyncio.Lock()

# network names / owners (upper case) of platforms whose IPs are shared by many unrelated sites
SHARED = {"SHOPIFY": "Shopify", "CLOUDFLARE": "Cloudflare", "AMAZO-CF": "Amazon CloudFront", "CLOUDFRONT": "Amazon CloudFront",
          "FASTLY": "Fastly", "AKAMAI": "Akamai", "WIX": "Wix", "SQUARESPACE": "Squarespace", "AUTOMATTIC": "WordPress.com",
          "BIGCOMMERCE": "BigCommerce", "WEEBLY": "Weebly", "VERCEL": "Vercel", "NETLIFY": "Netlify", "GITHUB": "GitHub",
          "GOOGLE": "Google", "INCAPSULA": "Imperva", "SUCURI": "Sucuri", "STACKPATH": "StackPath", "EDGECAST": "Edgio",
          "BUNNY": "Bunny CDN", "ECWID": "Ecwid", "TILDA": "Tilda", "WEBFLOW": "Webflow"}


def enabled() -> bool:
    return os.getenv("BG_IP_LOOKUP", "1") != "0"


def platform_host(host: str) -> bool:
    """Marketplaces and social sites: a listing's IP is the platform's own, so it is not looked up."""
    return any(risk.host_matches(host, p) for p in risk.MARKETPLACES + risk.SOCIAL)


def shared_name(network: str | None, org: str | None) -> str | None:
    text = f"{network or ''} {org or ''}".upper()
    return next((label for key, label in SHARED.items() if key in text), None)


def _resolve(host: str) -> list[str]:
    try:
        ips = {i[4][0] for i in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        return []
    return sorted(ips, key=lambda ip: (":" in ip, ip))  # IPv4 first


async def resolve(host: str) -> list[str]:
    return await asyncio.get_running_loop().run_in_executor(net._DNS, _resolve, host)


def _load() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf8"))
    except Exception:
        return {}


def _parse(doc: dict) -> dict:
    org = None
    for ent in doc.get("entities", []):
        if "abuse" in ent.get("roles", []) and len(doc.get("entities", [])) > 1:
            continue
        card = (ent.get("vcardArray") or [None, []])[1]
        org = next((v[3] for v in card if v[0] == "fn"), None) or ent.get("handle")
        if org:
            break
    rng = f"{doc['startAddress']} - {doc['endAddress']}" if doc.get("startAddress") else None
    return {"network": doc.get("name"), "org": org, "range": rng}


async def network(ip: str, client: httpx.AsyncClient) -> dict:
    """{network, org, range} of the block an IP belongs to; None fields if the lookup fails. Cached on disk."""
    cache = _load()
    hit = cache.get(ip)
    if hit and time.time() - hit.get("_t", 0) < TTL:
        return {k: v for k, v in hit.items() if k != "_t"}
    try:
        r = await client.get(f"https://rdap.org/ip/{ip}", headers={"Accept": "application/rdap+json"},
                             follow_redirects=True, timeout=15)
        info = _parse(r.json()) if r.status_code == 200 else None
    except Exception:
        info = None
    if not info:
        return {"network": None, "org": None, "range": None}  # not cached: try again next time
    async with _lock:
        cache = _load()
        cache[ip] = {**info, "_t": time.time()}
        CACHE.write_text(json.dumps(cache), encoding="utf8")
    return info


async def describe(host: str, client: httpx.AsyncClient) -> dict:
    """The server behind a listing's site: {"ips": [...], "network", "org", "range", "shared": label or None,
    "platform": bool}. Marketplace and social hosts are not looked up (their IPs are the platform's)."""
    out = {"ips": [], "network": None, "org": None, "range": None, "shared": None, "platform": False}
    if not host or not enabled():
        return out
    if platform_host(host):
        return {**out, "platform": True}
    ips = await resolve(host)
    if not ips:
        return out
    info = await network(ips[0], client)
    return {**out, "ips": ips, **info, "shared": shared_name(info["network"], info["org"])}
