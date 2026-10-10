"""User feedback API for labelling match results."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import base64
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .store import SESSIONS, DATA

router = APIRouter()

LABELS_DIR = DATA / "labels"
LABELS_DIR.mkdir(exist_ok=True)
REAL_LABELS_FILE = LABELS_DIR / "real.jsonl"

LABELS = ("confirmed", "false_positive", "not_sure")


class LabelIn(BaseModel):
    scan_id: str
    idx: int
    label: str
    note: str = ""


@router.post("/api/labels")
def post_label(body: LabelIn):
    """Record user feedback on a match. label: "confirmed" | "false_positive" | "not_sure"."""
    scan_id, idx, label, note = body.scan_id, body.idx, body.label, body.note[:500]
    if label not in LABELS:
        raise HTTPException(400, f"label must be one of {LABELS}")
    sess = SESSIONS.get(scan_id)
    if not sess:
        raise HTTPException(404, "Scan not found")

    item = sess["items"].get(idx)
    if not item:
        raise HTTPException(404, "Match not found")

    match = item["match"]
    f = match.get("forensics") or {}  # None for unverified matches
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "scan_id": scan_id,
        "idx": idx,
        "label": label,
        "note": note,
        "host": match.get("risk", {}).get("host"),
        "page_url": match.get("page_url"),
        "image_url": match.get("image_url"),
        "sha256": match.get("sha256"),
        "kind": f.get("kind"),
        "hash_distance": f.get("hash_distance"),
        "feature_inliers": f.get("feature_inliers"),
        "inlier_ratio": f.get("inlier_ratio"),
        "template_score": f.get("template_score"),
        "changed_pct": f.get("changed_pct"),
        "similarity": f.get("similarity"),
    }

    # Save the line to real.jsonl
    with REAL_LABELS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    # Save evidence images if available
    if item.get("evidence"):
        try:
            evidence_data = base64.b64decode(item["evidence"].split(",", 1)[1] if "," in item["evidence"] else item["evidence"])
            evidence_hash = hashlib.sha256(evidence_data).hexdigest()
            (LABELS_DIR / f"{evidence_hash}.jpg").write_bytes(evidence_data)
        except Exception:
            pass

    # Save original image
    try:
        original_data = sess.get("original", b"")
        if original_data:
            (LABELS_DIR / f"original-{scan_id}.jpg").write_bytes(original_data)
    except Exception:
        pass

    return {"status": "ok"}

@router.get("/api/labels/stats")
def get_label_stats():
    """Return counts of labels by type."""
    if not REAL_LABELS_FILE.exists():
        return {"confirmed": 0, "false_positive": 0, "not_sure": 0, "total": 0}

    counts = {"confirmed": 0, "false_positive": 0, "not_sure": 0}
    for line in REAL_LABELS_FILE.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            label = rec.get("label", "")
            if label in counts:
                counts[label] += 1
        except Exception:
            pass

    counts["total"] = sum(counts.values())
    return counts
