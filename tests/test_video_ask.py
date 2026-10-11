"""Step 9: a video (a screen recording, a saved reel) is read like a screenshot."""
import asyncio
import os
import tempfile

import cv2
import numpy as np
import pytest

from app import ask, listing, video


def _clip(screens: list[str | None], seconds_each: int = 2, fps: int = 5) -> bytes:
    """An MP4 showing each screen (a line of text, or None for black) for a few seconds."""
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (720, 1280))
    for screen in screens:
        frame = np.zeros((1280, 720, 3), np.uint8)
        if screen is not None:
            frame[:] = 255
            cv2.putText(frame, screen, (40, 640), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 4)
            cv2.rectangle(frame, (30, 200), (690, 500), (40, 90, 200), -1)  # a product photo stand-in
        for _ in range(seconds_each * fps):
            out.write(frame)
    out.release()
    data = open(path, "rb").read()
    os.remove(path)
    return data


def test_a_video_is_recognised():
    clip = _clip(["luxe-kicks.store"])
    if not video.is_video(clip):
        pytest.skip("this OpenCV build can't write MP4")
    assert video.is_video(clip) and not video.is_video(b"\x89PNG\r\n\x1a\n" + b"0" * 32)


def test_frames_are_distinct_and_never_blank():
    clip = _clip([None, "luxe-kicks.store", "luxe-kicks.store", "DM to order, Zelle only"])
    frames = ask.video_frames(clip)
    assert len(frames) == 2  # black skipped; the same screen held for 4 seconds counted once
    assert max(frames[0].size) == 1280  # read at full phone-screen size, not shrunk to a 640 thumbnail


def test_many_screens_are_spread_evenly_up_to_the_limit():
    clip = _clip([f"screen {i} " + "x" * (i % 7) for i in range(14)], seconds_each=1)
    frames = ask.video_frames(clip, limit=4)
    assert len(frames) == 4


def test_a_video_goes_through_the_screenshot_path(monkeypatch):
    clip = _clip(["luxe-kicks.store"])
    monkeypatch.setattr(ask, "_read", lambda img: ["Fresh pairs", "luxe-kicks.store", "DM to order"])
    checked = {}

    async def check(url, brand="", seen_text="", **kw):
        checked.update(url=url, seen=seen_text)
        return {"verdict": "red", "domain": "luxe-kicks.store", "url": url}

    async def nothing(result): return {}
    monkeypatch.setattr(listing, "check_url", check)
    monkeypatch.setattr(ask, "extras", nothing)
    r = asyncio.run(ask.ask("", clip))
    assert checked["url"] == "luxe-kicks.store" and "DM to order" in checked["seen"]
    assert r["found"]["how"] == "video"


def test_a_video_with_nothing_to_check_says_video(monkeypatch):
    monkeypatch.setattr(ask, "_read", lambda img: ["nice shoes"])
    with pytest.raises(listing.ListingError, match="in that video"):
        asyncio.run(ask.ask("", _clip(["nice shoes"])))


def test_a_broken_video_asks_for_a_screenshot_instead():
    with pytest.raises(listing.ListingError, match="Send a screenshot or the link instead"):
        ask.video_frames(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)


def test_a_pasted_file_path_is_never_a_store_name(monkeypatch):
    async def search(*a, **k):
        raise AssertionError("a file path must not be searched as a store name")
    monkeypatch.setattr(listing, "web_search", search)
    for path in (r'"D:\brand-guard-samples\test-recording.mp4"', "C:/Users/me/shot.png", "/Users/me/Desktop/a.mov"):
        with pytest.raises(listing.ListingError, match="file on your computer"):
            asyncio.run(ask.ask(path))
