"""Tests for seller intelligence module."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import sellers
from app.sellers_api import router
from app.store import SESSIONS


class TestSellerFromHtml:
    """Test seller_from_html function."""

    def test_json_ld_offers_seller_name(self):
        html = b"""
        <script type="application/ld+json">
        {
            "offers": {
                "seller": {
                    "name": "Best Seller Store"
                }
            }
        }
        </script>
        """
        result = sellers.seller_from_html(html, "aliexpress")
        assert result == "Best Seller Store"

    def test_json_ld_offers_array_seller(self):
        html = b"""
        <script type="application/ld+json">
        {
            "offers": [
                {"seller": {"name": "Store A"}},
                {"seller": {"name": "Store B"}}
            ]
        }
        </script>
        """
        result = sellers.seller_from_html(html, "aliexpress")
        assert result in ["Store A", "Store B"]

    def test_og_site_name_fallback(self):
        html = b"""
        <meta property="og:site_name" content="My Awesome Shop">
        <title>Product</title>
        """
        result = sellers.seller_from_html(html, "aliexpress")
        # Should return og:site_name when it differs from marketplace
        assert result == "My Awesome Shop"


class TestRegistrableDomain:
    """Test registrable_domain function."""

    def test_simple_domain(self):
        assert sellers.registrable_domain("example.com") == "example.com"

    def test_with_subdomain(self):
        assert sellers.registrable_domain("shop.example.com") == "example.com"

    def test_multi_level_subdomain(self):
        assert sellers.registrable_domain("shop.store.example.com") == "example.com"

    def test_multi_part_tld_co_uk(self):
        assert sellers.registrable_domain("shop.example.co.uk") == "example.co.uk"

    def test_multi_part_tld_com_au(self):
        assert sellers.registrable_domain("shop.example.com.au") == "example.com.au"

    def test_multi_part_tld_multiple_subdomains(self):
        assert sellers.registrable_domain("www.shop.example.co.uk") == "example.co.uk"

    def test_www_removal(self):
        assert sellers.registrable_domain("www.example.com") == "example.com"

    def test_empty_string(self):
        assert sellers.registrable_domain("") == ""

    def test_uppercase(self):
        assert sellers.registrable_domain("EXAMPLE.COM") == "example.com"


class TestSellerIdentity:
    """Test seller_identity function."""

    def test_website_identity(self):
        match = {"page_url": "https://www.example.com/product"}
        result = sellers.seller_identity(match)
        assert result["kind"] == "website"
        assert result["domain"] == "example.com"

    def test_aliexpress_with_store_id(self):
        match = {"page_url": "https://www.aliexpress.com/store/123456/product"}
        result = sellers.seller_identity(match)
        assert result["kind"] == "marketplace_seller"
        assert "123456" in result["name"]

    def test_dhgate_with_store_id(self):
        match = {"page_url": "https://www.dhgate.com/store/987654/product"}
        result = sellers.seller_identity(match)
        assert result["kind"] == "marketplace_seller"
        assert "987654" in result["name"]

    def test_ebay_usr_seller(self):
        match = {"page_url": "https://www.ebay.com/usr/awesomeseller"}
        result = sellers.seller_identity(match)
        assert result["kind"] == "marketplace_seller"
        assert "awesomeseller" in result["name"]

    def test_ebay_str_seller(self):
        match = {"page_url": "https://www.ebay.com/str/BestStoreEver"}
        result = sellers.seller_identity(match)
        assert result["kind"] == "marketplace_seller"
        assert "BestStoreEver" in result["name"]

    def test_etsy_shop(self):
        match = {"page_url": "https://www.etsy.com/shop/MyAwesomeShop"}
        result = sellers.seller_identity(match)
        assert result["kind"] == "marketplace_seller"
        assert "MyAwesomeShop" in result["name"]

    def test_amazon_seller_param(self):
        match = {"page_url": "https://www.amazon.com/dp/ABC123?seller=SELLER123"}
        result = sellers.seller_identity(match)
        assert result["kind"] == "marketplace_seller"
        assert "SELLER123" in result["name"]

    def test_amazon_me_param(self):
        match = {"page_url": "https://www.amazon.com/stores/MYSTORE"}
        result = sellers.seller_identity(match)
        assert result["kind"] == "marketplace_seller"

    def test_marketplace_without_seller(self):
        match = {"page_url": "https://www.aliexpress.com/item/ABC123"}
        result = sellers.seller_identity(match)
        assert result["kind"] == "marketplace_listing"
        assert "unknown seller" in result["name"]

    def test_social_platform(self):
        match = {"page_url": "https://www.instagram.com/someaccount"}
        result = sellers.seller_identity(match)
        assert result["kind"] == "social"


class TestGroupMatches:
    """Test group_matches function."""

    def test_group_by_website(self):
        matches = [
            {
                "idx": 0,
                "page_url": "https://shop1.example.com/product1",
                "risk": {"category": "unauthorized_use", "score": 50},
                "verification": "verified",
                "captured_at": "2026-10-09T10:00:00+00:00",
            },
            {
                "idx": 1,
                "page_url": "https://www.example.com/product2",
                "risk": {"category": "unauthorized_use", "score": 45},
                "verification": "verified",
                "captured_at": "2026-10-09T11:00:00+00:00",
            },
        ]
        groups = sellers.group_matches(matches)
        assert len(groups) == 1
        assert groups[0]["listings"] == 2
        assert groups[0]["verified_listings"] == 2

    def test_group_by_marketplace_seller(self):
        matches = [
            {
                "idx": 0,
                "page_url": "https://www.aliexpress.com/store/123/product1",
                "risk": {"category": "marketplace", "score": 55},
                "verification": "verified",
                "captured_at": "2026-10-09T10:00:00+00:00",
            },
            {
                "idx": 1,
                "page_url": "https://www.aliexpress.com/store/456/product2",
                "risk": {"category": "marketplace", "score": 60},
                "verification": "verified",
                "captured_at": "2026-10-09T11:00:00+00:00",
            },
        ]
        groups = sellers.group_matches(matches)
        assert len(groups) == 2  # Two different sellers

    def test_exclude_official_matches(self):
        matches = [
            {
                "idx": 0,
                "page_url": "https://example.com/product1",
                "risk": {"category": "official", "score": 0},
                "verification": "verified",
                "captured_at": "2026-10-09T10:00:00+00:00",
            },
            {
                "idx": 1,
                "page_url": "https://fake.example.com/product2",
                "risk": {"category": "unauthorized_use", "score": 60},
                "verification": "verified",
                "captured_at": "2026-10-09T11:00:00+00:00",
            },
        ]
        groups = sellers.group_matches(matches)
        assert len(groups) == 1
        assert groups[0]["max_risk"] == 60

    def test_count_verified_listings(self):
        matches = [
            {
                "idx": 0,
                "page_url": "https://example.com/product1",
                "risk": {"category": "unauthorized_use", "score": 50},
                "verification": "verified",
                "captured_at": "2026-10-09T10:00:00+00:00",
            },
            {
                "idx": 1,
                "page_url": "https://example.com/product2",
                "risk": {"category": "unauthorized_use", "score": 45},
                "verification": "thumbnail-only (original blocked download; low-res comparison)",
                "captured_at": "2026-10-09T11:00:00+00:00",
            },
            {
                "idx": 2,
                "page_url": "https://example.com/product3",
                "risk": {"category": "unauthorized_use", "score": 40},
                "verification": "unverified",
                "captured_at": "2026-10-09T12:00:00+00:00",
            },
        ]
        groups = sellers.group_matches(matches)
        assert len(groups) == 1
        assert groups[0]["listings"] == 3
        assert groups[0]["verified_listings"] == 2


class TestScoreAndFlag:
    """Test score_and_flag function."""

    def test_repeat_use_bonus(self):
        group = {
            "max_risk": 50,
            "verified_listings": 3,
            "listings": 3,
            "domains": ["example.com"],
            "kind": "website",
            "flags": [],
        }
        sellers.score_and_flag(group)
        assert group["seller_score"] == 50 + 6  # 50 + min(15, 3*(3-1))
        assert any("3 listings" in flag for flag in group["flags"])

    def test_domain_age_under_90_days(self):
        group = {
            "max_risk": 30,
            "verified_listings": 1,
            "listings": 1,
            "domains": ["newsite.com"],
            "kind": "website",
            "domain_created": "2026-09-30T00:00:00Z",
            "domain_age_days": 9,
            "flags": [],
        }
        sellers.score_and_flag(group)
        assert group["seller_score"] == 30 + 15  # Added 15 for age < 90 days
        assert any("9 days" in flag for flag in group["flags"])

    def test_cheap_tld_penalty(self):
        group = {
            "max_risk": 50,
            "verified_listings": 1,
            "listings": 1,
            "domains": ["example.xyz"],
            "kind": "website",
            "flags": [],
        }
        sellers.score_and_flag(group)
        assert group["seller_score"] == 50 + 5  # Added 5 for cheap TLD
        assert any("Low-trust TLD" in flag for flag in group["flags"])

    def test_marketplace_seller_bonus(self):
        group = {
            "max_risk": 50,
            "verified_listings": 2,
            "listings": 2,
            "domains": ["aliexpress.com"],
            "kind": "marketplace_seller",
            "flags": [],
        }
        sellers.score_and_flag(group)
        # Base 50 + 3 for repeat (min(15, 3*(2-1))) + 5 for marketplace seller
        assert group["seller_score"] == 50 + 3 + 5

    def test_unverified_warning(self):
        group = {
            "max_risk": 50,
            "verified_listings": 0,
            "listings": 3,
            "domains": ["example.com"],
            "kind": "website",
            "flags": [],
        }
        sellers.score_and_flag(group)
        assert any("unverified" in flag.lower() for flag in group["flags"])

    def test_score_clamped_to_100(self):
        group = {
            "max_risk": 90,
            "verified_listings": 10,
            "listings": 10,
            "domains": ["newsite.xyz"],
            "kind": "website",
            "domain_age_days": 5,
            "flags": [],
        }
        sellers.score_and_flag(group)
        assert group["seller_score"] == 100


class TestEnrichSellers:
    """Test enrich_sellers function."""

    def test_enrich_website_domain(self):
        groups = [
            {
                "key": "example.com",
                "kind": "website",
                "domains": ["example.com"],
                "listings": 1,
                "verified_listings": 1,
                "max_risk": 50,
                "demo": False,
            }
        ]
        matches_by_idx = {}

        # Mock rdap.lookup
        with patch("app.sellers.rdap.lookup") as mock_lookup:
            mock_lookup.return_value = {
                "created": "2020-01-15T00:00:00Z",
                "age_days": 2200,
                "registrar": "Example Registrar",
            }

            asyncio.run(sellers.enrich_sellers(groups, matches_by_idx))

            assert groups[0]["domain_created"] == "2020-01-15T00:00:00Z"
            assert groups[0]["domain_age_days"] == 2200
            assert groups[0]["registrar"] == "Example Registrar"


class TestSellersForScan:
    """Test sellers_for_scan function."""

    def test_sellers_for_scan_basic(self):
        scan_id = "test123"
        SESSIONS[scan_id] = {
            "brand": "TestBrand",
            "items": {
                0: {
                    "match": {
                        "idx": 0,
                        "page_url": "https://example.com/product1",
                        "risk": {"category": "unauthorized_use", "score": 50},
                        "verification": "verified",
                        "captured_at": "2026-10-09T10:00:00+00:00",
                    }
                },
                1: {
                    "match": {
                        "idx": 1,
                        "page_url": "https://example.com/product2",
                        "risk": {"category": "unauthorized_use", "score": 45},
                        "verification": "verified",
                        "captured_at": "2026-10-09T11:00:00+00:00",
                    }
                },
            },
        }

        with patch("app.sellers.rdap.lookup") as mock_lookup:
            mock_lookup.return_value = {
                "created": "2020-01-15T00:00:00Z",
                "age_days": 2200,
                "registrar": "Example Registrar",
            }

            result = asyncio.run(sellers.sellers_for_scan(scan_id, enrich=False))

            assert len(result) == 1
            assert result[0]["listings"] == 2
            assert result[0]["verified_listings"] == 2
            assert "sellers" in SESSIONS[scan_id]

    def test_sellers_for_scan_unknown(self):
        with pytest.raises(KeyError):
            asyncio.run(sellers.sellers_for_scan("nonexistent"))


class TestSellersAPI:
    """Test the API endpoint."""

    def test_get_sellers_endpoint(self):
        scan_id = "api_test"
        SESSIONS[scan_id] = {
            "brand": "TestBrand",
            "items": {
                0: {
                    "match": {
                        "idx": 0,
                        "page_url": "https://example.com/product1",
                        "risk": {"category": "unauthorized_use", "score": 50},
                        "verification": "verified",
                        "captured_at": "2026-10-09T10:00:00+00:00",
                    }
                },
            },
        }

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("app.sellers.rdap.lookup") as mock_lookup:
            mock_lookup.return_value = {
                "created": None,
                "age_days": None,
                "registrar": None,
            }
            response = client.get(f"/api/sellers/{scan_id}?enrich=0")

        assert response.status_code == 200
        data = response.json()
        assert data["scan_id"] == scan_id
        assert "sellers" in data
        assert len(data["sellers"]) == 1

    def test_get_sellers_unknown_scan(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/sellers/nonexistent?enrich=0")
        assert response.status_code == 404


class TestDemoData:
    """Test with real demo.json data."""

    def test_demo_json_groups_without_crash(self):
        data_file = Path(__file__).parent.parent / "data" / "demo.json"
        if not data_file.exists():
            pytest.skip("demo.json not found")

        data = json.loads(data_file.read_text(encoding="utf8"))
        matches = []
        for ev, payload in data.get("events", []):
            if ev == "match":
                matches.append(payload)

        # Should not crash
        groups = sellers.group_matches(matches)

        # Should have groups
        assert len(groups) > 0

        # Score and flag each group
        for group in groups:
            sellers.score_and_flag(group)
            # All groups should have a score
            assert "seller_score" in group
            assert 0 <= group["seller_score"] <= 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
