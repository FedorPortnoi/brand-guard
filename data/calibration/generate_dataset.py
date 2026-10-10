#!/usr/bin/env python
"""Generate labelled calibration dataset for forensics thresholds."""
import io
import json
from pathlib import Path
from PIL import Image, ImageDraw
import httpx

# Working directory
CALIB_DIR = Path(__file__).parent
SOURCES = CALIB_DIR / "SOURCES.md"
LABELS = CALIB_DIR / "labels.json"

def download_image(url: str, name: str) -> bytes:
    """Download an image with a normal User-Agent."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = httpx.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.content
    (CALIB_DIR / f"{name}.jpg").write_bytes(data)
    print(f"Downloaded {name}: {len(data)} bytes")
    return data

def gen_logo_stripes() -> bytes:
    """Simple two-stripe logo."""
    im = Image.new("RGB", (300, 200), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([20, 50, 120, 150], fill="black")
    d.rectangle([160, 50, 260, 150], fill="darkblue")
    d.text((80, 160), "REAL BRAND", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_logo_circle() -> bytes:
    """Circular logo."""
    im = Image.new("RGB", (300, 300), "white")
    d = ImageDraw.Draw(im)
    d.ellipse([50, 50, 250, 250], fill="red", outline="darkred", width=3)
    d.text((100, 130), "CIRCLE", fill="white")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_logo_chevron() -> bytes:
    """Chevron / tick mark."""
    im = Image.new("RGB", (280, 280), "white")
    d = ImageDraw.Draw(im)
    d.polygon([(80, 180), (140, 100), (200, 180)], fill="green", outline="darkgreen")
    d.text((100, 210), "APEX", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_shoe_photo() -> bytes:
    """Simple shoe-like product photo (gray shoe on white)."""
    im = Image.new("RGB", (400, 400), "white")
    d = ImageDraw.Draw(im)
    d.polygon([(80, 250), (150, 150), (250, 150), (320, 250)], fill="gray", outline="black", width=2)
    d.ellipse([70, 220, 330, 280], fill="darkgray")
    d.text((150, 300), "SHOE MODEL A", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_shoe_photo_b() -> bytes:
    """Different shoe."""
    im = Image.new("RGB", (400, 400), "white")
    d = ImageDraw.Draw(im)
    d.polygon([(100, 260), (180, 140), (240, 140), (300, 260)], fill="navy", outline="black", width=2)
    d.ellipse([80, 230, 320, 290], fill="darkblue")
    d.text((130, 310), "SHOE MODEL B", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_shoe_photo_c() -> bytes:
    """Completely different shoe - boot style, not on white."""
    im = Image.new("RGB", (400, 500), "lightgray")
    d = ImageDraw.Draw(im)
    # Draw a tall boot
    d.polygon([(100, 150), (150, 50), (250, 50), (300, 150), (280, 400), (120, 400)], fill="black", outline="darkgray", width=2)
    d.ellipse([120, 380, 280, 420], fill="gray")  # sole
    d.text((120, 430), "BOOT PRO", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_acme_logo() -> bytes:
    """The ACME SPORT two-stripe logo that falsely matched."""
    im = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(im)
    d.polygon([(60, 220), (160, 80), (200, 80), (100, 220)], fill="black")
    d.polygon([(140, 220), (240, 80), (280, 80), (180, 220)], fill="black")
    d.text((150, 250), "ACME SPORT", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_grid_page() -> bytes:
    """Busy product grid (for hard negatives)."""
    im = Image.new("RGB", (600, 600), "lightgray")
    d = ImageDraw.Draw(im)
    for i in range(3):
        for j in range(3):
            x = 30 + i * 200
            y = 30 + j * 200
            d.rectangle([x, y, x + 160, y + 160], fill="white", outline="black")
            d.text((x + 40, y + 70), f"SKU {i*3+j}", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()

def gen_similar_stripes(name: str) -> bytes:
    """Similar to stripes but different arrangement."""
    im = Image.new("RGB", (300, 200), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([30, 60, 100, 140], fill="darkgreen")
    d.rectangle([150, 60, 220, 140], fill="lightgreen")
    d.text((80, 160), name, fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_circles_pattern() -> bytes:
    """Multiple circles pattern."""
    im = Image.new("RGB", (300, 300), "white")
    d = ImageDraw.Draw(im)
    for i in range(3):
        x = 50 + i * 80
        d.ellipse([x, 80, x + 60, 140], fill="blue", outline="darkblue", width=2)
    d.text((80, 200), "DOTS", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_triangle_tick() -> bytes:
    """Triangle/tick, similar to chevron but different."""
    im = Image.new("RGB", (280, 280), "white")
    d = ImageDraw.Draw(im)
    d.polygon([(140, 80), (220, 200), (60, 200)], fill="purple", outline="indigo", width=2)
    d.text((100, 230), "DELTA", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def gen_diagonal_stripes() -> bytes:
    """Diagonal lines pattern."""
    im = Image.new("RGB", (300, 250), "white")
    d = ImageDraw.Draw(im)
    for i in range(6):
        x = i * 50
        d.line([(x, 0), (x + 100, 250)], fill="orange", width=3)
    d.text((80, 220), "STRIPE", fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def save_original(data: bytes, name: str) -> str:
    """Save original and return its relative path."""
    path = CALIB_DIR / f"{name}.jpg"
    path.write_bytes(data)
    return f"{name}.jpg"

def gen_exact(orig: bytes, name: str) -> str:
    """Exact recompression."""
    path = CALIB_DIR / f"{name}_exact.jpg"
    img = Image.open(io.BytesIO(orig))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    path.write_bytes(buf.getvalue())
    return f"{name}_exact.jpg"

def gen_modified(orig: bytes, name: str) -> str:
    """Resize + recolour."""
    path = CALIB_DIR / f"{name}_modified.jpg"
    img = Image.open(io.BytesIO(orig))
    img = img.resize((int(img.width * 0.8), int(img.height * 0.8)))
    # Light brightness shift
    from PIL import ImageEnhance
    img = ImageEnhance.Brightness(img).enhance(1.1)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    path.write_bytes(buf.getvalue())
    return f"{name}_modified.jpg"

def gen_cropped(orig: bytes, name: str) -> str:
    """Crop to 70-85% and reframe."""
    path = CALIB_DIR / f"{name}_cropped.jpg"
    img = Image.open(io.BytesIO(orig))
    w, h = img.size
    crop_w, crop_h = int(w * 0.75), int(h * 0.75)
    left = (w - crop_w) // 4
    top = (h - crop_h) // 4
    cropped = img.crop((left, top, left + crop_w, top + crop_h))
    # Embed in larger canvas with white border
    canvas = Image.new("RGB", (w, h), "white")
    canvas.paste(cropped, ((w - crop_w) // 2, (h - crop_h) // 2))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=90)
    path.write_bytes(buf.getvalue())
    return f"{name}_cropped.jpg"

def gen_altered(orig: bytes, name: str) -> str:
    """Blur/blur some region."""
    import cv2
    import numpy as np
    path = CALIB_DIR / f"{name}_altered.jpg"
    img = Image.open(io.BytesIO(orig))
    arr = np.array(img)
    h, w = arr.shape[:2]
    # Blur center region
    arr[h//4:3*h//4, w//4:3*w//4] = cv2.GaussianBlur(arr[h//4:3*h//4, w//4:3*w//4], (31, 31), 0)
    result = Image.fromarray(arr)
    buf = io.BytesIO()
    result.save(buf, "JPEG", quality=90)
    path.write_bytes(buf.getvalue())
    return f"{name}_altered.jpg"

def gen_embedded_small(orig: bytes, name: str) -> str:
    """Logo embedded small in a busy scene."""
    import cv2
    import numpy as np
    path = CALIB_DIR / f"{name}_embedded.jpg"

    # Create a busy background
    bg = Image.new("RGB", (800, 600), "lightcyan")
    d = ImageDraw.Draw(bg)
    for i in range(10):
        d.rectangle([i*70, 50, i*70+50, 150], fill=("red" if i%2 else "blue"))

    # Shrink original and paste
    orig_img = Image.open(io.BytesIO(orig))
    orig_img.thumbnail((200, 200))
    bg.paste(orig_img, (300, 200))

    buf = io.BytesIO()
    bg.save(buf, "JPEG", quality=90)
    path.write_bytes(buf.getvalue())
    return f"{name}_embedded.jpg"

def gen_various_quality(orig: bytes, name: str, quality: int) -> str:
    """Recompress at different quality."""
    path = CALIB_DIR / f"{name}_q{quality}.jpg"
    img = Image.open(io.BytesIO(orig))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    path.write_bytes(buf.getvalue())
    return f"{name}_q{quality}.jpg"

def gen_rotated(orig: bytes, name: str, angle: float) -> str:
    """Rotate slightly."""
    path = CALIB_DIR / f"{name}_rot.jpg"
    img = Image.open(io.BytesIO(orig))
    img = img.rotate(angle, expand=False)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    path.write_bytes(buf.getvalue())
    return f"{name}_rot.jpg"

def main():
    """Generate full dataset."""
    labels = []
    sources = ["# Calibration Dataset Sources\n\n"]

    # Originals: 8 varied images
    print("Generating originals...")

    # Logos
    orig_stripes = gen_logo_stripes()
    save_original(orig_stripes, "logo_stripes")
    sources.append("- logo_stripes.jpg: Generated simple two-stripe logo\n")

    orig_circle = gen_logo_circle()
    save_original(orig_circle, "logo_circle")
    sources.append("- logo_circle.jpg: Generated circular logo\n")

    orig_chevron = gen_logo_chevron()
    save_original(orig_chevron, "logo_chevron")
    sources.append("- logo_chevron.jpg: Generated chevron/tick logo\n")

    # Product photos
    orig_shoe_a = gen_shoe_photo()
    save_original(orig_shoe_a, "shoe_a")
    sources.append("- shoe_a.jpg: Generated shoe photo A\n")

    orig_shoe_b = gen_shoe_photo_b()
    save_original(orig_shoe_b, "shoe_b")
    sources.append("- shoe_b.jpg: Generated shoe photo B (different model)\n")

    orig_shoe_c = gen_shoe_photo_c()
    save_original(orig_shoe_c, "shoe_c")
    sources.append("- shoe_c.jpg: Generated shoe photo C (different style)\n")

    # The ACME logo for reproduction
    orig_acme = gen_acme_logo()
    save_original(orig_acme, "acme_logo")
    sources.append("- acme_logo.jpg: ACME SPORT logo (two-stripe)\n")

    # Busy grid page
    orig_grid = gen_grid_page()
    save_original(orig_grid, "grid_page")
    sources.append("- grid_page.jpg: Generated product grid page\n")

    # Download real wholesale shoes
    print("Downloading wholesale shoes image...")
    try:
        shoes_data = download_image(
            "https://i0.wp.com/thecleverbusiness.com/wp-content/uploads/2022/12/wholesale55-wholesale-adidas-shoes-sneakers-supplier.jpg?w=1200&ssl=1",
            "wholesale_shoes"
        )
        sources.append("- wholesale_shoes.jpg: Wholesale Adidas shoes (external source)\n")
    except Exception as e:
        print(f"Warning: Could not download wholesale shoes: {e}")
        sources.append("- wholesale_shoes.jpg: (failed to download, not available)\n")
        shoes_data = None

    # Additional logos
    similar_stripes = gen_similar_stripes("GREEN BRAND")
    save_original(similar_stripes, "logo_stripes_alt")
    sources.append("- logo_stripes_alt.jpg: Generated alternative two-stripe logo\n")

    circles = gen_circles_pattern()
    save_original(circles, "logo_circles")
    sources.append("- logo_circles.jpg: Generated multi-circle logo\n")

    triangle = gen_triangle_tick()
    save_original(triangle, "logo_triangle")
    sources.append("- logo_triangle.jpg: Generated triangle logo\n")

    diag = gen_diagonal_stripes()
    save_original(diag, "logo_diagonal")
    sources.append("- logo_diagonal.jpg: Generated diagonal stripes logo\n")

    print("\nGenerating positives (related=True)...")
    # Positives: exact, modified, cropped, altered for each
    for orig_name, orig_data in [
        ("logo_stripes", orig_stripes),
        ("logo_circle", orig_circle),
        ("shoe_a", orig_shoe_a),
        ("shoe_b", orig_shoe_b),
        ("logo_stripes_alt", similar_stripes),
        ("logo_circles", circles),
        ("logo_triangle", triangle),
        ("logo_chevron", orig_chevron),
    ]:
        # Exact (multiple quality levels)
        for q in [85, 90, 95]:
            labels.append({
                "original": f"{orig_name}.jpg",
                "candidate": gen_various_quality(orig_data, orig_name, q),
                "related": True,
                "kind": "exact",
                "note": f"Exact recompression (quality {q})"
            })
        # Modified (3 variations)
        for i in range(3):
            labels.append({
                "original": f"{orig_name}.jpg",
                "candidate": gen_modified(orig_data, f"{orig_name}_mod{i}" if i > 0 else orig_name),
                "related": True,
                "kind": "modified",
                "note": "Resized + recoloured"
            })
        # Cropped
        labels.append({
            "original": f"{orig_name}.jpg",
            "candidate": gen_cropped(orig_data, orig_name),
            "related": True,
            "kind": "cropped",
            "note": "Cropped and reframed"
        })
        # Altered
        labels.append({
            "original": f"{orig_name}.jpg",
            "candidate": gen_altered(orig_data, orig_name),
            "related": True,
            "kind": "altered",
            "note": "Same image, visibly edited (blurred region)"
        })
        # Embedded small
        labels.append({
            "original": f"{orig_name}.jpg",
            "candidate": gen_embedded_small(orig_data, orig_name),
            "related": True,
            "kind": "modified",
            "note": "Logo embedded small in larger scene"
        })
        # Rotated (slight rotation)
        labels.append({
            "original": f"{orig_name}.jpg",
            "candidate": gen_rotated(orig_data, orig_name, 3.0),
            "related": True,
            "kind": "modified",
            "note": "Slightly rotated"
        })

    print("\nGenerating negatives (related=False)...")
    # Hard negatives: similar geometry (cross all logos with each other)
    all_logos = [
        ("logo_stripes", "stripes"),
        ("logo_circle", "circle"),
        ("logo_chevron", "chevron"),
        ("logo_stripes_alt", "stripes_alt"),
        ("logo_circles", "circles"),
        ("logo_triangle", "triangle"),
        ("logo_diagonal", "diagonal"),
    ]

    all_shoes = [
        ("shoe_a", "A"),
        ("shoe_b", "B"),
        ("shoe_c", "C"),
    ]

    # Logo vs logo (all pairs)
    for i, (name1, desc1) in enumerate(all_logos):
        for name2, desc2 in all_logos[i+1:]:
            labels.append({
                "original": f"{name1}.jpg",
                "candidate": f"{name2}.jpg",
                "related": False,
                "kind": None,
                "note": f"Hard negative: {desc1} vs {desc2} logos"
            })

    # Shoe vs shoe (all pairs)
    for i, (name1, desc1) in enumerate(all_shoes):
        for name2, desc2 in all_shoes[i+1:]:
            labels.append({
                "original": f"{name1}.jpg",
                "candidate": f"{name2}.jpg",
                "related": False,
                "kind": None,
                "note": f"Hard negative: shoe {desc1} vs {desc2}"
            })

    # Shoe vs logo (all pairs, both directions)
    for shoe_name, shoe_desc in all_shoes:
        for logo_name, logo_desc in all_logos:
            labels.append({
                "original": f"{shoe_name}.jpg",
                "candidate": f"{logo_name}.jpg",
                "related": False,
                "kind": None,
                "note": f"Hard negative: shoe {shoe_desc} vs {logo_desc} logo"
            })
            labels.append({
                "original": f"{logo_name}.jpg",
                "candidate": f"{shoe_name}.jpg",
                "related": False,
                "kind": None,
                "note": f"Hard negative: {logo_desc} logo vs shoe {shoe_desc}"
            })

    # ACME vs wholesale shoes (the known false positive)
    if shoes_data:
        labels.append({
            "original": "acme_logo.jpg",
            "candidate": "wholesale_shoes.jpg",
            "related": False,
            "kind": None,
            "note": "Known false positive: ACME two-stripe logo vs wholesale shoes"
        })

    # Acme vs other logos
    for logo_name, logo_desc in all_logos:
        labels.append({
            "original": "acme_logo.jpg",
            "candidate": f"{logo_name}.jpg",
            "related": False,
            "kind": None,
            "note": f"Hard negative: ACME vs {logo_desc} logo"
        })

    # Grid page negatives
    for logo_name, logo_desc in all_logos[:3]:
        labels.append({
            "original": f"{logo_name}.jpg",
            "candidate": "grid_page.jpg",
            "related": False,
            "kind": None,
            "note": f"Hard negative: {logo_desc} logo vs busy product grid"
        })

    for shoe_name, shoe_desc in all_shoes[:2]:
        labels.append({
            "original": f"{shoe_name}.jpg",
            "candidate": "grid_page.jpg",
            "related": False,
            "kind": None,
            "note": f"Hard negative: shoe {shoe_desc} vs product grid"
        })

    print(f"\nGenerated {len(labels)} labelled pairs")
    print(f"Positives: {sum(1 for l in labels if l['related'])}")
    print(f"Negatives: {sum(1 for l in labels if not l['related'])}")

    # Save labels
    LABELS.write_text(json.dumps(labels, indent=2), encoding="utf-8")
    SOURCES.write_text("".join(sources), encoding="utf-8")

    print(f"\nSaved labels.json and SOURCES.md")

if __name__ == "__main__":
    main()
