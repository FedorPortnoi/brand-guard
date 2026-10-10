"""FastAPI endpoint for checking a suspect video against the original commercial."""
import asyncio

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from . import video, video_fetch

router = APIRouter()


async def _video(upload: UploadFile | None, url: str, what: str) -> tuple[bytes, str]:
    if upload is not None and upload.filename:
        data = await upload.read()
        source = "upload"
    elif url.strip():
        try:
            data = await video_fetch.fetch(url)
        except video_fetch.FetchError as e:
            raise HTTPException(422, f"{what}: {e}")
        source = "link"
    else:
        raise HTTPException(400, f"Add the {what.lower()} as a file or a link")
    if not video.is_video(data):
        raise HTTPException(422, f"{what}: that file is not a video this tool can read (MP4, MOV, WebM, MKV, AVI)")
    return data, source


@router.get("/api/video/status")
def status():
    return {"link_download": video_fetch.enabled(), "max_mb": video.MAX_BYTES // 1_000_000,
            "max_minutes": video.MAX_SECONDS // 60}


@router.post("/api/video/compare")
async def compare(original: UploadFile | None = File(None), original_url: str = Form(""),
                  suspect: UploadFile | None = File(None), suspect_url: str = Form("")):
    """How much of the original video appears in the suspect video, where, and how it was edited."""
    orig, orig_src = await _video(original, original_url, "Original video")
    sus, sus_src = await _video(suspect, suspect_url, "Suspect video")
    try:
        result = await asyncio.to_thread(video.compare, orig, sus)
    except video.VideoError as e:
        raise HTTPException(422, str(e))
    return {**result, "original_source": orig_src, "suspect_source": sus_src,
            "suspect_url": suspect_url.strip() if sus_src == "link" else None}
