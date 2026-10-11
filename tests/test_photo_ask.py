"""Step 11: a photo with no address and no @account (a product photo, a shop screen)."""
import asyncio

import pytest

from app import ask, listing


def test_the_product_and_price_are_read_off_the_photo():
    assert ask.product_from_text(["Mega Sneaker Drop", "Samba OG  $49", "Only 3 left!"]) == ("Samba OG", 49.0)
    assert ask.product_from_text(["Nike Dunk Low Panda", "$59.99", "Add to cart"]) == ("Nike Dunk Low Panda", 59.99)
    assert ask.product_from_text(["Sale", "Samba OG Shoes", "Only 3 left!"]) == ("Samba OG Shoes", None)
    assert ask.product_from_text(["Rolex Submariner US$1,250.00"]) == ("Rolex Submariner", 1250.0)
    assert ask.product_from_text(["nice"]) == ("", None)  # one stray word isn't a product


def test_a_price_far_below_trusted_stores_is_a_warning():
    alts = [{"price": 115.0, "currency": "USD"}, {"price": 120.0, "currency": "USD"}, {"price": 130.0, "currency": None}]
    low = ask._price_signal(39.99, alts)
    assert low["tone"] == "bad" and low["detail"].startswith("$39.99 here, about $120.00 at trusted stores")
    assert ask._price_signal(99.0, alts)["tone"] == "neutral"
    assert ask._price_signal(None, alts) is None and ask._price_signal(39.99, []) is None
    assert ask._price_signal(10.0, [{"price": 100.0, "currency": "EUR"}]) is None  # other currencies aren't compared


def _photo(monkeypatch, lines, alts):
    monkeypatch.setattr(ask, "read_text", lambda image: lines)

    async def extras(result):
        assert result["facts"]["product"]  # the search runs on the product read off the photo
        return {"real_brand": None, "alternatives": alts, "alternatives_for": {"what": result["facts"]["product"], "kind": "product"},
                "alternatives_note": "", "same_photo": []}
    monkeypatch.setattr(ask, "extras", extras)

    async def no_check(*a, **k):
        raise AssertionError("no seller to check")
    monkeypatch.setattr(listing, "check_url", no_check)
    return asyncio.run(ask.ask("", b"\x89PNG photo"))


def test_a_product_photo_is_couldnt_check_with_trusted_stores_and_the_price(monkeypatch):
    alts = [{"host": "solefly.com", "price": 115.0, "currency": "USD"}, {"host": "dtlr.com", "price": 120.0, "currency": "USD"}]
    r = _photo(monkeypatch, ["Nike Dunk Low Retro Panda", "$39.99", "Add to cart"], alts)
    assert r["verdict"] == "yellow" and r["headline"] == "Couldn't check"
    assert r["found"] == {"how": "screenshot", "what": "Nike Dunk Low Retro Panda",
                          "read": ["Nike Dunk Low Retro Panda", "$39.99", "Add to cart"]}
    assert [s["label"] for s in r["signals"]] == ["Price far below trusted stores", "No seller shown"]
    assert r["ask_for"] == ask.NO_SELLER and [a["host"] for a in r["alternatives"]] == ["solefly.com", "dtlr.com"]


def test_a_photo_with_no_text_or_no_product_says_what_to_send(monkeypatch):
    monkeypatch.setattr(ask, "read_text", lambda image: [])
    with pytest.raises(listing.ListingError, match="no text in that photo"):
        asyncio.run(ask.ask("", b"\x89PNG photo"))
    for lines in (["wow"], ["Thanks for shopping with us"]):  # no product with a price: not a product screen
        monkeypatch.setattr(ask, "read_text", lambda image, lines=lines: lines)
        with pytest.raises(listing.ListingError, match="in that screenshot, so I can't tell who's selling it"):
            asyncio.run(ask.ask("", b"\x89PNG photo"))
