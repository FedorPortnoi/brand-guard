"""Domain and URL reputation: PhishStats (free, no key) and Google Safe Browsing (optional key).

PhishStats gives two things per scan: a list of lookalike domains (brand name in the domain, reported as
phishing) and a set of reported hosts that raises the risk of any match found on them.
Safe Browsing (GOOGLE_SAFE_BROWSING_KEY) checks each match's page. Its Lookup API is for non-commercial
use; commercial use needs Google's Web Risk API.
"""
import os
import re

import httpx

from .risk import host_of
from .sellers import registrable_domain

PHISHSTATS = "https://api.phishstats.info/api/phishing"
SAFE_BROWSING = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
THREAT_TYPES = ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"]


def phishstats_enabled() -> bool:
    return os.getenv("BG_PHISHSTATS", "1") != "0"


def safe_browsing_enabled() -> bool:
    return bool(os.getenv("GOOGLE_SAFE_BROWSING_KEY"))


def brand_tokens(brand: str) -> list[str]:
    """'Louis Vuitton' -> ['louisvuitton', 'louis-vuitton']. Tokens under 4 characters match too much and are dropped."""
    b = brand.lower()
    out = []
    for t in (re.sub(r"[^a-z0-9]+", "", b), re.sub(r"[^a-z0-9]+", "-", b).strip("-")):
        if len(t) >= 4 and t not in out:
            out.append(t)
    return out


def _official(host: str, official: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in official if d)


async def phishstats_reports(brand: str, official: list[str], errors: list,
                             client: httpx.AsyncClient | None = None, size: int = 100) -> list[dict]:
    """Hosts in the PhishStats feed whose reported URLs contain the brand name, newest first.

    Each: host, domain, reports, last_seen, max_score, brand_in_domain, country.
    """
    if not phishstats_enabled():
        return []
    tokens = brand_tokens(brand)
    own = client is None
    c = client or httpx.AsyncClient(timeout=20)
    hosts: dict[str, dict] = {}
    try:
        for tok in tokens:
            try:
                r = await c.get(PHISHSTATS, params={"_where": f"(url,like,~{tok}~)", "_sort": "-date", "_size": size})
                r.raise_for_status()
                rows = r.json()
            except Exception as e:
                errors.append(f"phishstats: HTTP {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError)
                              else f"phishstats: {type(e).__name__}")
                break
            for x in rows if isinstance(rows, list) else []:
                host = host_of(x.get("url") or "")
                if not host or _official(host, official):
                    continue
                dom = registrable_domain(host)
                h = hosts.setdefault(host, {"host": host, "domain": dom, "reports": 0, "last_seen": "", "max_score": 0.0,
                                            "brand_in_domain": any(t in dom for t in tokens),
                                            "country": x.get("countryname")})
                h["reports"] += 1
                h["last_seen"] = max(h["last_seen"], (x.get("date") or "")[:10])
                try:
                    h["max_score"] = max(h["max_score"], float(x.get("score") or 0))
                except (TypeError, ValueError):
                    pass
    finally:
        if own:
            await c.aclose()
    return sorted(hosts.values(), key=lambda h: h["last_seen"], reverse=True)


class ScanReputation:
    """Per-scan reputation checks applied to each match's risk dict."""

    def __init__(self, phish_hosts: list[dict]):
        self.by_host = {h["host"]: h for h in phish_hosts}
        # a whole domain is flagged only when it is a brand lookalike, never a shared host like sites.google.com
        self.by_domain = {h["domain"]: h for h in phish_hosts if h["brand_in_domain"]}
        self._sb: dict[str, list[str]] = {}

    def phish_hit(self, url: str) -> dict | None:
        host = host_of(url)
        return self.by_host.get(host) or self.by_domain.get(registrable_domain(host))

    async def safe_browsing(self, url: str, client: httpx.AsyncClient, errors: list) -> list[str]:
        """Threat types Google Safe Browsing reports for url (empty if none, not configured, or the call fails)."""
        if not safe_browsing_enabled() or not url:
            return []
        if url not in self._sb:
            body = {"client": {"clientId": "brand-guard", "clientVersion": "1.0"},
                    "threatInfo": {"threatTypes": THREAT_TYPES, "platformTypes": ["ANY_PLATFORM"],
                                   "threatEntryTypes": ["URL"], "threatEntries": [{"url": url}]}}
            try:
                r = await client.post(SAFE_BROWSING, params={"key": os.environ["GOOGLE_SAFE_BROWSING_KEY"]}, json=body, timeout=10)
                r.raise_for_status()
                self._sb[url] = sorted({m.get("threatType", "") for m in r.json().get("matches", []) if m.get("threatType")})
            except Exception as e:
                errors.append(f"safe-browsing: HTTP {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError)
                              else f"safe-browsing: {type(e).__name__}")
                return []
        return self._sb[url]

    async def apply(self, r: dict, url: str, client: httpx.AsyncClient, errors: list) -> dict:
        """Raise the score and add reasons for reported pages. Official domains and authorised resellers are left alone."""
        if r.get("category") in ("official", "authorised_reseller"):
            return r
        hit = self.phish_hit(url)
        if hit:
            r["score"] = min(100, r["score"] + 20)
            r["reasons"].append(f"Domain reported as phishing in the PhishStats feed ({hit['reports']} report"
                                f"{'' if hit['reports'] == 1 else 's'}, last seen {hit['last_seen'] or 'unknown'})")
        threats = await self.safe_browsing(url, client, errors)
        if threats:
            r["score"] = min(100, r["score"] + 25)
            r["reasons"].append("Google Safe Browsing flags this page: " + ", ".join(t.replace("_", " ").lower() for t in threats))
        return r
