"""Step 10: PDFs (receipts, invoices, order confirmations) are read and their seller checked."""
import asyncio

import pymupdf
import pytest

from app import ask, listing


def _pdf(lines=(), links=(), image_only=False, password=None) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    if image_only:  # a scan: a picture of text, no text layer
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False)
        pix.set_rect(pix.irect, (255, 255, 255))
        page.insert_image(pymupdf.Rect(72, 72, 472, 272), pixmap=pix)
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=12)
        y += 24
    for uri in links:
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, y, 300, y + 20), "uri": uri})
        y += 24
    opts = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": password, "owner_pw": password} if password else {}
    return doc.tobytes(**opts)


RECEIPT = _pdf(["MEGA SNEAKER DROP", "Order confirmation #10432", "Paid with: Zelle",
                "Questions? Visit mega-sneaker-drop.shop/help"],
               ["https://mega-sneaker-drop.shop/orders/10432", "https://www.shopify.com/", "https://www.paypal.com/",
                "https://www.instagram.com/megasneakerdrop/"])


def test_a_pdfs_text_and_the_addresses_its_links_point_to_are_read():
    lines = ask.pdf_text(RECEIPT)
    assert "Paid with: Zelle" in lines and "https://mega-sneaker-drop.shop/orders/10432" in lines
    assert ask.is_pdf(RECEIPT) and not ask.is_pdf(b"\x89PNG\r\n")


def test_the_seller_is_the_site_named_most_not_payment_or_platform_services():
    assert ask.seller_address(ask.pdf_text(RECEIPT)) == "https://mega-sneaker-drop.shop/orders/10432"  # the site named twice
    assert ask.seller_address(["https://www.paypal.com/", "https://www.shopify.com/", "https://www.instagram.com/x/"]) is None


def test_a_scanned_pdf_is_read_like_a_screenshot(monkeypatch):
    seen = []
    monkeypatch.setattr(ask, "_read", lambda img: seen.append(img.size) or ["Order at luxe-kicks.store"])
    assert ask.pdf_text(_pdf(image_only=True)) == ["Order at luxe-kicks.store"]
    assert seen and max(seen[0]) > 1000  # rendered at twice the page size for small receipt print


def test_a_locked_or_broken_pdf_asks_for_a_screenshot():
    with pytest.raises(listing.ListingError, match="password protected"):
        ask.pdf_text(_pdf(["secret"], password="pw"))
    with pytest.raises(listing.ListingError, match="Could not read that PDF"):
        ask.pdf_text(b"%PDF-1.7 not really")


def test_a_pdf_goes_through_ask_and_says_pdf(monkeypatch):
    checked = {}

    async def check(url, brand="", seen_text="", **kw):
        checked.update(url=url, seen=seen_text)
        return {"verdict": "red", "domain": "mega-sneaker-drop.shop", "url": url}

    async def nothing(result): return {}
    monkeypatch.setattr(listing, "check_url", check)
    monkeypatch.setattr(ask, "extras", nothing)
    r = asyncio.run(ask.ask("", RECEIPT))
    assert checked["url"] == "https://mega-sneaker-drop.shop/orders/10432" and "Zelle" in checked["seen"]
    assert r["found"]["how"] == "pdf"
    with pytest.raises(listing.ListingError, match="in that PDF"):
        asyncio.run(ask.ask("", _pdf(["Thanks for shopping"])))
