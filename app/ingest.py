"""Turn user uploads into visual evidence the scan pipeline can inspect.

File extensions are hints only. The content is sniffed so renamed image and video files still work.
Archives are read in memory and never extracted to disk.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from PIL import Image

from . import video


MAX_UPLOAD_BYTES = 250_000_000
MAX_ARCHIVE_MEMBER_BYTES = 200_000_000
MAX_ARCHIVE_ENTRIES = 2_000
MAX_ARCHIVE_TOTAL_BYTES = 600_000_000


class IngestError(ValueError):
    """A user-facing upload error."""


@dataclass(frozen=True)
class PreparedUpload:
    data: bytes
    filename: str
    note: str


def direct_image_url(url: str) -> str:
    """Return a URL only when it clearly names image media, not a social post page."""
    url = (url or "").strip()
    return url if re.search(r"\.(?:avif|gif|heic|heif|jpe?g|png|webp)(?:\?|#|$)", url, re.I) else ""


def _is_image(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


def _pdf_page(data: bytes, filename: str) -> PreparedUpload:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - dependency is installed from requirements.txt in production
        raise IngestError("PDF support is not installed on this server.") from exc
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.page_count < 1:
                raise IngestError("That PDF has no pages.")
            page = document[0]
            longest = max(float(page.rect.width), float(page.rect.height), 1.0)
            scale = max(0.1, min(2.0, 1600.0 / longest))
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
            rendered = pixmap.tobytes("jpeg", jpg_quality=90)
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError("Could not read that PDF.") from exc
    return PreparedUpload(rendered, filename, f"Using page 1 of {filename or 'the PDF'}")


def _archive(data: bytes, filename: str, depth: int) -> PreparedUpload:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir() and not item.flag_bits & 0x1]
            if len(members) > MAX_ARCHIVE_ENTRIES:
                raise IngestError(f"That archive contains more than {MAX_ARCHIVE_ENTRIES:,} files.")
            if sum(item.file_size for item in members) > MAX_ARCHIVE_TOTAL_BYTES:
                raise IngestError("That archive expands beyond the safe processing limit.")
            usable = [item for item in members if 0 < item.file_size <= MAX_ARCHIVE_MEMBER_BYTES]
            # Office files are ZIP containers. The largest media item is usually the useful visual, while
            # XML, thumbnails and relationship files naturally fall to the end of this ordering.
            visual_ext = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic",
                          ".heif", ".avif", ".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv", ".pdf"}
            usable.sort(key=lambda item: (PurePosixPath(item.filename).suffix.lower() in visual_ext, item.file_size),
                        reverse=True)
            for item in usable:
                try:
                    with archive.open(item) as source:
                        member = source.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
                    if len(member) > MAX_ARCHIVE_MEMBER_BYTES:
                        continue
                    prepared = prepare_upload(member, item.filename, depth=depth + 1)
                    return PreparedUpload(prepared.data, item.filename,
                                          f"Using {item.filename} from {filename or 'the archive'}")
                except (IngestError, NotImplementedError, RuntimeError, zipfile.BadZipFile):
                    continue
    except IngestError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise IngestError("Could not read that archive.") from exc
    raise IngestError("No readable image, video or PDF was found in that archive.")


def prepare_upload(data: bytes, filename: str = "", *, depth: int = 0) -> PreparedUpload:
    """Return image or video bytes suitable for pipeline.run_scan()."""
    if not data:
        raise IngestError("That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(f"That file is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB.")
    if video.is_video(data):
        return PreparedUpload(data, filename, f"Using video {filename}" if filename else "Using uploaded video")
    if _is_image(data):
        return PreparedUpload(data, filename, f"Using image {filename}" if filename else "Using uploaded image")
    if data.startswith(b"%PDF-"):
        return _pdf_page(data, filename)
    if depth < 2 and zipfile.is_zipfile(io.BytesIO(data)):
        return _archive(data, filename, depth)
    raise IngestError("This file has no readable visual content. Upload an image, video, PDF, ZIP, or Office file containing media.")
