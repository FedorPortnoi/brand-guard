"""Download a public video from a link (Instagram, TikTok, YouTube and many other sites) with yt-dlp.

Experimental. Platforms often block downloads, and Instagram usually needs a login. Downloading can conflict
with a platform's terms, so use it only to check content you have a right to check; uploading a saved file
always works instead.
"""
import asyncio
import os
import tempfile
from urllib.parse import urlparse

from . import net, video

TIMEOUT = 120
# video only is enough for frame matching; prefer H.264 so OpenCV can always decode it
FORMAT = "bv*[vcodec^=avc1][height<=720]/bv*[ext=mp4][height<=720]/b[ext=mp4][height<=720]/bv*[height<=720]/b"


class FetchError(ValueError):
    pass


def enabled() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def _friendly(msg: str) -> str:
    m = msg.lower()
    if "empty media response" in m:  # yt-dlp's Instagram message for removed, private or login-only posts
        return ("Instagram returned no video for that link: the post may have been deleted or made private, "
                "or it may need a login. If it plays in your browser, save it and upload the file instead.")
    if any(k in m for k in ("login", "log in", "sign in", "cookies", "rate-limit", "rate limit")):
        return ("The platform wants a login before it allows downloads (Instagram usually does). "
                "Save the video yourself and upload the file instead.")
    if "private" in m:
        return "That video is private."
    if "unsupported url" in m or "no video" in m:
        return "That link is not a video page this tool can read."
    if "larger than max-filesize" in m or "file is larger" in m:
        return f"That video is larger than {video.MAX_BYTES // 1_000_000} MB."
    if "does not pass filter" in m:
        return f"That video is longer than {video.MAX_SECONDS // 60} minutes."
    return "Could not download that video: " + msg.strip().splitlines()[0][:160]


def _download(url: str) -> bytes:
    import yt_dlp
    with tempfile.TemporaryDirectory() as d:
        opts = {"format": FORMAT, "outtmpl": os.path.join(d, "v.%(ext)s"), "noplaylist": True, "quiet": True,
                "no_warnings": True, "noprogress": True, "max_filesize": video.MAX_BYTES, "socket_timeout": 20,
                "retries": 1, "match_filter": yt_dlp.utils.match_filter_func(f"duration <=? {video.MAX_SECONDS}")}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            raise FetchError(_friendly(str(e)))
        files = [os.path.join(d, f) for f in os.listdir(d) if not f.endswith((".part", ".ytdl"))]
        if not files:
            raise FetchError(_friendly("no video was downloaded (does not pass filter or no video)"))
        with open(max(files, key=os.path.getsize), "rb") as f:
            return f.read()


async def fetch(url: str) -> bytes:
    """Video bytes for a public video page. Raises FetchError with a message meant for the user."""
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("Enter a full link starting with https://")
    if not await asyncio.to_thread(net.safe_url, url):
        raise FetchError("That link is not a public web address.")
    if not enabled():
        raise FetchError("Downloading from links needs yt-dlp (pip install yt-dlp).")
    try:
        return await asyncio.wait_for(asyncio.to_thread(_download, url), TIMEOUT)
    except asyncio.TimeoutError:
        raise FetchError(f"The download took longer than {TIMEOUT} seconds.")
