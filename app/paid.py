"""'Already paid?': what to do now, by how the shopper paid, for a Not-legit answer.

The payment methods come from what the shopper could see: the page's text and its payment icons (alt text,
titles, file names), the post's caption and the screenshot they sent. The advice is fixed text; nothing here
claims money will come back."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

FTC = "https://reportfraud.ftc.gov"
REPORT_SITE = "https://safebrowsing.google.com/safebrowsing/report_phish/"

# method -> words that show it is accepted (or asked for)
METHODS = {
    "card": ("visa", "mastercard", "master card", "american express", "amex", "discover card", "credit card", "debit card",
             "apple pay", "google pay", "shop pay"),
    "paypal": ("paypal",),
    "zelle": ("zelle",),
    "cash app": ("cash app", "cashapp"),
    "venmo": ("venmo",),
    "crypto": ("bitcoin", "btc", "usdt", "tether", "ethereum", "eth wallet", "crypto", "wallet address", "binance pay"),
    # stores sell gift cards; only being asked to *pay* with one is the scam
    "gift card": ("pay with gift card", "pay with a gift card", "pay by gift card", "gift card payment", "itunes card",
                  "steam card", "google play card"),
}

STEPS = {
    "card": "Card: call the number on the back of your card now, say the charge is fraud and ask for a chargeback and a new card number.",
    "paypal": "PayPal: open a dispute at paypal.com/disputes (within 180 days). Goods & Services payments are covered; Friends & Family usually isn't.",
    "zelle": "Zelle: call your bank now and report it as fraud. Zelle works like cash, so speed matters.",
    "cash app": "Cash App: in the app, open the payment, tap \"Report a payment issue\", and contact your bank too.",
    "venmo": "Venmo: in the app, open the payment and report it, and contact your bank too. Payments to strangers are rarely covered.",
    "crypto": "Crypto: it almost never comes back. Report it at ic3.gov with the wallet address and transaction ID. Anyone offering to \"recover\" it for a fee is another scam.",
    "gift card": "Gift card: contact the company that issued the card now, with the card number and receipt, and ask them to freeze it.",
}
ANY = "Call whoever you paid through (your bank, card issuer or payment app) now and report it as fraud."


def _pattern(word: str) -> str:
    return rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])"


def methods(html: bytes | None, *texts: str) -> list[str]:
    """The payment methods named on the page (text, icon alt/title/file names) or in the other texts, in METHODS order."""
    parts = [t for t in texts if t]
    if html:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        parts.append(soup.get_text(" "))
        for img in soup.find_all(["img", "svg", "use", "a", "span", "li"]):
            parts += [str(img.get(a) or "") for a in ("alt", "title", "aria-label", "src", "href", "class")]
    text = " ".join(parts).lower().replace("-", " ").replace("_", " ")
    return [m for m, words in METHODS.items() if any(re.search(_pattern(w), text) for w in words)]


def steps(verdict: str, found: list[str], platform: str | None = None, kind: str | None = None) -> dict:
    """The 'Already paid?' block: empty unless Not legit (red). Steps for each method found, or one general step
    when none was seen; then where to report it."""
    if verdict != "red":
        return {}
    report = [f"Report it to the FTC: {FTC}"]
    if kind:
        report.append(f"Report the seller to {platform}: use Report on the post or listing.")
    else:
        report.append(f"Report the site to Google: {REPORT_SITE}")
    return {"methods": found, "steps": [STEPS[m] for m in found] or [ANY], "report": report}
