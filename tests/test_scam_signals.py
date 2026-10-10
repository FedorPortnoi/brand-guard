"""Scam-style wording raises risk and marks listings as possible scams; classifieds count as marketplaces."""
from app import risk, takedown

F = {"kind": "exact", "label": "Exact / re-compressed copy", "changed_pct": 0, "c2pa": {"state": "not_present"},
     "ai_signal": {"level": "none", "hints": []}}


def test_scam_wording_on_a_job_listing():
    r = risk.assess("https://jobs-acme-hiring.example.com/apply", "Acme is hiring! Work from home, no experience needed",
                    ["acme.com"], F)
    assert r["category"] == "possible_scam"
    assert r["score"] == 25 + 25
    assert any(x.startswith("Scam-style wording: work from home, no experience needed") for x in r["reasons"])


def test_classified_site_counts_as_marketplace():
    r = risk.assess("https://sfbay.craigslist.org/apa/123.html", "Acme branded apartment, deposit required", [], F)
    assert any("marketplace" in x.lower() for x in r["reasons"])
    assert r["category"] == "possible_scam" and r["score"] == 25 + 20 + 25


def test_official_domain_is_never_a_scam():
    r = risk.assess("https://careers.acme.com/jobs", "Work from home at Acme", ["acme.com"], F)
    assert r == {"category": "official", "score": 0, "reasons": ["Domain is on the brand's official list"], "host": "careers.acme.com"}


def test_ordinary_listing_is_not_flagged():
    r = risk.assess("https://shop.example.com/p/1", "Acme water bottle 750 ml", [], F)
    assert r["category"] != "possible_scam" and not any("Scam" in x for x in r["reasons"])


def test_unverified_scam_page_is_kept():
    r = risk.context_only("https://acme-prize.example.net/win", "You have won! Claim your prize", [])
    assert r and r["category"] == "unverified" and any("Scam-style wording" in x for x in r["reasons"])


def test_scam_listing_gets_trademark_notice():
    assert takedown.notice_type([{"forensics": None, "risk": {"category": "possible_scam"}}]) == "trademark"
