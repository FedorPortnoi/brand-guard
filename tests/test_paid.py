"""Step 5: 'Already paid?' steps, by the payment methods the shopper could see, only on Not legit."""
import asyncio

from app import infra, listing, net, paid, rdap

STORE = b"""<html><head><title>Mega Sale Sneakers</title></head><body><p>Welcome</p>
<a href="/pages/gift-cards">Gift Cards</a>
<footer>We accept <img alt="Visa" src="/i/visa.svg"> <img src="/icons/paypal-logo.png">
<span class="payment-icon--apple-pay"></span></footer></body></html>"""


def test_payment_methods_come_from_page_text_icons_and_screenshots():
    assert paid.methods(STORE) == ["card", "paypal"]  # alt text, an icon's file name, a class name
    assert paid.methods(None, "DM to order, Zelle or CashApp only. BTC ok") == ["zelle", "cash app", "crypto"]
    assert paid.methods(None, "", "Venmo me @kicks") == ["venmo"]  # a post caption counts too


def test_ordinary_words_are_not_payment_methods():
    assert paid.methods(b"<p>Discover our collection. Ships from Zellenberg.</p>") == []
    assert "gift card" not in paid.methods(STORE)  # selling gift cards is normal
    assert paid.methods(None, "Pay with a gift card (Steam or Apple) to hold your pair") == ["gift card"]


def test_steps_only_for_not_legit():
    assert paid.steps("green", ["card"]) == {} and paid.steps("yellow", ["card"]) == {}
    red = paid.steps("red", ["card", "zelle"])
    assert red["methods"] == ["card", "zelle"]
    assert [s.split(":")[0] for s in red["steps"]] == ["Card", "Zelle"]
    assert red["report"] == [f"Report it to the FTC: {paid.FTC}", f"Report the site to Google: {paid.REPORT_SITE}"]


def test_no_method_seen_gives_one_general_step_and_platforms_get_their_own_report():
    r = paid.steps("red", [], "Instagram", "social")
    assert r["steps"] == [paid.ANY]
    assert r["report"][1] == "Report the seller to Instagram: use Report on the post or listing."


def test_every_method_has_fixed_advice():
    assert set(paid.STEPS) == set(paid.METHODS)
    assert "ic3.gov" in paid.STEPS["crypto"] and "paypal.com/disputes" in paid.STEPS["paypal"]


def _offline(monkeypatch, html):
    async def fetch_html(client, url): return html
    async def lookup(domain, client=None): return {"age_days": 3, "created": "2026-10-08T00:00:00Z"}
    async def describe(host, client):
        return {"ips": ["198.51.100.9"], "network": "X", "org": "X", "range": None, "shared": None, "platform": False}
    async def safe(url): return True
    async def final(client, url): return url
    monkeypatch.setattr(net, "fetch_html", fetch_html)
    monkeypatch.setattr(net, "safe_url_async", safe)
    monkeypatch.setattr(rdap, "lookup", lookup)
    monkeypatch.setattr(infra, "describe", describe)
    monkeypatch.setattr(listing, "final_url", final)


def test_a_not_legit_check_carries_the_steps_and_a_legit_one_does_not(monkeypatch):
    _offline(monkeypatch, STORE)
    r = asyncio.run(listing.check_url("https://mega-sale.example/p", log=False))
    assert r["verdict"] == "red" and r["already_paid"]["methods"] == ["card", "paypal"]
    old = {"age_days": 4000, "created": "2015-01-01T00:00:00Z"}

    async def lookup(domain, client=None): return old
    monkeypatch.setattr(rdap, "lookup", lookup)
    r = asyncio.run(listing.check_url("https://mega-sale.example/p", log=False))
    assert r["verdict"] == "green" and r["already_paid"] == {}
