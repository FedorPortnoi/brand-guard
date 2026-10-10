"""Local, free image forensics: compare a found copy against the brand's original."""
import base64
import io

import cv2
import imagehash
import numpy as np
from PIL import ExifTags, Image

# === Threshold constants for relatedness and classification ===
# Relatedness decision (any one met -> related):
HASH_RELATED_MAX = 35  # max hash distance for related via hashes
INLIERS_MIN = 13  # min ORB feature inliers for strong features
INLIER_RATIO_MIN = 0.4  # min ratio of inliers to good matches (real resized copies score 0.41-0.51)
TEMPLATE_MIN = 0.70  # 0.5 flagged 16 unrelated real photo pairs (all scored 0.50-0.62); see data/real_eval

# Classification thresholds (kind):
EXACT_HASH_MAX = 6  # max hash distance for "exact"
EXACT_CHANGED_MAX = 2  # max % changed for "exact"
ALTERED_HASH_MAX = 90  # max hash distance for "altered"
ALTERED_CHANGED_MIN = 8  # min % changed for "altered"
CROPPED_HASH_MIN = 30  # min hash distance (with strong features) for "cropped"
MODIFIED_HASH_MAX = 30  # max hash distance for "modified"

# === Homography sanity checks ===
# Reject degenerate homographies (repeated texture, near-identical images with flips/reflections)
CHECK_HOMOGRAPHY = True  # reject impossible geometry; real-web false positives had singular-value ratios in the thousands
DET_MIN = 0.02  # min determinant of 2x2 linear part
DET_MAX = 50.0  # max determinant
SINGULAR_RATIO_MAX = 4.0  # max ratio of singular values (extreme shear)
PERSPECTIVE_MAX = 0.002  # max |H[2,0]| or |H[2,1]| (extreme perspective)
HULL_FRAC_MIN = 0.10  # min share of the original's feature area covered by inliers; results identical for 0.05-0.30


def load(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode in ("P", "LA", "RGBA") or "transparency" in img.info:
        rgba = img.convert("RGBA")  # flatten transparency onto white, not black
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return img.convert("RGB")


def _gray(img: Image.Image, max_side=640) -> np.ndarray:
    arr = np.array(img)
    g = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    h, w = g.shape
    s = max_side / max(h, w)
    if s < 1:
        g = cv2.resize(g, (int(w * s), int(h * s)))
    return g


class Reference:
    """Precomputed fingerprints of the original so each candidate is cheap to check."""

    def __init__(self, data: bytes):
        self.img = load(data)
        self.phash = imagehash.phash(self.img, hash_size=16)
        self.dhash = imagehash.dhash(self.img, hash_size=16)
        self.gray = _gray(self.img)
        self.orb = cv2.ORB_create(1500)
        self.kp, self.des = self.orb.detectAndCompute(self.gray, None)
        self.c2pa = None  # set by caller: provenance state of the ORIGINAL


def _is_degenerate_homography(H: np.ndarray, ref_kp, cand_kp, good_matches, mask, ref_gray):
    """Check if homography is degenerate (spurious from repeated texture, reflections, etc.)."""
    if H is None:
        return True, 0.0

    # Check determinant and singular values of 2x2 linear part
    A = H[:2, :2]
    det = float(np.linalg.det(A))
    if det <= 0 or det < DET_MIN or det > DET_MAX:
        return True, 0.0

    # Check singular value ratio (extreme shear/anisotropy)
    _, s, _ = np.linalg.svd(A)
    if s[0] / max(s[1], 1e-6) > SINGULAR_RATIO_MAX:
        return True, 0.0

    # Check perspective (extreme perspective distortion)
    if abs(H[2, 0]) > PERSPECTIVE_MAX or abs(H[2, 1]) > PERSPECTIVE_MAX:
        return True, 0.0

    # Check hull coverage: inliers must cover >= 15% of reference image
    inlier_indices = [i for i, m in enumerate(good_matches) if mask[i, 0]]
    if not inlier_indices:
        return True, 0.0

    inlier_pts = np.float32([ref_kp[good_matches[i].trainIdx].pt for i in inlier_indices])
    if len(inlier_pts) < 4:
        return True, 0.0

    hull_area = float(cv2.contourArea(cv2.convexHull(inlier_pts)))
    # Measure coverage against the area where the original HAS features, not the whole canvas,
    # so a perfect copy of a small or sparse logo still scores 1.0.
    all_pts = np.float32([k.pt for k in ref_kp])
    ref_area = float(cv2.contourArea(cv2.convexHull(all_pts))) if len(all_pts) >= 3 else 0.0
    hull_frac = min(1.0, hull_area / ref_area) if ref_area > 0 else 0.0

    if hull_frac < HULL_FRAC_MIN:
        return True, hull_frac

    return False, hull_frac


def _orb_match(ref: Reference, cand_gray: np.ndarray):
    """Match ORB features. Returns (inliers, ratio, H, degenerate, hull_frac)."""
    kp2, des2 = ref.orb.detectAndCompute(cand_gray, None)
    if ref.des is None or des2 is None or len(kp2) < 8 or len(ref.kp) < 8:
        return 0, 0.0, None, False, 0.0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    good = [m for m, n in (p for p in bf.knnMatch(des2, ref.des, k=2) if len(p) == 2)
            if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return len(good), 0.0, None, False, 0.0
    src = np.float32([kp2[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([ref.kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if H is None:
        return len(good), 0.0, None, False, 0.0
    inliers = int(mask.sum())

    # Check for degenerate homography
    is_degen, hull_frac = _is_degenerate_homography(H, ref.kp, kp2, good, mask, ref.gray)
    if CHECK_HOMOGRAPHY and is_degen:
        return 0, 0.0, H, True, hull_frac

    return inliers, inliers / max(len(good), 1), H, is_degen, hull_frac


def _heatmap(ref: Reference, cand: Image.Image, H) -> tuple[str, float]:
    """Align candidate onto the original and return (diff overlay as data URI, % pixels changed)."""
    rg = ref.gray
    cg = _gray(cand)
    if H is not None:
        aligned = cv2.warpPerspective(cg, H, (rg.shape[1], rg.shape[0]))
    else:
        aligned = cv2.resize(cg, (rg.shape[1], rg.shape[0]))
    a = cv2.GaussianBlur(rg, (5, 5), 0)
    b = cv2.GaussianBlur(aligned, (5, 5), 0)
    # normalise brightness so simple recolours/exposure shifts don't count as edits
    b = cv2.normalize(b, None, int(a.min()), int(a.max()), cv2.NORM_MINMAX)
    diff = cv2.absdiff(a, b)
    valid = (cv2.warpPerspective(np.full_like(cg, 255), H, (rg.shape[1], rg.shape[0])) > 0) if H is not None else np.ones_like(rg, bool)
    changed = (diff > 45) & valid
    pct = float(changed.mean() * 100)
    color = cv2.applyColorMap(cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX), cv2.COLORMAP_INFERNO)
    base = cv2.cvtColor(rg, cv2.COLOR_GRAY2BGR)
    overlay = cv2.addWeighted(base, 0.45, color, 0.55, 0)
    ok, buf = cv2.imencode(".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode(), pct


AI_TOOLS = ("stable diffusion", "midjourney", "dall-e", "dalle", "firefly", "comfyui", "automatic1111", "sdxl", "flux")


def _mime(data: bytes) -> str:
    return {b"\xff\xd8": "image/jpeg", b"\x89P": "image/png", b"RI": "image/webp", b"GI": "image/gif"}.get(data[:2], "image/jpeg")


def c2pa_status(data: bytes) -> dict:
    """Real Content Credentials validation (c2pa-python). States: trusted | valid | invalid | not_present | error.
    'valid' means the signature/hashes check out, NOT that the signer is trusted (no trust list is configured)."""
    try:
        from c2pa import Reader
        with Reader(_mime(data), io.BytesIO(data)) as reader:
            state = (reader.get_validation_state() or "unknown").lower()
            active = reader.get_active_manifest() or {}
            return {"state": state, "issuer": ((active.get("signature_info") or {}).get("issuer")),
                    "claim_generator": active.get("claim_generator")}
    except Exception as e:
        if "ManifestNotFound" in f"{type(e).__name__} {e}":
            return {"state": "not_present"}
        return {"state": "error", "error": type(e).__name__}


def ai_signal(data: bytes, img: Image.Image) -> dict:
    """Weak, honest hints only. Never proof of AI generation."""
    hints = []
    try:
        meta = Image.open(io.BytesIO(data))
        blob = " ".join(str(v) for v in meta.info.values()).lower() + " " + str(dict(meta.getexif())).lower()
        raw = data[:60_000].lower()
        for t in AI_TOOLS:
            if t in blob or t.encode() in raw:
                hints.append(f"metadata mentions '{t}'")
                break
    except Exception:
        pass
    # IPTC/XMP digital-source-type used by generators to self-declare synthetic media
    if b"trainedalgorithmicmedia" in data[:200_000].lower():
        hints.append("XMP declares trainedAlgorithmicMedia")
    ela = _ela_score(img)
    if ela is not None and ela > 0.12:
        hints.append("uneven JPEG recompression (possible local editing)")
    level = "declared" if any("trainedAlgorithmic" in h or "metadata" in h for h in hints) else ("weak" if hints else "none")
    return {"level": level, "hints": hints, "note": "Heuristic hints only. Not proof, and a clean result does not mean the image is real."}


def _ela_score(img: Image.Image) -> float | None:
    try:
        small = img.copy(); small.thumbnail((512, 512))
        buf = io.BytesIO(); small.save(buf, "JPEG", quality=90); buf.seek(0)
        diff = np.abs(np.asarray(small, np.int16) - np.asarray(Image.open(buf).convert("RGB"), np.int16)).max(axis=2)
        return float((diff > 12).mean())
    except Exception:
        return None


def _template_score(ref: Reference, cand_gray: np.ndarray) -> float:
    """Edge template match over a scale pyramid. Helps flat logos where ORB finds no keypoints."""
    try:
        side = 320
        h, w = cand_gray.shape
        s = side / max(h, w)
        cg = cv2.resize(cand_gray, (max(int(w * s), 8), max(int(h * s), 8)))
        ce = cv2.dilate(cv2.Canny(cv2.GaussianBlur(cg, (3, 3), 0), 60, 160), None)
        ce = cv2.copyMakeBorder(ce, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=0)  # allow small offsets / near-equal sizes
        best = 0.0
        for frac in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
            t = cv2.resize(ref.gray, None, fx=side * frac / max(ref.gray.shape), fy=side * frac / max(ref.gray.shape))
            if t.shape[0] >= ce.shape[0] or t.shape[1] >= ce.shape[1] or t.shape[0] < 16:
                continue
            te = cv2.dilate(cv2.Canny(cv2.GaussianBlur(t, (3, 3), 0), 60, 160), None)
            if te.mean() < 3:
                continue
            best = max(best, float(cv2.matchTemplate(ce, te, cv2.TM_CCOEFF_NORMED).max()))
        return best
    except Exception:
        return 0.0


def features(ref: Reference, data: bytes) -> dict:
    """Compute forensic features for a candidate against a reference.
    Returns: {hash_dist, inliers, ratio, tmpl, changed, degenerate, hull_frac}
    Note: changed is computed lazily in compare() only for related candidates."""
    cand = load(data)
    ph = ref.phash - imagehash.phash(cand, hash_size=16)
    dh = ref.dhash - imagehash.dhash(cand, hash_size=16)
    cg = _gray(cand)
    inliers, ratio, H, degenerate, hull_frac = _orb_match(ref, cg)
    tmpl = _template_score(ref, cg)
    hash_dist = min(ph, dh)
    return {
        "hash_dist": hash_dist,
        "inliers": inliers,
        "ratio": ratio,
        "tmpl": tmpl,
        "changed": None,  # Computed lazily in compare()
        "degenerate": degenerate,
        "hull_frac": round(hull_frac, 3),
    }


def compare(ref: Reference, data: bytes) -> dict | None:
    """Return None if the candidate is unrelated to the original."""
    cand = load(data)
    feats = features(ref, data)
    hash_dist = feats["hash_dist"]
    inliers = feats["inliers"]
    ratio = feats["ratio"]
    tmpl = feats["tmpl"]

    strong_features = inliers >= INLIERS_MIN and ratio >= INLIER_RATIO_MIN
    related = hash_dist <= HASH_RELATED_MAX or strong_features or tmpl >= TEMPLATE_MIN
    if not related:
        return None

    # Compute changed and heatmap only for related candidates (lazy computation)
    cg = _gray(cand)
    _, _, H, _, _ = _orb_match(ref, cg)
    heat, changed = _heatmap(ref, cand, H)
    c2 = c2pa_status(data)
    ai = ai_signal(data, cand)

    if hash_dist <= EXACT_HASH_MAX and changed < EXACT_CHANGED_MAX:
        kind, label = "exact", "Exact / re-compressed copy"
    elif strong_features and hash_dist <= ALTERED_HASH_MAX and changed >= ALTERED_CHANGED_MIN:
        kind, label = "altered", "Same image, visibly edited"
    elif strong_features and hash_dist > CROPPED_HASH_MIN:
        kind, label = "cropped", "Cropped / reframed / composited copy"
    elif strong_features or hash_dist <= MODIFIED_HASH_MAX or tmpl >= TEMPLATE_MIN:
        kind, label = "modified", "Resized / recoloured / lightly modified"
    else:
        kind, label = "lookalike", "Lookalike (possible re-draw or AI re-generation)"

    return {
        "kind": kind,
        "label": label,
        "hash_distance": int(hash_dist),
        "feature_inliers": inliers,
        "inlier_ratio": round(ratio, 2),
        "changed_pct": round(changed, 1),
        "heatmap": heat,
        "template_score": round(tmpl, 2),
        "c2pa": c2,
        "ai_signal": ai,
        "size": list(cand.size),
    }


_KIND_RANK = {"exact": 5, "altered": 4, "modified": 3, "cropped": 2, "lookalike": 1}


def compare_best(refs: list[Reference], data: bytes) -> tuple[dict | None, int | None]:
    """Compare against several references (a video's key frames) and return (best match, its index).

    The candidate is analysed once. The full comparison runs on the reference closest by perceptual hash, and
    on any other reference that a cheap check (hash distance or ORB ratio-test matches) says could match,
    so an unrelated image costs about one full comparison instead of one per key frame.
    """
    if len(refs) == 1:
        f = compare(refs[0], data)
        return (f, 0) if f else (None, None)
    cand = load(data)
    ph, dh = imagehash.phash(cand, hash_size=16), imagehash.dhash(cand, hash_size=16)
    kp2, des2 = refs[0].orb.detectAndCompute(_gray(cand), None)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)

    def could_match(ref: Reference) -> bool:
        if min(ref.phash - ph, ref.dhash - dh) <= HASH_RELATED_MAX:
            return True
        if ref.des is None or des2 is None or len(kp2) < 8 or len(ref.kp) < 8:
            return False
        good = sum(1 for p in bf.knnMatch(des2, ref.des, k=2) if len(p) == 2 and p[0].distance < 0.75 * p[1].distance)
        return good >= INLIERS_MIN

    order = sorted(range(len(refs)), key=lambda i: refs[i].phash - ph)
    best, best_i = None, None
    for i in [order[0]] + [i for i in order[1:] if could_match(refs[i])]:
        f = compare(refs[i], data)
        if f and (best is None or (_KIND_RANK.get(f["kind"], 0), -f["hash_distance"])
                  > (_KIND_RANK.get(best["kind"], 0), -best["hash_distance"])):
            best, best_i = f, i
        if best and best["kind"] == "exact":
            break
    return best, best_i
