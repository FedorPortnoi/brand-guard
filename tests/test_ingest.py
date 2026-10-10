import io
import zipfile

import pytest
from PIL import Image, ImageDraw

from app import ingest


def _jpg() -> bytes:
    image = Image.new("RGB", (320, 220), "white")
    ImageDraw.Draw(image).rectangle((45, 35, 270, 185), fill="#c92622")
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90)
    return out.getvalue()


def test_content_is_sniffed_even_with_img_extension():
    prepared = ingest.prepare_upload(_jpg(), "evidence.img")
    assert prepared.data.startswith(b"\xff\xd8")
    assert prepared.filename == "evidence.img"


def test_zip_and_office_style_archives_use_largest_visual():
    small = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(small, "PNG")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("word/document.xml", "<document />")
        out.writestr("word/media/thumbnail.png", small.getvalue())
        out.writestr("word/media/hero.jpg", _jpg())
    prepared = ingest.prepare_upload(archive.getvalue(), "campaign.docx")
    assert prepared.filename == "word/media/hero.jpg"
    assert "campaign.docx" in prepared.note


def test_non_visual_file_gets_clear_error():
    with pytest.raises(ingest.IngestError, match="no readable visual content"):
        ingest.prepare_upload(b"plain text only", "notes.txt")


def test_social_post_is_not_treated_as_direct_image_url():
    assert ingest.direct_image_url("https://www.tiktok.com/@brand/video/123") == ""
    assert ingest.direct_image_url("https://cdn.example.com/post/photo.JPG?size=large")


def test_pdf_renders_first_page_when_pymupdf_is_available():
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page(width=320, height=220)
    page.draw_rect((40, 40, 280, 180), color=(0.8, 0.15, 0.13), fill=(0.8, 0.15, 0.13))
    prepared = ingest.prepare_upload(document.tobytes(), "evidence.pdf")
    document.close()
    assert prepared.data.startswith(b"\xff\xd8")
    assert prepared.note == "Using page 1 of evidence.pdf"
