"""Shared process state and paths. Import from here instead of from main, to avoid circular imports."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

# scan_id -> {"brand": str, "original": jpeg bytes, "official_domains": [str],
#             "items": {idx: {"match": match dict, "evidence": base64 jpeg | None}}}
SESSIONS: dict[str, dict] = {}


def original_for(sess: dict, match: dict) -> bytes:
    """The rights holder's image to show next to a match: the matching key frame for a video scan, else the original."""
    frame = (match.get("ref_frame") or {}).get("idx")
    frames = sess.get("frames") or []
    return frames[frame] if isinstance(frame, int) and 0 <= frame < len(frames) else sess["original"]
