"""Keep the suite offline: no-key sources off, API keys removed (app.main loads .env on import).

Tests that exercise a source turn it back on with monkeypatch.setenv and a mock transport.
"""
import pytest

KEYS = ("SERPAPI_KEY", "BRAVE_API_KEY", "YDC_API_KEY", "GEMINI_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET",
        "GOOGLE_SAFE_BROWSING_KEY", "IA_ACCESS_KEY", "IA_SECRET_KEY", "PUBLIC_BASE_URL")


@pytest.fixture(autouse=True)
def offline_sources(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BG_ALIEXPRESS", "0")
    monkeypatch.setenv("BG_PHISHSTATS", "0")
    monkeypatch.setenv("BG_IP_LOOKUP", "0")  # no DNS or RDAP lookups for listing servers


@pytest.fixture(autouse=True)
def investigation_log(tmp_path, monkeypatch):
    """Each test writes its own investigation log, never data/investigations.db."""
    from app import investigations
    monkeypatch.setattr(investigations, "DB", tmp_path / "investigations.db")
