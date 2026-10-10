"""Tests for forensics calibration and labelling."""
import io
import json
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import forensics, labels_api
from app.store import SESSIONS


def gen_image(color: str = "red") -> bytes:
    """Generate a test image."""
    im = Image.new("RGB", (200, 200), color)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_logo(text: str) -> bytes:
    """Generate a simple logo."""
    im = Image.new("RGB", (300, 150), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([50, 30, 250, 120], fill="blue", outline="darkblue", width=2)
    d.text((80, 60), text, fill="white")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


class TestFeaturesFunction:
    """Test the features() function."""

    def test_self_match_exact(self):
        """Self-match should give low hash distance and high features."""
        data = gen_logo("BRAND")
        ref = forensics.Reference(data)
        feats = forensics.features(ref, data)

        assert feats["hash_dist"] == 0
        assert feats["inliers"] > 0
        assert feats["ratio"] == 1.0
        assert feats["tmpl"] > 0.9

    def test_recompression_exact(self):
        """Recompressed image should be exact or modified."""
        orig = gen_logo("TEST")
        ref = forensics.Reference(orig)

        # Recompress at different quality
        img = Image.open(io.BytesIO(orig))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=75)
        data = buf.getvalue()

        feats = forensics.features(ref, data)
        # Small changes, mostly same
        assert feats["hash_dist"] < 20
        # changed is computed lazily, so check via compare()
        result = forensics.compare(ref, data)
        assert result is not None
        assert result["changed_pct"] < 5


class TestCompareFunction:
    """Test the main compare() function."""

    def test_self_match_returns_exact(self):
        """Self-match should return kind='exact'."""
        data = gen_logo("ACME")
        ref = forensics.Reference(data)
        result = forensics.compare(ref, data)

        assert result is not None
        assert result["kind"] == "exact"
        assert result["hash_distance"] == 0

    def test_unrelated_returns_none(self):
        """Significantly different images should not match."""
        ref = forensics.Reference(gen_logo("ACME"))

        # Create a completely different logo
        diff = Image.new("RGB", (300, 150), "white")
        d = ImageDraw.Draw(diff)
        d.ellipse([50, 30, 250, 120], fill="red", outline="darkred", width=2)
        d.text((100, 60), "OTHER", fill="white")
        buf = io.BytesIO()
        diff.save(buf, "JPEG", quality=90)
        data = buf.getvalue()

        result = forensics.compare(ref, data)
        # With new tight thresholds, different logos should not match
        if result:
            assert result["hash_distance"] > 80 or result["inlier_ratio"] < 0.2

    def test_result_keys_unchanged(self):
        """Return dict must have expected keys."""
        data = gen_logo("TEST")
        ref = forensics.Reference(data)
        result = forensics.compare(ref, data)

        expected_keys = {
            "kind", "label", "hash_distance", "feature_inliers",
            "inlier_ratio", "changed_pct", "heatmap", "template_score",
            "c2pa", "ai_signal", "size"
        }
        assert set(result.keys()) == expected_keys

    def test_logo_template_match(self):
        """Simple logos should use template matching."""
        # Create a simple geometric logo
        logo = Image.new("RGB", (200, 100), "white")
        d = ImageDraw.Draw(logo)
        d.rectangle([30, 20, 170, 80], fill="blue", outline="darkblue", width=2)
        buf = io.BytesIO()
        logo.save(buf, "JPEG", quality=90)
        logo_bytes = buf.getvalue()

        ref = forensics.Reference(logo_bytes)

        # Test that features include template score
        feats = forensics.features(ref, logo_bytes)
        assert feats["tmpl"] > 0.8  # Self-match should have high template score


class TestLabelsAPI:
    """Test the labels API."""

    @pytest.fixture
    def client(self, tmp_path):
        """Create a test client with temporary labels directory."""
        app = FastAPI()
        app.include_router(labels_api.router)

        # Override labels dir to temp location
        labels_api.LABELS_DIR = tmp_path / "labels"
        labels_api.LABELS_DIR.mkdir(exist_ok=True)
        labels_api.REAL_LABELS_FILE = labels_api.LABELS_DIR / "real.jsonl"

        return TestClient(app)

    def test_post_label_not_found_scan(self, client):
        """POST to unknown scan should return 404."""
        response = client.post("/api/labels", json={
            "scan_id": "unknown",
            "idx": 0,
            "label": "confirmed"
        })
        assert response.status_code == 404

    def test_post_label_not_found_item(self, client):
        """POST to missing item should return 404."""
        # Create a session
        SESSIONS["test123"] = {"items": {}, "original": b"test"}

        response = client.post("/api/labels", json={
            "scan_id": "test123",
            "idx": 999,
            "label": "confirmed"
        })
        assert response.status_code == 404

    def test_post_label_success(self, client, tmp_path):
        """Successfully posting a label should save it."""
        labels_api.LABELS_DIR = tmp_path / "labels"
        labels_api.LABELS_DIR.mkdir(exist_ok=True)
        labels_api.REAL_LABELS_FILE = labels_api.LABELS_DIR / "real.jsonl"

        # Create a session with an item
        match = {  # the shape pipeline.py produces: forensic numbers live under "forensics"
            "page_url": "https://example.com/p",
            "image_url": "https://example.com/img.jpg",
            "sha256": "ab" * 32,
            "forensics": {"kind": "modified", "hash_distance": 20, "feature_inliers": 10, "inlier_ratio": 0.5,
                          "template_score": 0.85, "changed_pct": 10.5, "heatmap": "data:image/jpeg;base64,xx"},
            "risk": {"host": "example.com"}
        }
        SESSIONS["scan123"] = {
            "items": {0: {"match": match, "evidence": None}},
            "original": b"test"
        }

        response = client.post("/api/labels", json={
            "scan_id": "scan123",
            "idx": 0,
            "label": "confirmed",
            "note": "This is a real copy"
        })
        assert response.status_code == 200

        # Check the label was saved
        labels = labels_api.REAL_LABELS_FILE.read_text(encoding="utf-8")
        assert len(labels) > 0
        record = json.loads(labels.strip().split("\n")[0])
        assert record["label"] == "confirmed"
        assert record["scan_id"] == "scan123"
        assert record["page_url"] == "https://example.com/p"
        assert (record["kind"], record["hash_distance"], record["feature_inliers"]) == ("modified", 20, 10)
        assert "heatmap" not in labels

    def test_post_label_rejects_unknown_label(self, client):
        SESSIONS["scan124"] = {"items": {0: {"match": {"risk": {}}, "evidence": None}}, "original": b""}
        assert client.post("/api/labels", json={"scan_id": "scan124", "idx": 0, "label": "maybe"}).status_code == 400

    def test_get_stats_empty(self, client):
        """Stats with no labels should return zeros."""
        response = client.get("/api/labels/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["confirmed"] == 0
        assert data["total"] == 0

    def test_get_stats_with_labels(self, client, tmp_path):
        """Stats should count labels correctly."""
        labels_api.LABELS_DIR = tmp_path / "labels"
        labels_api.LABELS_DIR.mkdir(exist_ok=True)
        labels_api.REAL_LABELS_FILE = labels_api.LABELS_DIR / "real.jsonl"

        # Create some labels
        labels = [
            {"label": "confirmed"},
            {"label": "confirmed"},
            {"label": "false_positive"},
            {"label": "not_sure"},
        ]
        lines = "\n".join(json.dumps(l) for l in labels) + "\n"
        labels_api.REAL_LABELS_FILE.write_text(lines, encoding="utf-8")

        response = client.get("/api/labels/stats")
        data = response.json()
        assert data["confirmed"] == 2
        assert data["false_positive"] == 1
        assert data["not_sure"] == 1
        assert data["total"] == 4


class TestCalibrateIntegration:
    """Integration tests for calibration."""

    def test_features_consistent_with_compare(self):
        """features() output should be consistent with compare()."""
        data = gen_logo("TEST")
        ref = forensics.Reference(data)

        feats = forensics.features(ref, data)
        result = forensics.compare(ref, data)

        # The features should match what's in the result
        assert result["hash_distance"] == feats["hash_dist"]
        assert result["feature_inliers"] == feats["inliers"]
        assert result["inlier_ratio"] == round(feats["ratio"], 2)
        # changed is lazily computed in compare(), so feats["changed"] is None
        assert feats["changed"] is None
        assert result["template_score"] == round(feats["tmpl"], 2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
