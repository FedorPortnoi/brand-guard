"""Video support: key frames, suspect-clip comparison, video scans, the compare endpoint and link downloads. Offline."""
import asyncio
import io
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import videogen as g  # noqa: E402

from app import pipeline, search, video, video_fetch  # noqa: E402
from app.main import app  # noqa: E402
from app.store import SESSIONS, original_for  # noqa: E402

SCENES = ["sneaker__adidas_stan_smith_shoe_0.jpg", "bottle__coca_cola_bottle_0.jpg",
          "watch__rolex_submariner_0.jpg", "bag__gucci_handbag_0.jpg"]
OTHER = ["logo__puma_logo_0.jpg", "sneaker__nike_air_max_shoe_0.jpg", "watch__casio_wristwatch_0.jpg"]


@pytest.fixture(scope="module")
def clips():
    ims, other = g.photos(SCENES), g.photos(OTHER)
    if not ims or not other:
        pytest.skip("real evaluation photos not downloaded (data/real_eval)")
    scenes = [g.scene_frames(im, 3) for im in ims]
    allf = [f for s in scenes for f in s]
    mid = [f for s in scenes[1:3] for f in s]
    return {
        "scenes": scenes,
        "original": g.encode(g.black(1) + allf),
        "clip": g.encode(mid),
        "mirrored": g.encode([cv2.flip(f, 1) for f in mid]),
        "vertical": g.encode([cv2.resize(f[:, (480 - 152) // 2:(480 + 152) // 2], (304, 540)) for f in allf[:90]]),
        "fast": g.encode(allf[::2]),
        "unrelated": g.encode([f for im in other for f in g.scene_frames(im, 3)]),
        "blank": g.encode(g.black(3)),
    }


def test_is_video(clips):
    assert video.is_video(clips["original"])
    jpg = io.BytesIO(); Image.new("RGB", (8, 8)).save(jpg, "JPEG")
    assert not video.is_video(jpg.getvalue())
    assert not video.is_video(b"\x00\x00\x00\x1cftypavif" + b"\x00" * 20)  # AVIF image, not a video


def test_key_frames_skip_black_and_cover_scenes(clips):
    kf = video.key_frames(clips["original"])
    assert 4 <= len(kf) <= video.KEY_FRAMES
    assert all(f["t"] >= 1.0 for f in kf)  # the black opening second is never a key frame
    covered = {min(int((f["t"] - 1) // 3), 3) for f in kf}
    assert covered == {0, 1, 2, 3}  # every scene is represented
    assert all(f["jpeg"][:2] == b"\xff\xd8" and f["thumb"].startswith("data:image/jpeg") for f in kf)


def test_blank_video_has_no_key_frames(clips):
    with pytest.raises(video.VideoError):
        video.key_frames(clips["blank"])


def test_clip_of_two_scenes(clips):
    r = video.compare(clips["original"], clips["clip"])
    assert r["verdict"] == "copy" and r["label"] == "Contains your video"
    assert r["suspect_match_pct"] == 100.0 and 45 <= r["original_coverage_pct"] <= 55
    assert len(r["segments"]) == 1
    seg = r["segments"][0]
    assert seg["suspect_start"] == 0.0 and abs(seg["original_start"] - 4.0) <= 0.5 and seg["speed"] is None
    assert r["flags"] == [] and r["evidence"]
    assert r["pixel_check"] == "Exact / re-compressed copy"


def test_mirrored(clips):
    r = video.compare(clips["original"], clips["mirrored"])
    assert r["verdict"] == "copy" and video.FLAG_TEXT["mirrored"] in r["flags"]
    assert "inconclusive" not in r["pixel_check"]  # the flip is undone before the pixel check


def test_vertical_repost(clips):
    r = video.compare(clips["original"], clips["vertical"])
    assert r["verdict"] == "copy" and video.FLAG_TEXT["vertical"] in r["flags"]


def test_sped_up(clips):
    r = video.compare(clips["original"], clips["fast"])
    assert r["verdict"] == "copy"
    assert any(f.startswith("Played at about 2") for f in r["flags"])


def test_unrelated_and_blank(clips):
    r = video.compare(clips["original"], clips["unrelated"])
    assert r["verdict"] == "no_match" and r["segments"] == []
    r = video.compare(clips["original"], clips["blank"])
    assert r["verdict"] == "no_match"


def test_scan_with_video_original(clips, monkeypatch):
    """A still of one scene, found online, matches the video and records which frame it matched."""
    still = io.BytesIO()
    Image.fromarray(cv2.cvtColor(clips["scenes"][2][15], cv2.COLOR_BGR2RGB)).save(still, "JPEG", quality=85)
    other = io.BytesIO()
    Image.fromarray(cv2.cvtColor(g.scene_frames(g.photos(OTHER)[0], 1)[0], cv2.COLOR_BGR2RGB)).save(other, "JPEG")
    cands = [{"image_url": "demo://still", "page_url": "https://reposts.example.com/v", "title": "ad still",
              "source": "test", "blob": still.getvalue()},
             {"image_url": "demo://other", "page_url": "https://other.example.com/x", "title": "other",
              "source": "test", "blob": other.getvalue()}]

    async def fake_discover(*a, **k): return cands
    async def none(*a, **k): return []
    monkeypatch.setattr(search, "discover", fake_discover)
    monkeypatch.setattr(search, "reverse_candidates", none)

    async def go():
        return [e async for e in pipeline.run_scan(clips["original"], "Acme", [], [], semantic_search=True)]
    ev = asyncio.run(go())
    kf = [d for e, d in ev if e == "keyframes"]
    assert kf and len(kf[0]["frames"]) >= 4
    assert any("off for videos" in d.get("message", "") for e, d in ev if e == "status")
    matches = [d for e, d in ev if e == "match"]
    assert [m["title"] for m in matches] == ["ad still"]
    t = matches[0]["ref_frame"]["t"]
    assert 7.0 <= t <= 10.0  # scene 3 runs from 7 s to 10 s
    sess = SESSIONS[kf[0]["scan_id"]]
    assert len(sess["frames"]) == len(kf[0]["frames"])
    assert original_for(sess, matches[0]) == sess["frames"][matches[0]["ref_frame"]["idx"]]


def test_compare_endpoint_uploads(clips):
    c = TestClient(app)
    r = c.post("/api/video/compare", files={"original": ("a.mp4", clips["original"], "video/mp4"),
                                             "suspect": ("b.mp4", clips["clip"], "video/mp4")})
    assert r.status_code == 200
    j = r.json()
    assert j["verdict"] == "copy" and j["suspect_source"] == "upload" and j["suspect_url"] is None
    r = c.post("/api/video/compare", files={"original": ("a.mp4", clips["original"], "video/mp4")})
    assert r.status_code == 400
    r = c.post("/api/video/compare", files={"original": ("a.jpg", b"\xff\xd8\xff\xe0notvideo", "image/jpeg"),
                                             "suspect": ("b.mp4", clips["clip"], "video/mp4")})
    assert r.status_code == 422 and "not a video" in r.json()["detail"]


def test_compare_endpoint_link(clips, monkeypatch):
    async def fake_fetch(url):
        if "instagram" in url:
            raise video_fetch.FetchError(video_fetch._friendly("ERROR: login required to view this post"))
        return clips["mirrored"]
    monkeypatch.setattr(video_fetch, "fetch", fake_fetch)
    c = TestClient(app)
    r = c.post("/api/video/compare", files={"original": ("a.mp4", clips["original"], "video/mp4")},
               data={"suspect_url": "https://www.tiktok.com/@x/video/1"})
    assert r.status_code == 200 and r.json()["suspect_source"] == "link" and r.json()["verdict"] == "copy"
    r = c.post("/api/video/compare", files={"original": ("a.mp4", clips["original"], "video/mp4")},
               data={"suspect_url": "https://www.instagram.com/reel/abc/"})
    assert r.status_code == 422 and "upload the file" in r.json()["detail"]


def test_fetch_rejects_bad_links():
    for url, msg in (("ftp://x.example/v.mp4", "https://"), ("not a link", "https://"),
                     ("http://127.0.0.1/v.mp4", "public web address"), ("http://192.168.1.5/v", "public web address")):
        with pytest.raises(video_fetch.FetchError, match=msg):
            asyncio.run(video_fetch.fetch(url))


def test_friendly_errors():
    assert "upload the file" in video_fetch._friendly("ERROR: [Instagram] abc: Requested content is not available, rate-limit reached or login required")
    assert "deleted or made private" in video_fetch._friendly(
        "ERROR: [Instagram] DWHw5YvkXBW: Instagram sent an empty media response. Check if this post is accessible "
        "in your browser without being logged-in. If it is not, then use --cookies-from-browser")
    assert video_fetch._friendly("ERROR: Private video") == "That video is private."
    assert "not a video page" in video_fetch._friendly("ERROR: Unsupported URL: https://example.com")
    assert "longer than" in video_fetch._friendly("does not pass filter (duration <=? 600)")


def test_title_cards_are_not_key_frames():
    """A logo on a plain background (title / end card) is skipped, unless the video is nothing but cards."""
    ims = g.photos(SCENES[:2])
    if not ims:
        pytest.skip("real evaluation photos not downloaded (data/real_eval)")
    card = np.full((g.H, g.W, 3), 30, np.uint8)
    cv2.putText(card, "ACME", (150, 150), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 5)
    clip = g.encode([card] * 20 + [f for im in ims for f in g.scene_frames(im, 3)] + [card] * 20)
    kf = video.key_frames(clip)
    assert kf and all(2.0 <= f["t"] < 8.0 for f in kf)  # only the two real scenes (2 s to 8 s)
    only_cards = video.key_frames(g.encode([card] * 30))
    assert len(only_cards) >= 1
