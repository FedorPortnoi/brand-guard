"""Seller intelligence: group matches by seller/site and surface damning facts."""
import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from . import net, rdap, risk
from .store import SESSIONS


# Multi-part TLD suffix list (common ones)
MULTI_TLDS = {
    "co.uk", "com.au", "com.cn", "co.jp", "com.br", "co.in", "com.mx", "com.nz",
    "co.za", "co.kr", "com.tw", "co.id", "co.th", "com.sg", "com.hk", "com.my",
    "co.il", "com.ar", "com.ua", "com.sa", "co.ug", "co.ke",
    "co.nz", "com.tr", "com.ph", "com.pk", "com.vn", "com.co", "com.pe", "com.eg", "com.ng", "com.bd",
    "org.uk", "ac.uk", "gov.uk", "net.au", "org.au", "com.ve", "com.ec", "com.uy", "com.do", "com.gt",
}


def registrable_domain(host: str) -> str:
    """Extract registrable domain from hostname, e.g. 'shop.example.co.uk' -> 'example.co.uk'."""
    if not host:
        return ""
    host = host.lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # Check if last two parts form a multi-part TLD
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def seller_identity(match: dict) -> dict:
    """Extract seller identity from a match dict."""
    page_url = match.get("page_url", "")
    host = risk.host_of(page_url)

    # Check if it's a marketplace
    marketplace = None
    for m in risk.MARKETPLACES:
        if risk.host_matches(host, m):
            marketplace = m
            break

    if not marketplace:
        # Check if it's a social platform
        for s in risk.SOCIAL:
            if risk.host_matches(host, s):
                return {
                    "key": host,
                    "name": host,
                    "kind": "social",
                    "domain": host,
                }
        # Regular website
        reg_domain = registrable_domain(host)
        return {
            "key": reg_domain,
            "name": reg_domain,
            "kind": "website",
            "domain": reg_domain,
        }

    # Marketplace-specific seller extraction
    seller_name = None
    seller_id = None

    if match.get("seller"):
        # Given directly by a marketplace search source (eBay Browse API, OneFindMe)
        seller_name = str(match["seller"]).strip()[:80]

    elif "aliexpress" in marketplace:
        # AliExpress: /store/<id> or ?storeId=
        if "/store/" in page_url:
            seller_id = page_url.split("/store/")[1].split("/")[0].split("?")[0]
            seller_name = f"Store {seller_id}" if seller_id.isdigit() else seller_id
        else:
            parsed = parse_qs(urlparse(page_url).query)
            sid = parsed.get("storeId", [None])[0]
            if sid:
                seller_id = sid
                seller_name = f"Store {sid}"

    elif "dhgate" in marketplace:
        # DHgate: /store/<id> or shop/<id> or ?storeId=
        if "/store/" in page_url or "/shop/" in page_url:
            for prefix in ["/store/", "/shop/"]:
                if prefix in page_url:
                    seller_id = page_url.split(prefix)[1].split("/")[0].split("?")[0]
                    seller_name = f"Store {seller_id}" if seller_id.isdigit() else seller_id
                    break
        else:
            parsed = parse_qs(urlparse(page_url).query)
            sid = parsed.get("storeId", [None])[0]
            if sid:
                seller_id = sid
                seller_name = f"Store {sid}"

    elif "ebay" in marketplace:
        # eBay: /usr/<name> or /str/<name>
        if "/usr/" in page_url:
            seller_name = page_url.split("/usr/")[1].split("/")[0].split("?")[0]
        elif "/str/" in page_url:
            seller_name = page_url.split("/str/")[1].split("/")[0].split("?")[0]

    elif "etsy" in marketplace:
        # Etsy: /shop/<name>
        if "/shop/" in page_url:
            seller_name = page_url.split("/shop/")[1].split("/")[0].split("?")[0]

    elif "amazon" in marketplace:
        # Amazon: ?seller= or ?me= or /stores/<id>
        parsed = parse_qs(urlparse(page_url).query)
        seller_id = parsed.get("seller", [None])[0] or parsed.get("me", [None])[0]
        if seller_id:
            seller_name = seller_id
        elif "/stores/" in page_url:
            seller_id = page_url.split("/stores/")[1].split("/")[0].split("?")[0]
            seller_name = f"Store {seller_id}" if seller_id.isdigit() else seller_id

    elif "shopee" in marketplace:
        # Shopee: /shop/<id> or ?shopid=
        if "/shop/" in page_url:
            seller_id = page_url.split("/shop/")[1].split("/")[0].split("?")[0]
            seller_name = f"Store {seller_id}" if seller_id.isdigit() else seller_id
        else:
            parsed = parse_qs(urlparse(page_url).query)
            sid = parsed.get("shopid", [None])[0]
            if sid:
                seller_id = sid
                seller_name = f"Store {sid}"

    elif "temu" in marketplace:
        # Temu: /shop/<id> or seller_id query param
        if "/shop/" in page_url:
            seller_id = page_url.split("/shop/")[1].split("/")[0].split("?")[0]
            seller_name = f"Store {seller_id}" if seller_id.isdigit() else seller_id
        else:
            parsed = parse_qs(urlparse(page_url).query)
            sid = parsed.get("seller_id", [None])[0]
            if sid:
                seller_id = sid
                seller_name = f"Store {sid}"

    if seller_name:
        key = f"{host}:{seller_name}"
        return {
            "key": key,
            "name": seller_name,
            "kind": "marketplace_seller",
            "domain": host,
        }

    # Marketplace but no seller identified
    return {
        "key": f"{host}(unknown seller)",
        "name": f"{marketplace} (unknown seller)",
        "kind": "marketplace_listing",
        "domain": host,
    }


def seller_from_html(html: bytes, marketplace: str) -> str | None:
    """Extract seller name from HTML via JSON-LD or og:site_name.
    Handles @graph, offers arrays, and seller nested objects.
    Falls back to og:site_name only if it differs from marketplace name."""
    if not html:
        return None

    try:
        text = html.decode("utf-8", errors="ignore")
    except Exception:
        return None

    # Try JSON-LD first
    json_ld_pattern = r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>'
    for match in re.finditer(json_ld_pattern, text, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(match.group(1))
            # Handle both dict and list
            items = [data] if isinstance(data, dict) else data
            for item in items:
                seller = _extract_seller_from_json(item)
                if seller:
                    return seller
        except (json.JSONDecodeError, ValueError):
            continue

    # Fallback to og:site_name if it differs from marketplace
    og_match = re.search(r'<meta\s+property="og:site_name"\s+content="([^"]+)"', text, re.IGNORECASE)
    if og_match:
        og_name = og_match.group(1).strip()
        if og_name and marketplace.lower() not in og_name.lower():
            return og_name

    return None


def _extract_seller_from_json(data: dict) -> str | None:
    """Recursively extract seller name from JSON-LD object."""
    if not isinstance(data, dict):
        return None

    # Check @graph
    if "@graph" in data:
        graph = data["@graph"]
        if isinstance(graph, list):
            for item in graph:
                seller = _extract_seller_from_json(item)
                if seller:
                    return seller

    # Check offers.seller.name
    if "offers" in data:
        offers = data["offers"]
        if isinstance(offers, dict):
            if "seller" in offers:
                seller_obj = offers["seller"]
                if isinstance(seller_obj, dict) and "name" in seller_obj:
                    return seller_obj["name"]
        elif isinstance(offers, list):
            for offer in offers:
                if isinstance(offer, dict) and "seller" in offer:
                    seller_obj = offer["seller"]
                    if isinstance(seller_obj, dict) and "name" in seller_obj:
                        return seller_obj["name"]

    # Check seller.name at root
    if "seller" in data:
        seller_obj = data["seller"]
        if isinstance(seller_obj, dict) and "name" in seller_obj:
            return seller_obj["name"]

    return None


def group_matches(matches: list[dict]) -> list[dict]:
    """Group matches by seller identity, excluding 'official' category."""
    groups_dict: dict[str, dict] = {}

    for match in matches:
        # Skip official matches
        if match.get("risk", {}).get("category") in ("official", "authorised_reseller"):
            continue

        identity = seller_identity(match)
        key = identity["key"]

        if key not in groups_dict:
            groups_dict[key] = {
                "key": key,
                "name": identity["name"],
                "kind": identity["kind"],
                "domains": [identity["domain"]],
                "idxs": [],
                "listings": 0,
                "verified_listings": 0,
                "max_risk": 0,
                "avg_risk": 0.0,
                "verified_risks": [],
                "categories": {},
                "first_seen": match.get("captured_at", ""),
                "last_seen": match.get("captured_at", ""),
                "demo": match.get("demo", False),
                "flags": [],
                "seller_score": 0,
            }

        group = groups_dict[key]
        group["idxs"].append(match.get("idx"))
        group["listings"] += 1

        # Track domain list (unique)
        domain = identity["domain"]
        if domain not in group["domains"]:
            group["domains"].append(domain)

        # Count verified listings and track their risks
        verification = match.get("verification", "")
        if verification in ("verified", "thumbnail-only (original blocked download; low-res comparison)"):
            group["verified_listings"] += 1
            risk_score = match.get("risk", {}).get("score", 0)
            group["verified_risks"].append(risk_score)

        # Max risk
        risk_score = match.get("risk", {}).get("score", 0)
        group["max_risk"] = max(group["max_risk"], risk_score)

        # Track risk category counts
        category = match.get("risk", {}).get("category", "unknown")
        group["categories"][category] = group["categories"].get(category, 0) + 1

        # Track timestamps
        captured = match.get("captured_at", "")
        if captured:
            if not group["first_seen"] or captured < group["first_seen"]:
                group["first_seen"] = captured
            if captured > group["last_seen"]:
                group["last_seen"] = captured

        # All demo flag
        group["demo"] = group["demo"] and match.get("demo", False)

    # Compute avg_risk and remove temp tracking list
    for group in groups_dict.values():
        if group["verified_risks"]:
            group["avg_risk"] = round(sum(group["verified_risks"]) / len(group["verified_risks"]), 1)
        del group["verified_risks"]

    return list(groups_dict.values())


def score_and_flag(group: dict) -> None:
    """Compute seller_score and flags in place. Always overwrites flags."""
    score = group["max_risk"]
    flags = []

    # Bonus for repeat use (verified listings)
    verified = group["verified_listings"]
    if verified > 1:
        bonus = min(15, 3 * (verified - 1))
        score += bonus

    # Flag for multiple listings
    if group["listings"] >= 2:
        flags.append(f"Uses your image on {group['listings']} listing{'s' if group['listings'] > 1 else ''}")

    # Domain age penalty and flags
    domain_created = group.get("domain_created")
    domain_age = group.get("domain_age_days")
    if domain_age is not None:
        if domain_age < 90:
            score += 15
            if domain_created:
                try:
                    dt = datetime.fromisoformat(domain_created.replace("Z", "+00:00"))
                    created_str = dt.strftime("%Y-%m-%d")
                    flags.append(f"Domain registered {domain_age} days ago ({created_str})")
                except ValueError:
                    flags.append(f"Domain registered {domain_age} days ago")
        elif domain_age < 365:
            score += 8
            if domain_created:
                try:
                    dt = datetime.fromisoformat(domain_created.replace("Z", "+00:00"))
                    created_str = dt.strftime("%Y-%m-%d")
                    flags.append(f"Domain registered {domain_age} days ago ({created_str})")
                except ValueError:
                    flags.append(f"Domain registered {domain_age} days ago")

    # Cheap TLD penalty
    domain = group["domains"][0] if group["domains"] else ""
    if domain.endswith(risk.CHEAP_TLDS):
        score += 5
        tld = domain.rsplit(".", 1)[-1]
        flags.append(f"Low-trust TLD (.{tld})")

    # Marketplace seller bonus
    if group["kind"] == "marketplace_seller" and group["listings"] >= 2:
        score += 5
        marketplace = group["domains"][0] if group["domains"] else "unknown"
        flags.append(f"Marketplace seller on {marketplace}")

    # Unverified warning
    if verified == 0:
        flags.append("All matches unverified (download blocked)")

    # Clamp score to 0-100
    score = max(0, min(100, score))

    group["seller_score"] = score
    group["flags"] = flags


async def enrich_sellers(groups: list[dict], matches_by_idx: dict[int, dict], client=None, max_fetch: int = 12) -> None:
    """Add domain metadata and fetch missing seller names from marketplace listings.
    Modifies groups in place, adding domain_created, domain_age_days, registrar fields.
    Merges marketplace_listing groups into marketplace_seller groups when seller is found."""
    from .store import UA
    import httpx

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=10, headers=UA)

    try:
        sem = asyncio.Semaphore(6)
        tasks = []
        merged: list[dict] = []

        # Enrich website domains with RDAP data (skip if demo)
        async def rdap_lookup_task(group):
            async with sem:
                if not group.get("demo"):
                    domain = group["domains"][0]
                    info = await rdap.lookup(domain, client)
                    group["domain_created"] = info.get("created")
                    group["domain_age_days"] = info.get("age_days")
                    group["registrar"] = info.get("registrar")

        for group in groups:
            if group["kind"] == "website" and group["domains"]:
                tasks.append(rdap_lookup_task(group))

        # Fetch marketplace listings to extract seller names (up to max_fetch)
        to_fetch = [g for g in groups if g["kind"] == "marketplace_listing"][:max_fetch]

        async def fetch_seller_task(group):
            async with sem:
                # Get the first listing's page URL
                idx = group["idxs"][0] if group["idxs"] else None
                if idx is None or idx not in matches_by_idx:
                    return
                match = matches_by_idx[idx]
                page_url = match.get("page_url")
                if not page_url:
                    return

                # Fetch HTML and extract seller
                html = await net.fetch_html(client, page_url)
                marketplace = group["domains"][0] if group["domains"] else ""
                seller_name = seller_from_html(html, marketplace)

                if seller_name:
                    # Merge into marketplace_seller group
                    host = group["domains"][0]
                    new_key = f"{host}:{seller_name}"

                    # Find or create marketplace_seller group
                    seller_group = next((g for g in groups if g["key"] == new_key), None)
                    if seller_group:
                        # Merge into existing
                        seller_group["idxs"].extend(group["idxs"])
                        seller_group["listings"] += group["listings"]
                        # avg_risk is the mean over verified listings, so weight by verified count
                        v_old, v_new = seller_group["verified_listings"], group["verified_listings"]
                        if v_old + v_new:
                            total = seller_group.get("avg_risk", 0.0) * v_old + group.get("avg_risk", 0.0) * v_new
                            seller_group["avg_risk"] = round(total / (v_old + v_new), 1)
                        seller_group["verified_listings"] += group["verified_listings"]
                        # Widen the seen-window; empty/None dates are ignored
                        firsts = [d for d in (seller_group.get("first_seen"), group.get("first_seen")) if d]
                        lasts = [d for d in (seller_group.get("last_seen"), group.get("last_seen")) if d]
                        seller_group["first_seen"] = min(firsts) if firsts else ""
                        seller_group["last_seen"] = max(lasts) if lasts else ""
                        seller_group["max_risk"] = max(seller_group["max_risk"], group["max_risk"])
                        for cat, count in group["categories"].items():
                            seller_group["categories"][cat] = seller_group["categories"].get(cat, 0) + count
                        seller_group["demo"] = seller_group["demo"] and group["demo"]
                        merged.append(group)
                    else:
                        # Create new marketplace_seller group from this listing
                        group["key"] = new_key
                        group["name"] = seller_name
                        group["kind"] = "marketplace_seller"

        for group in to_fetch:
            tasks.append(fetch_seller_task(group))

        # Run all tasks with timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=20.0
            )
        except asyncio.TimeoutError:
            pass
        for g in merged:  # listings folded into an existing seller group
            if g in groups:
                groups.remove(g)

    finally:
        if own_client:
            await client.aclose()


async def sellers_for_scan(scan_id: str, enrich: bool = True) -> list[dict]:
    """Main entry point: read matches, group, optionally enrich, score, and cache result."""
    if scan_id not in SESSIONS:
        raise KeyError(f"Unknown scan: {scan_id}")

    sess = SESSIONS[scan_id]

    # Collect all matches
    matches = []
    matches_by_idx = {}
    for item in sess["items"].values():
        match = item.get("match", {})
        matches.append(match)
        idx = match.get("idx")
        if idx is not None:
            matches_by_idx[idx] = match

    # Group by seller
    groups = group_matches(matches)

    # Enrich if requested
    if enrich:
        await enrich_sellers(groups, matches_by_idx)

    # Score and flag
    for group in groups:
        score_and_flag(group)

    # Sort by score descending
    groups.sort(key=lambda g: g["seller_score"], reverse=True)

    # Cache in session
    sess["sellers"] = groups

    return groups
