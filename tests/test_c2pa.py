"""C2PA Content Credentials on a real signed image: valid, stripped and tampered copies."""
import io
from pathlib import Path

from PIL import Image

from app import forensics, risk

SIGNED = (Path(__file__).parent / "fixtures" / "c2pa_signed.jpg").read_bytes()


def _stripped() -> bytes:
    b = io.BytesIO(); Image.open(io.BytesIO(SIGNED)).convert("RGB").save(b, "JPEG", quality=90); return b.getvalue()


def _tampered() -> bytes:
    t = bytearray(SIGNED); end = len(t) - 2
    for k in range(end - 4000, end - 3000, 7):
        t[k] ^= 0x55
    return bytes(t)


def test_signed_image_is_valid():
    st = forensics.c2pa_status(SIGNED)
    assert st["state"] == "valid" and st["issuer"] == "C2PA Test Signing Cert"


def test_stripped_and_tampered_states():
    assert forensics.c2pa_status(_stripped())["state"] == "not_present"
    assert forensics.c2pa_status(_tampered())["state"] == "invalid"


def test_risk_reasons_for_stripped_and_tampered_copies():
    ref = forensics.Reference(SIGNED)
    ref.c2pa = forensics.c2pa_status(SIGNED)["state"]
    f = forensics.compare(ref, _stripped())
    r = risk.assess("https://shop.example.com/item", "photo", [], f, ref.c2pa)
    assert any("Your original carries valid Content Credentials; this copy has none" in x for x in r["reasons"])

    f = forensics.compare(ref, _tampered())
    r = risk.assess("https://shop.example.com/item", "photo", [], f, ref.c2pa)
    assert f["c2pa"]["state"] == "invalid"
    assert any("FAIL validation" in x for x in r["reasons"])
