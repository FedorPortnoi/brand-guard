"""Tests for takedown draft generation."""
import asyncio
import base64
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import sellers, takedown, takedown_api
from app.store import SESSIONS


# Test fixtures

@pytest.fixture
def test_app():
    """Create a minimal FastAPI app with the takedown router."""
    app = FastAPI()
    app.include_router(takedown_api.router)
    return app


@pytest.fixture
def client(test_app):
    """Create a test client."""
    return TestClient(test_app)


@pytest.fixture
def test_image_bytes():
    """Create a test JPEG image."""
    img = Image.new("RGB", (100, 100), color="red")
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def test_session(test_image_bytes):
    """Create a test session in SESSIONS."""
    scan_id = "test_scan_001"
    original = test_image_bytes

    # Create 3 test matches: 2 on AliExpress, 1 on a website
    matches = [
        # Match 0: AliExpress, unverified
        {
            "idx": 0,
            "image_url": "https://img.aliexpress.com/product/image.jpg",
            "page_url": "https://www.aliexpress.com/item/123456.html",
            "title": "Fake Logo T-shirt",
            "forensics": None,
            "risk": {
                "category": "unverified",
                "score": 50,
                "reasons": ["Marketplace listing", "Suspicious wording"],
                "host": "aliexpress.com",
            },
            "verification": "unverified",
            "captured_at": "2026-10-10T10:00:00+00:00",
            "sha256": "abc123def456",
        },
        # Match 1: AliExpress, verified copyright
        {
            "idx": 1,
            "image_url": "https://img.aliexpress.com/product/image2.jpg",
            "page_url": "https://www.aliexpress.com/item/654321.html",
            "title": "Logo Replica - Cheap",
            "forensics": {
                "kind": "modified",
                "label": "Modified / slightly edited copy",
                "hash_distance": 45,
                "feature_inliers": 120,
                "inlier_ratio": 0.85,
                "changed_pct": 5.0,
                "c2pa": {"state": "not_present"},
                "ai_signal": {"level": "none", "hints": []},
            },
            "risk": {
                "category": "marketplace",
                "score": 65,
                "reasons": ["Modified copy", "Marketplace listing"],
                "host": "aliexpress.com",
            },
            "verification": "verified",
            "captured_at": "2026-10-10T10:01:00+00:00",
            "sha256": "xyz789uvw012",
        },
        # Match 2: Non-platform website, verified lookalike
        {
            "idx": 2,
            "image_url": "https://example-shop.com/product-image.jpg",
            "page_url": "https://example-shop.com/products/fake-item",
            "title": "Similar Product",
            "forensics": {
                "kind": "lookalike",
                "label": "Lookalike or similar-looking design",
                "hash_distance": 180,
                "feature_inliers": 25,
                "inlier_ratio": 0.35,
                "changed_pct": 40.0,
                "c2pa": {"state": "not_present"},
                "ai_signal": {"level": "none", "hints": []},
            },
            "risk": {
                "category": "likely_counterfeit",
                "score": 72,
                "reasons": ["Lookalike design", "Suspicious wording"],
                "host": "example-shop.com",
            },
            "verification": "verified",
            "captured_at": "2026-10-10T10:02:00+00:00",
            "sha256": "qwerty1234567",
        },
    ]

    # Create session
    SESSIONS[scan_id] = {
        "brand": "TestBrand",
        "original": original,
        "official_domains": ["official.com"],
        "items": {
            0: {
                "match": matches[0],
                "evidence": base64.b64encode(test_image_bytes).decode(),
            },
            1: {
                "match": matches[1],
                "evidence": base64.b64encode(test_image_bytes).decode(),
            },
            2: {
                "match": matches[2],
                "evidence": base64.b64encode(test_image_bytes).decode(),
            },
        },
    }

    yield scan_id

    # Cleanup
    del SESSIONS[scan_id]


# Tests

def test_channels_structure():
    """Verify CHANNELS table has required fields."""
    assert "aliexpress" in takedown.CHANNELS
    assert "amazon" in takedown.CHANNELS
    assert "ebay" in takedown.CHANNELS
    assert "pinterest" in takedown.CHANNELS

    for key, channel in takedown.CHANNELS.items():
        assert "name" in channel
        assert "report_url" in channel
        assert "how_to" in channel
        assert "verified" in channel
        assert "source" in channel
        assert isinstance(channel["verified"], bool)


def test_channels_verified():
    """Test that some channels are verified."""
    verified = [k for k, v in takedown.CHANNELS.items() if v["verified"]]
    assert len(verified) > 0
    assert "youtube" in verified or "tiktok" in verified or "aliexpress" in verified


def test_registrable_domain():
    """Test registrable domain extraction."""
    assert sellers.registrable_domain("shop.example.com") == "example.com"
    assert sellers.registrable_domain("example.co.uk") == "example.co.uk"
    assert sellers.registrable_domain("shop.example.com.au") == "example.com.au"
    assert sellers.registrable_domain("example.com") == "example.com"


def test_notice_type_copyright():
    """Test notice_type for copyright (copied image)."""
    matches = [
        {
            "forensics": {
                "kind": "exact",
                "label": "Exact copy",
                "hash_distance": 0,
            },
            "risk": {"category": "unauthorized_use", "score": 40},
        }
    ]
    assert takedown.notice_type(matches) == "copyright"


def test_notice_type_trademark():
    """Test notice_type for trademark (lookalike)."""
    matches = [
        {
            "forensics": {
                "kind": "lookalike",
                "label": "Lookalike design",
            },
            "risk": {"category": "likely_counterfeit"},
        }
    ]
    assert takedown.notice_type(matches) == "trademark"


def test_notice_type_combined_group():
    """Test notice_type for mixed group → both."""
    matches = [
        {
            "forensics": {"kind": "modified", "label": "Modified copy"},
            "risk": {"category": "unauthorized_use"},
        },
        {
            "forensics": {"kind": "lookalike", "label": "Lookalike design"},
            "risk": {"category": "likely_counterfeit"},
        },
    ]
    assert takedown.notice_type(matches) == "both"


def test_notice_type_unverified():
    """Test notice_type for unverified match."""
    matches = [
        {
            "forensics": None,
            "risk": {"category": "unverified"},
        }
    ]
    assert takedown.notice_type(matches) == "trademark"


def test_build_drafts_aliexpress(test_session):
    """Test build_drafts groups AliExpress matches."""
    drafts = asyncio.run(takedown.build_drafts(
        test_session,
        [0, 1],
        {"name": "Test User", "company": "Test Co"},
        "http://localhost:8000",
    ))

    assert len(drafts) >= 1
    ali_drafts = [d for d in drafts if d["channel"] == "aliexpress"]
    assert len(ali_drafts) == 1
    assert ali_drafts[0]["idxs"] == [0, 1]


def test_build_drafts_grouping(test_session):
    """Test build_drafts groups by channel/host: 2 AliExpress + 1 website = 2 drafts."""
    drafts = asyncio.run(takedown.build_drafts(
        test_session,
        [0, 1, 2],
        {"name": "Test User"},
        "http://localhost:8000",
    ))

    assert len(drafts) == 2

    channels = [d["channel"] for d in drafts]
    assert "aliexpress" in channels
    assert "example-shop.com" in channels


def test_build_drafts_has_note_field(test_session):
    """Test that drafts have a note field."""
    drafts = asyncio.run(takedown.build_drafts(
        test_session,
        [0],
        {"name": "Test"},
        "http://localhost:8000",
    ))

    assert len(drafts) > 0
    assert "note" in drafts[0]
    assert "Draft generated by Brand Guard" in drafts[0]["note"]


def test_build_drafts_body_no_disclaimer(test_session):
    """Test body does NOT contain disclaimer footer."""
    drafts = asyncio.run(takedown.build_drafts(
        test_session,
        [0],
        {"name": "Test"},
        "http://localhost:8000",
    ))

    body = drafts[0]["body"]
    assert "Brand Guard" not in body
    assert "---" not in body


def test_build_drafts_subject_claims_language(test_session):
    """Test subject uses 'claimed' or 'suspected' language."""
    drafts = asyncio.run(takedown.build_drafts(
        test_session,
        [0],
        {"name": "Test"},
        "http://localhost:8000",
    ))

    subject = drafts[0]["subject"]
    assert "claimed" in subject.lower() or "suspected" in subject.lower()


def test_build_drafts_body_contains_urls(test_session):
    """Test body lists every URL and SHA-256."""
    drafts = asyncio.run(takedown.build_drafts(
        test_session,
        [0, 1],
        {"name": "Test"},
        "http://localhost:8000",
    ))

    body = drafts[0]["body"]
    assert "https://www.aliexpress.com/item/123456.html" in body
    assert "https://www.aliexpress.com/item/654321.html" in body
    assert "abc123def456" in body
    assert "xyz789uvw012" in body


def test_build_drafts_body_contains_confirm_placeholders(test_session):
    """Test body contains CONFIRM placeholders."""
    drafts = asyncio.run(takedown.build_drafts(
        test_session,
        [0],
        {"name": "Test"},
        "http://localhost:8000",
    ))

    body = drafts[0]["body"]
    assert "[CONFIRM:" in body
    assert "good-faith belief" in body
    assert "authorized" in body
    assert "perjury" in body


def test_build_drafts_unverified_finding_text(test_session):
    """Test unverified matches show correct finding text."""
    drafts = asyncio.run(takedown.build_drafts(
        test_session,
        [0],
        {"name": "Test"},
        "http://localhost:8000",
    ))

    body = drafts[0]["body"]
    assert "image could not be downloaded for verification" in body


def test_build_drafts_missing_fields_become_placeholders(test_session):
    """Test missing rights_holder fields become [PLACEHOLDER]."""
    drafts = asyncio.run(takedown.build_drafts(
        test_session,
        [0],
        {},  # Empty rights_holder
        "http://localhost:8000",
    ))

    body = drafts[0]["body"]
    assert "[YOUR NAME]" in body
    assert "[YOUR COMPANY]" in body
    assert "[YOUR EMAIL]" in body


def test_build_drafts_evidence_pdfs_in_draft(test_session):
    """Test evidence_pdfs list in draft."""
    drafts = asyncio.run(takedown.build_drafts(
        test_session,
        [0, 1],
        {"name": "Test"},
        "http://localhost:8000",
    ))

    draft = drafts[0]
    assert "evidence_pdfs" in draft
    assert len(draft["evidence_pdfs"]) == 2
    assert all("/api/report/" in url for url in draft["evidence_pdfs"])


def test_build_drafts_mailto_properly_encoded():
    """Test mailto uses %20 for spaces, keeps "@" literal, and is under 1800 chars."""
    mailto = takedown._build_mailto(
        "abuse@example.com",
        "Report of suspected misuse",
        "Hello there,\nplease remove this.",
    )

    assert mailto.startswith("mailto:abuse@example.com?")
    query = mailto.split("?", 1)[1]
    assert "subject=Report%20of%20suspected%20misuse" in query
    assert "body=Hello%20there%2C%0Aplease%20remove%20this." in query
    assert "+" not in query
    assert len(mailto) <= 1800

    # Long bodies fall back to the short body, still %20-encoded
    long_mailto = takedown._build_mailto("abuse@example.com", "Subject line", "word " * 1000)
    assert len(long_mailto) <= 1800
    assert "Paste%20the%20notice%20here" in long_mailto  # the page copies the full text to the clipboard
    assert "+" not in long_mailto


def test_build_drafts_trademark_with_recipient_gets_mailto(test_session):
    """Test trademark notice with a recipient gets a mailto link."""
    with patch("app.takedown.rdap.lookup", new_callable=AsyncMock) as mock_rdap:
        mock_rdap.return_value = {
            "created": "2020-01-01",
            "age_days": 2000,
            "registrar": "Example Registrar",
            "abuse_email": "abuse@example.com",
        }

        drafts = asyncio.run(takedown.build_drafts(
            test_session,
            [2],  # Non-platform website (lookalike/trademark)
            {"name": "Test"},
            "http://localhost:8000",
        ))

        assert len(drafts) > 0
        draft = drafts[0]
        assert draft["notice_type"] == "trademark"
        assert draft["recipient"] == "abuse@example.com"
        assert draft.get("mailto") is not None


def test_build_drafts_rdap_concurrent(test_session):
    """Test RDAP lookups run concurrently with asyncio.gather."""
    with patch("app.takedown.rdap.lookup", new_callable=AsyncMock) as mock_rdap:
        mock_rdap.return_value = {
            "created": "2020-01-01",
            "age_days": 2000,
            "registrar": "Example Registrar",
            "abuse_email": "abuse@example.com",
        }

        drafts = asyncio.run(takedown.build_drafts(
            test_session,
            [2],
            {"name": "Test"},
            "http://localhost:8000",
        ))

        # RDAP should be called for the registrable domain
        assert mock_rdap.called


def test_bundle_zip_structure(test_session):
    """Test bundle_zip creates valid ZIP with expected structure."""
    drafts = [
        {
            "channel": "aliexpress",
            "host": "aliexpress.com",
            "notice_type": "copyright",
            "body": "Test notice",
            "note": "Test note",
            "idxs": [0, 1],
            "evidence_pdfs": [],
        },
        {
            "channel": "example.com",
            "host": "example.com",
            "notice_type": "trademark",
            "body": "Test notice 2",
            "note": "Test note",
            "idxs": [2],
            "evidence_pdfs": [],
        },
    ]

    zip_bytes = takedown.bundle_zip(test_session, [0, 1, 2], drafts)

    assert isinstance(zip_bytes, bytes)
    assert len(zip_bytes) > 0

    # Verify ZIP is valid
    buf = io.BytesIO(zip_bytes)
    with zipfile.ZipFile(buf, "r") as zf:
        names = zf.namelist()
        assert "README.txt" in names
        assert any("notices/" in n for n in names)
        assert any("evidence/" in n for n in names)


def test_bundle_zip_notice_has_note_header(test_session):
    """Test bundle notices have the note header."""
    drafts = [
        {
            "channel": "test",
            "host": "test.com",
            "notice_type": "copyright",
            "body": "Test notice body",
            "note": "Test note header",
            "idxs": [0],
            "evidence_pdfs": [],
        }
    ]

    zip_bytes = takedown.bundle_zip(test_session, [0], drafts)

    buf = io.BytesIO(zip_bytes)
    with zipfile.ZipFile(buf, "r") as zf:
        for name in zf.namelist():
            if name.startswith("notices/"):
                content = zf.read(name).decode()
                assert "Test note header" in content
                assert "Test notice body" in content
                assert content.index("Test note header") < content.index("Test notice body")
                break


def test_bundle_zip_pdfs_are_valid(test_session):
    """Test bundle_zip PDFs start with %PDF."""
    drafts = [{"channel": "test", "host": "test.com", "body": "test", "note": "test", "idxs": [0]}]
    zip_bytes = takedown.bundle_zip(test_session, [0], drafts)

    buf = io.BytesIO(zip_bytes)
    with zipfile.ZipFile(buf, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".pdf"):
                content = zf.read(name)
                assert content.startswith(b"%PDF"), f"PDF {name} doesn't start with %PDF"


def test_api_endpoint_404_unknown_scan(client):
    """Test POST /api/takedown returns 404 for unknown scan."""
    response = client.post(
        "/api/takedown/unknown_scan",
        json={"idxs": [0], "rights_holder": {}},
    )
    assert response.status_code == 404


def test_api_endpoint_400_empty_idxs(client, test_session):
    """Test POST /api/takedown returns 400 for empty idxs."""
    response = client.post(
        f"/api/takedown/{test_session}",
        json={"idxs": [], "rights_holder": {}},
    )
    assert response.status_code == 400


def test_api_endpoint_404_unknown_idx(client, test_session):
    """Test POST /api/takedown returns 404 for unknown idx."""
    response = client.post(
        f"/api/takedown/{test_session}",
        json={"idxs": [999], "rights_holder": {}},
    )
    assert response.status_code == 404


def test_api_endpoint_success(client, test_session):
    """Test POST /api/takedown returns 200 with drafts."""
    response = client.post(
        f"/api/takedown/{test_session}",
        json={"idxs": [0, 1], "rights_holder": {"name": "Test User"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert "drafts" in data
    assert isinstance(data["drafts"], list)
    assert all("note" in d for d in data["drafts"])


def test_api_endpoint_single_draft(client, test_session):
    """Test GET /api/takedown/{scan_id}/{idx} returns draft for single match."""
    response = client.get(f"/api/takedown/{test_session}/0")
    assert response.status_code == 200
    data = response.json()
    assert "drafts" in data
    assert len(data["drafts"]) > 0


def test_api_endpoint_single_draft_404(client, test_session):
    """Test GET /api/takedown/{scan_id}/{idx} returns 404 for unknown idx."""
    response = client.get(f"/api/takedown/{test_session}/999")
    assert response.status_code == 404


def test_api_endpoint_bundle_zip_returns_zip(client, test_session):
    """Test POST /api/takedown/{scan_id}/bundle.zip returns ZIP file."""
    response = client.post(
        f"/api/takedown/{test_session}/bundle.zip",
        json={"idxs": [0, 1], "rights_holder": {}},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers.get("content-disposition", "").startswith("attachment")

    # Verify it's a valid ZIP
    buf = io.BytesIO(response.content)
    with zipfile.ZipFile(buf, "r") as zf:
        assert len(zf.namelist()) > 0


def test_api_endpoint_bundle_zip_400_empty_idxs(client, test_session):
    """Test bundle.zip returns 400 for empty idxs."""
    response = client.post(
        f"/api/takedown/{test_session}/bundle.zip",
        json={"idxs": [], "rights_holder": {}},
    )
    assert response.status_code == 400


def test_api_endpoint_bundle_zip_404_unknown_scan(client):
    """Test bundle.zip returns 404 for unknown scan."""
    response = client.post(
        "/api/takedown/unknown/bundle.zip",
        json={"idxs": [0], "rights_holder": {}},
    )
    assert response.status_code == 404


def test_registrable_domain_country_endings():
    """Two-part country endings keep the site name (sears.com.mx, not com.mx), same as the Sellers tab."""
    for host, want in (("www.sears.com.mx", "sears.com.mx"), ("shop.example.co.uk", "example.co.uk"),
                       ("a.b.example.com.tr", "example.com.tr"), ("example.com", "example.com"), ("x.y.shop", "y.shop")):
        assert sellers.registrable_domain(host) == want
