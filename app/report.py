"""PDF evidence report for one offending match."""
import io
from datetime import datetime, timezone

from fpdf import FPDF

DISCLAIMER = ("This report is automated analysis, not legal advice or a finding of infringement. Similarity and risk "
              "scores are signals that require human review before any enforcement or takedown request. Evidence images "
              "were fetched at the capture time shown and may have changed since.")


def _t(s) -> str:
    """fpdf core fonts are latin-1 only."""
    return str(s if s is not None else "").encode("latin-1", "replace").decode("latin-1")


def build(brand: str, original: bytes, evidence: bytes | None, match: dict) -> bytes:
    f, r = match.get("forensics") or {}, match["risk"]
    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Brand Guard - Evidence Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _t(f"Brand: {brand}    Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    def kv(k, v):
        pdf.set_font("Helvetica", "B", 10); pdf.cell(38, 6, _t(k))
        pdf.set_font("Helvetica", "", 10); pdf.multi_cell(0, 6, _t(v), new_x="LMARGIN", new_y="NEXT")

    kv("Source page", match.get("page_url") or "n/a")
    kv("Image URL", match.get("image_url"))
    kv("Page title", match.get("title"))
    if match.get("ref_frame"):
        t = match["ref_frame"].get("t", 0)
        kv("Matches video at", f"{int(t // 60)}:{int(t % 60):02d} (the original image shown below is that frame)")
    if match.get("seller"):
        kv("Seller", match["seller"] + (f"  ({match['price']})" if match.get("price") else ""))
    arc = match.get("archive") or {}
    if arc.get("url"):
        kv("Archived copy", f"{arc['url']} (Wayback Machine, {arc.get('captured_at', 'date unknown')})")
    kv("Captured (UTC)", match.get("captured_at"))
    kv("Evidence SHA-256", match.get("sha256") or "n/a (image could not be downloaded)")
    kv("Verification", match.get("verification", "verified"))
    kv("Risk score", f"{r['score']}/100 ({r['category'].replace('_', ' ')})")
    if f:
        kv("Finding", f.get("label"))
        kv("Similarity", f"hash distance {f.get('hash_distance')}/256, feature inliers {f.get('feature_inliers')}, "
                         f"{f.get('changed_pct')}% pixels differ")
        kv("Content Credentials", (f.get("c2pa") or {}).get("state", "not checked") + " (c2pa validation; 'valid' = signature intact, signer trust not established)")
        ai = f.get("ai_signal") or {}
        kv("Synthetic hints", ("; ".join(ai.get("hints", [])) or "none") + " - heuristic only, not proof")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11); pdf.cell(0, 7, "Reasons", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for reason in r["reasons"]:
        pdf.multi_cell(0, 5.5, _t(f"- {reason}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    y = pdf.get_y()
    for i, (cap, blob) in enumerate((("Original (rights holder)", original), ("Found copy", evidence))):
        if not blob:
            continue
        x = 10 + i * 95
        pdf.image(io.BytesIO(blob), x=x, y=y, w=88, h=66, keep_aspect_ratio=True)
        pdf.set_xy(x, y + 70); pdf.set_font("Helvetica", "I", 9); pdf.cell(88, 5, cap)
    pdf.set_xy(10, y + 78)
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4.5, _t(DISCLAIMER), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
