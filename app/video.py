"""Video support for short commercials and reposted clips.

1. key_frames(): one representative frame per scene, so a scan can match stills, thumbnails and reposts of any scene.
2. compare(): frame-by-frame check of a suspect clip against the original. Each sampled frame gets a 256-bit
   perceptual hash; variants catch the usual repost edits: mirrored, zoomed in, reframed to vertical (9:16)
   for Reels / TikTok, and played faster or slower.

Decoding uses OpenCV's bundled FFmpeg; nothing else is needed.
"""
import base64
import io
import os
import tempfile

import cv2
import imagehash
import numpy as np
from PIL import Image, ImageOps

from . import forensics

MAX_BYTES = 200_000_000
MAX_SECONDS = 600  # only the first 10 minutes are analysed
SAMPLE_FPS = 2.0  # frames per second sampled from the suspect clip in compare()
ORIGINAL_FPS = 6.0  # the original is sampled densely, so fast cuts still have a frame close in time
MAX_ORIGINAL_FRAMES = 1800
SCENE_CUT = 60  # hash distance (of 256 bits) between consecutive 1-fps frames that starts a new scene
FRAME_MATCH_MAX = 40  # hash distance (of 256) for two frames to count as the same shot
WEAK_MATCH_MAX = 64  # accepted only where it agrees with the time offset set by strong matches
OFFSET_WINDOW = 0.6  # seconds around the predicted original time searched for a weak match
MERGE_GAP = 3.0  # seconds; matches this close with the same offset form one segment
MIN_STD = 10  # grey-level standard deviation below which a frame is blank (black, fades) and never matched
KEY_FRAMES = 8
CARD_FLAT_SHARE = 0.70  # a frame this flat is a title / end card
KEY_FRAME_DISTINCT = 60  # key frames closer than this (hash distance) count as the same picture
SPEED_TOLERANCE = 0.15  # report a speed change when playback differs from 1x by more than this

_POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def is_video(data: bytes) -> bool:
    """Sniff common video containers: MP4/MOV (ftyp), WebM/MKV, AVI, FLV."""
    head = data[:16]
    return (head[4:8] == b"ftyp" and head[8:12] not in (b"avif", b"avis", b"heic", b"heix", b"mif1", b"msf1")) \
        or head[:4] == b"\x1a\x45\xdf\xa3" or (head[:4] == b"RIFF" and head[8:12] == b"AVI ") or head[:3] == b"FLV"


class VideoError(ValueError):
    pass


def _sample(data: bytes, fps: float, size: int = 640) -> tuple[list[tuple[float, Image.Image]], float, bool]:
    """Decode and sample frames at about `fps`, at most `size` pixels on the long side.
    Returns ([(t, RGB image)], duration seconds, truncated)."""
    if len(data) > MAX_BYTES:
        raise VideoError(f"Video is larger than {MAX_BYTES // 1_000_000} MB")
    fd, path = tempfile.mkstemp(suffix=".video")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise VideoError("Could not read that video (unsupported format or corrupt file)")
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if not 1 <= src_fps <= 240:
            src_fps = 25.0
        step = max(1, round(src_fps / fps))
        out, i, truncated = [], 0, False
        while True:
            if i / src_fps >= MAX_SECONDS:
                truncated = True
                break
            if not cap.grab():
                break
            if i % step == 0:
                ok, frame = cap.retrieve()
                if ok and frame is not None:
                    h, w = frame.shape[:2]
                    s = size / max(h, w)
                    if s < 1:
                        frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
                    out.append((i / src_fps, Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))))
            i += 1
        cap.release()
        if not out:
            raise VideoError("Could not read any frames from that video")
        return out, i / src_fps, truncated
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _bits(img: Image.Image) -> np.ndarray:
    return np.packbits(imagehash.phash(img, hash_size=16).hash.flatten())


def _informative(img: Image.Image) -> bool:
    return float(np.asarray(img.convert("L")).std()) >= MIN_STD


def _is_card(img: Image.Image) -> bool:
    """Title / end card: a logo or text on a plain background. Most of the frame is one flat grey level.
    Every logo image on the web resembles these, so they make poor evidence. Measured on real commercials:
    cards 73-97 % flat, scenes 16-24 %."""
    g = np.asarray(img.convert("L").resize((160, 90)))
    return np.bincount((g // 16).ravel(), minlength=16).max() / g.size >= CARD_FLAT_SHARE


def _usable(frames: list) -> list[int]:
    """Indices of frames worth matching: not blank, and not title/end cards unless the video is only cards."""
    ok = [k for k, (_, im) in enumerate(frames) if _informative(im)]
    scenes = [k for k in ok if not _is_card(frames[k][1])]
    return scenes or ok


def _dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamming distances between packed hashes a (n, 32) and b (m, 32) -> (n, m)."""
    return _POP[np.bitwise_xor(a[:, None, :], b[None, :, :])].sum(-1, dtype=np.uint16)


def _jpeg(img: Image.Image, side: int, quality: int = 85) -> bytes:
    im = img.copy()
    im.thumbnail((side, side))
    b = io.BytesIO(); im.save(b, "JPEG", quality=quality)
    return b.getvalue()


def _uri(img: Image.Image, side: int = 200) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(_jpeg(img, side, 80)).decode()


def key_frames(data: bytes, n: int = KEY_FRAMES) -> list[dict]:
    """Up to n distinct frames spread over the whole video. Each: {t, jpeg (up to 960 px), thumb (data URI)}, by time.

    The video is split into n equal stretches of time; each gives the middle frame of its longest scene, unless
    that looks like a frame already picked. Leftover slots go to the most distinct remaining frames. Fast-cut
    commercials have a "scene" almost every second, so picking only the longest scenes would bunch frames together.
    """
    frames, _, _ = _sample(data, 1.0)
    good = [frames[k] for k in _usable(frames)]
    if not good:
        raise VideoError("The video has no usable frames (blank or too dark)")
    hashes = np.stack([_bits(im) for _, im in good])
    times = np.array([t for t, _ in good])
    d_next = _dist(hashes[:-1], hashes[1:]).diagonal() if len(good) > 1 else np.array([])
    cuts = [0] + [k + 1 for k, d in enumerate(d_next) if d > SCENE_CUT] + [len(good)]
    scenes = [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]
    middles = [((a + b) // 2, b - a) for a, b in scenes]  # (frame index, scene length)

    picked: list[int] = []

    def distinct(k: int) -> bool:
        return not picked or int(_dist(hashes[[k]], hashes[picked]).min()) > KEY_FRAME_DISTINCT

    edges = np.linspace(times[0], times[-1] + 1e-6, n + 1)
    for lo, hi in zip(edges, edges[1:]):
        for k, _ in sorted(((k, ln) for k, ln in middles if lo <= times[k] < hi), key=lambda x: -x[1]):
            if distinct(k):
                picked.append(k)
                break
    while len(picked) < n:
        rest = [k for k in range(len(good)) if k not in picked]
        if not rest:
            break
        far = _dist(hashes[rest], hashes[picked]).min(1) if picked else np.full(len(rest), 999)
        k = rest[int(far.argmax())]
        if picked and far.max() <= KEY_FRAME_DISTINCT:
            break
        picked.append(k)
    return [{"t": round(good[k][0], 1), "jpeg": _jpeg(good[k][1], 960, 90), "thumb": _uri(good[k][1], 160)} for k in sorted(picked)]


def _variants_original(img: Image.Image) -> dict[str, Image.Image]:
    w, h = img.size
    out = {"full": img, "zoomed": img.crop((w * 0.1, h * 0.1, w * 0.9, h * 0.9))}
    if w > h * 1.1:  # landscape original: a vertical repost keeps a centred 9:16 strip
        vw = h * 9 / 16
        out["vertical"] = img.crop(((w - vw) / 2, 0, (w + vw) / 2, h))
    return out


FLAG_TEXT = {"mirrored": "Mirrored (flipped left to right)", "zoomed": "Zoomed in / cropped",
             "vertical": "Reframed to vertical 9:16 (typical of Reels / TikTok reposts)"}


def _theil_sen(x: np.ndarray, y: np.ndarray, max_pairs: int = 400) -> float:
    """Median of pairwise slopes: robust to the odd mismatched frame."""
    i, j = np.triu_indices(len(x), 1)
    keep = np.abs(x[j] - x[i]) >= 1.0
    i, j = i[keep], j[keep]
    if len(i) > max_pairs:
        pick = np.linspace(0, len(i) - 1, max_pairs).astype(int)
        i, j = i[pick], j[pick]
    return float(np.median((y[j] - y[i]) / (x[j] - x[i]))) if len(i) else 1.0


def compare(original: bytes, suspect: bytes) -> dict:
    """How much of `original` appears in `suspect`, where, and how it was edited.

    The original is sampled densely (ORIGINAL_FPS) so every suspect frame has an original frame close in time;
    fast cuts and animation change a lot within a quarter of a second. Strong frame matches fix the time
    offset between the videos, and weaker matches are accepted only where they agree with that offset.
    """
    O, o_dur, o_trunc = _sample(original, ORIGINAL_FPS)
    if len(O) > MAX_ORIGINAL_FRAMES:
        O = [O[k] for k in np.linspace(0, len(O) - 1, MAX_ORIGINAL_FRAMES).astype(int)]
    S, s_dur, s_trunc = _sample(suspect, SAMPLE_FPS)
    o_ok = _usable(O)
    s_ok = _usable(S)
    result = {"original_seconds": round(o_dur, 1), "suspect_seconds": round(s_dur, 1), "truncated": o_trunc or s_trunc,
              "segments": [], "flags": [], "evidence": [], "original_coverage_pct": 0.0, "suspect_match_pct": 0.0}
    if not o_ok or not s_ok:
        return {**result, "verdict": "no_match", "label": "No usable frames to compare (blank or too dark)"}

    # distance of every suspect frame to every original frame, minimised over the edit variants
    combos = []  # (variant name, mirrored, original row indices, packed hashes)
    o_var: dict[str, list] = {"full": [], "zoomed": [], "vertical": []}
    for k in o_ok:
        for name, im in _variants_original(O[k][1]).items():
            o_var[name].append((k, _bits(im)))
    s_full = np.stack([_bits(S[k][1]) for k in s_ok])
    s_mirr = np.stack([_bits(ImageOps.mirror(S[k][1])) for k in s_ok])
    col = {k: c for c, k in enumerate(o_ok)}
    D = np.full((len(s_ok), len(o_ok)), 999, dtype=np.uint16)
    how = np.zeros((len(s_ok), len(o_ok)), dtype=np.uint8)
    for name in ("full", "zoomed", "vertical"):  # "full" and unmirrored first, so ties prefer the plain match
        rows = o_var[name]
        if not rows:
            continue
        cols = np.array([col[k] for k, _ in rows]); hashes = np.stack([h for _, h in rows])
        for mirrored, sh in ((False, s_full), (True, s_mirr)):
            combos.append((name, mirrored))
            for a in range(0, len(s_ok), 200):  # chunked to bound memory on long videos
                d = _dist(sh[a:a + 200], hashes)
                cur = D[a:a + 200][:, cols]
                better = d < cur
                cur[better] = d[better]
                D[a:a + 200][:, cols] = cur
                h = how[a:a + 200][:, cols]
                h[better] = len(combos) - 1
                how[a:a + 200][:, cols] = h

    St = np.array([S[k][0] for k in s_ok]); Ot = np.array([O[k][0] for k in o_ok])
    best_col = D.argmin(1); best_d = D[np.arange(len(s_ok)), best_col]
    strong = best_d <= FRAME_MATCH_MAX
    pairs = {int(q): int(best_col[q]) for q in np.nonzero(strong)[0]}
    if not pairs:
        return {**result, "verdict": "no_match", "label": "No match found: none of the suspect clip's frames match your video"}

    # time offsets supported by strong matches, then weaker matches that agree with one of them
    offs = np.sort(np.array([Ot[c] - St[q] for q, c in pairs.items()]))
    groups = np.split(offs, np.nonzero(np.diff(offs) > 0.5)[0] + 1)
    centers = [float(np.median(g)) for g in groups if len(g) >= max(2, 0.1 * len(offs))] or [float(np.median(offs))]
    for q in range(len(s_ok)):
        if q in pairs:
            continue
        for c in centers:
            win = np.nonzero(np.abs(Ot - (St[q] + c)) <= OFFSET_WINDOW)[0]
            if len(win):
                j = win[D[q, win].argmin()]
                if D[q, j] <= WEAK_MATCH_MAX:
                    pairs[q] = int(j)
                    break

    matched = sorted(pairs.items())  # (suspect row, original col)
    segs, cur = [], [matched[0]]
    for q, c in matched[1:]:
        pq, pc = cur[-1]
        if St[q] - St[pq] <= MERGE_GAP and abs((Ot[c] - St[q]) - (Ot[pc] - St[pq])) <= 1.0:
            cur.append((q, c))
        else:
            segs.append(cur); cur = [(q, c)]
    segs.append(cur)

    step = 1 / SAMPLE_FPS
    segments = []
    for seg in segs:
        ts = np.array([St[q] for q, _ in seg]); to = np.array([Ot[c] for _, c in seg])
        speed = None
        if len(seg) >= 6 and ts[-1] - ts[0] >= 4:
            slope = _theil_sen(ts, to)
            if abs(slope - 1) > SPEED_TOLERANCE:
                speed = round(slope, 2)
        segments.append({"suspect_start": round(float(ts[0]), 1), "suspect_end": round(float(ts[-1] + step), 1),
                         "original_start": round(float(to.min()), 1), "original_end": round(float(to.max() + step), 1),
                         "frames": len(seg), "speed": speed})

    hows = [combos[how[q, c]] for q, c in matched]
    flags = []
    if sum(mirr for _, mirr in hows) > 0.3 * len(hows):
        flags.append(FLAG_TEXT["mirrored"])
    for name in ("vertical", "zoomed"):
        if sum(n == name for n, _ in hows) > 0.3 * len(hows):
            flags.append(FLAG_TEXT[name])
    speeds = [s["speed"] for s in segments if s["speed"]]
    if speeds:
        flags.append(f"Played at about {speeds[0]}x speed")

    longest = max(s["suspect_end"] - s["suspect_start"] for s in segments)
    suspect_pct = round(100 * len(matched) / len(s_ok), 1)
    o_secs = {int(t) for t in Ot}
    coverage = round(100 * len({int(Ot[c]) for _, c in matched}) / max(1, len(o_secs)), 1)
    if longest >= 3 or suspect_pct >= 30:
        verdict, label = "copy", "Contains your video"
    elif len(matched) >= 2:
        verdict, label = "partial", "Some scenes match your video"
    else:
        verdict, label = "weak", "One frame resembles your video (weak evidence)"

    # evidence: the best-matching frame of the longest segments, and a pixel-level check of the closest pair
    evidence = []
    for seg in sorted(segs, key=len, reverse=True)[:4]:
        q, c = min(seg, key=lambda p: D[p[0], p[1]])
        evidence.append({"suspect_t": round(float(St[q]), 1), "original_t": round(float(Ot[c]), 1),
                         "hash_distance": int(D[q, c]),
                         "suspect_thumb": _uri(S[s_ok[q]][1]), "original_thumb": _uri(O[o_ok[c]][1])})
    q, c = min(matched, key=lambda p: D[p[0], p[1]])
    sus_img = S[s_ok[q]][1]
    if combos[how[q, c]][1]:
        sus_img = ImageOps.mirror(sus_img)  # undo the flip so pixels line up
    try:
        f = forensics.compare(forensics.Reference(_jpeg(O[o_ok[c]][1], 960, 92)), _jpeg(sus_img, 960, 92))
    except Exception:
        f = None
    pixel = f["label"] if f else "Pixel check inconclusive (heavily edited or low resolution)"

    return {**result, "verdict": verdict, "label": label, "segments": segments, "flags": flags, "evidence": evidence,
            "original_coverage_pct": coverage, "suspect_match_pct": suspect_pct, "pixel_check": pixel}
