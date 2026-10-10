"""Build small test videos from the real evaluation photos (data/real_eval)."""
import os
import tempfile
from pathlib import Path

import cv2
import numpy as np

REAL = Path(__file__).parent.parent / "data" / "real_eval"
W, H, FPS = 480, 270, 10


def photos(names):
    out = []
    for n in names:
        p = REAL / n
        if not p.exists():
            return None
        out.append(cv2.imread(str(p)))
    return out


def scene_frames(img, seconds, w=W, h=H):
    """A slow pan/zoom over one photo, like a commercial shot."""
    ih, iw = img.shape[:2]
    frames = []
    n = int(seconds * FPS)
    for k in range(n):
        z = 0.85 - 0.15 * k / max(1, n - 1)  # slow zoom in
        cw, ch = iw * z, ih * z
        cw, ch = (min(cw, ch * w / h), min(ch, cw * h / w))
        x = (iw - cw) * (0.3 + 0.4 * k / max(1, n - 1)); y = (ih - ch) / 2
        crop = img[int(y):int(y + ch), int(x):int(x + cw)]
        frames.append(cv2.resize(crop, (w, h), interpolation=cv2.INTER_AREA))
    return frames


def encode(frames, fps=FPS) -> bytes:
    h, w = frames[0].shape[:2]
    fd, path = tempfile.mkstemp(suffix=".mp4"); os.close(fd)
    try:
        vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        for f in frames:
            vw.write(f)
        vw.release()
        return Path(path).read_bytes()
    finally:
        os.remove(path)


def black(seconds, w=W, h=H):
    return [np.zeros((h, w, 3), np.uint8)] * int(seconds * FPS)
