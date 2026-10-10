"""Context-based risk signals. Output is *signals*, never a legal verdict."""
from urllib.parse import urlparse

MARKETPLACES = ("aliexpress", "alibaba", "dhgate", "temu", "wish.com", "shein", "ebay", "etsy", "amazon",
                "taobao", "1688", "made-in-china", "banggood", "joom", "mercari", "olx", "avito", "ozon", "wildberries",
                # classifieds: where fake job, rental, prize and "opportunity" listings often run
                "craigslist", "gumtree", "kijiji", "offerup", "marktplaats", "leboncoin", "kleinanzeigen")
SUSPICIOUS_WORDS = ("replica", "fake", "1:1", "copy", "knockoff", "clone", "cheap", "outlet", "wholesale",
                    "discount", "% off", "factory", "mirror", "aaa", "super copy", "unbranded")
SOCIAL = ("pinterest", "facebook", "instagram", "tiktok", "reddit", "twitter", "x.com", "youtube", "tumblr", "vk.com")
# Wording typical of impersonation scams (fake jobs, giveaways, investments, rentals). Signals, not proof.
SCAM_WORDS = ("guaranteed return", "guaranteed income", "guaranteed profit", "risk-free investment", "double your money",
              "investment opportunity", "passive income", "work from home", "no experience needed", "upfront fee",
              "registration fee", "processing fee", "deposit required", "wire transfer", "western union", "moneygram",
              "pay with gift card", "gift card payment", "pay in crypto", "bitcoin only", "usdt only", "whatsapp me",
              "message me on telegram", "claim your prize", "you have won", "you've won", "free giveaway", "airdrop",
              "verify your account", "limited spots")
CHEAP_TLDS = (".xyz", ".top", ".shop", ".click", ".store", ".online", ".site", ".buzz", ".icu", ".cn")


def parse_domains(text: str) -> list[str]:
    """'Shop.Acme.com, www.acme.co.uk' -> ['shop.acme.com', 'acme.co.uk']."""
    return [d.strip().lower().removeprefix("www.") for d in (text or "").split(",") if d.strip()]


def on_domains(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains or () if d)


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def assess(page_url: str, title: str, official_domains: list[str], forensic: dict, original_c2pa: str = "unknown",
           resellers: list[str] = ()) -> dict:
    host = host_of(page_url)
    text = f"{title} {page_url}".lower()
    reasons: list[str] = []

    if on_domains(host, official_domains):
        return {"category": "official", "score": 0, "reasons": ["Domain is on the brand's official list"], "host": host}
    if on_domains(host, resellers):
        return {"category": "authorised_reseller", "score": 5,
                "reasons": ["Domain is on your authorised resellers list", forensic["label"]], "host": host}

    score = {"exact": 25, "modified": 30, "cropped": 35, "altered": 55, "lookalike": 50, "same_product": 40}.get(forensic["kind"], 20)
    reasons.append(forensic["label"])

    category = "unknown"
    if any(m in host for m in MARKETPLACES):
        category = "marketplace"; score += 20; reasons.append("Third-party marketplace listing")
    elif any(s in host for s in SOCIAL):
        category = "social"; score -= 10; reasons.append("Social/UGC platform (often fan or repost)")
    hits = [w for w in SUSPICIOUS_WORDS if w in text]
    if hits:
        category = "likely_counterfeit" if category != "social" else category
        score += 25; reasons.append("Suspicious wording: " + ", ".join(hits[:3]))
    scam = [w for w in SCAM_WORDS if w in text]
    if scam:
        category = "possible_scam"
        score += 25; reasons.append("Scam-style wording: " + ", ".join(scam[:3]) + " (common in fake job, giveaway and investment listings)")
    if host.endswith(CHEAP_TLDS):
        score += 10; reasons.append(f"Low-trust TLD ({host.rsplit('.', 1)[-1]})")
    if forensic["kind"] in ("altered", "lookalike"):
        score += 10
    if forensic["kind"] == "same_product":
        reasons.append(f"Semantic match: looks like the same product photographed differently (similarity {forensic.get('similarity')})")
    if forensic["changed_pct"] >= 8:
        reasons.append(f"{forensic['changed_pct']}% of pixels differ from your original")
    c2 = forensic["c2pa"]["state"]
    if c2 in ("valid", "trusted"):
        score -= 10; reasons.append(f"Content Credentials present and cryptographically {c2} (signer: {forensic['c2pa'].get('issuer') or 'unknown'})")
    elif c2 == "invalid":
        score += 15; reasons.append("Content Credentials present but FAIL validation (tampered or broken manifest)")
    elif original_c2pa in ("valid", "trusted") and c2 == "not_present":
        reasons.append("Your original carries valid Content Credentials; this copy has none (may have been stripped, or lost by resizing/CDN processing)")
    ai = forensic["ai_signal"]
    if ai["level"] != "none":
        score += 10 if ai["level"] == "declared" else 3
        reasons.append("Possible synthetic/edited-image hints: " + "; ".join(ai["hints"]) + " (heuristic, not proof)")

    score = max(0, min(100, score))
    if category == "unknown":
        category = "unauthorized_use" if score >= 40 else "low_concern"
    return {"category": category, "score": score, "reasons": reasons, "host": host}


def context_only(page_url: str, title: str, official_domains: list[str], resellers: list[str] = ()) -> dict | None:
    """Risk from page context alone, for candidates whose image could not be downloaded and verified."""
    host = host_of(page_url)
    if on_domains(host, official_domains) or on_domains(host, resellers):
        return None
    text = f"{title} {page_url}".lower()
    reasons, score = [], 0
    if any(m in host for m in MARKETPLACES):
        score += 25; reasons.append("Third-party marketplace listing")
    hits = [w for w in SUSPICIOUS_WORDS if w in text]
    if hits:
        score += 30; reasons.append("Suspicious wording: " + ", ".join(hits[:3]))
    scam = [w for w in SCAM_WORDS if w in text]
    if scam:
        score += 30; reasons.append("Scam-style wording: " + ", ".join(scam[:3]))
    if host.endswith(CHEAP_TLDS):
        score += 10; reasons.append("Low-trust TLD")
    if score < 30:
        return None
    reasons.append("Image could not be downloaded, so similarity to your original is UNVERIFIED")
    return {"category": "unverified", "score": min(score, 60), "reasons": reasons, "host": host}
