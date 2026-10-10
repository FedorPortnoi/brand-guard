#!/usr/bin/env python3
"""Create realistic synthetic product images for threshold calibration."""
import io
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
import numpy as np

OUTPUT_DIR = Path(__file__).parent / "synthetic"
OUTPUT_DIR.mkdir(exist_ok=True)

def create_sneaker_image(color_main, color_accent, variant=0):
    """Create a synthetic sneaker/shoe image."""
    img = Image.new("RGB", (512, 512), (240, 240, 240))
    draw = ImageDraw.Draw(img, "RGBA")

    # Main shoe body (side view)
    shoe_x = [80, 150, 200, 400, 450, 420, 350, 250, 100, 80]
    shoe_y = [200, 160, 140, 140, 160, 280, 320, 340, 320, 200]
    draw.polygon(list(zip(shoe_x, shoe_y)), fill=color_main, outline=(0, 0, 0, 255))

    # Shoe sole
    sole_y = 320
    draw.rectangle([80, sole_y, 450, sole_y+40], fill=(50, 50, 50), outline=(0, 0, 0, 255))

    # Accent stripe or detail
    if variant % 3 == 0:
        draw.rectangle([150, 200, 160, 300], fill=color_accent, outline=(0, 0, 0, 255))
    elif variant % 3 == 1:
        draw.ellipse([200, 160, 350, 240], fill=color_accent, outline=(0, 0, 0, 255))
    else:
        draw.rectangle([250, 240, 450, 260], fill=color_accent, outline=(0, 0, 0, 255))

    # Add some texture via noise
    arr = np.array(img, dtype=np.float32)
    noise = np.random.normal(0, 8, arr.shape)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)

    return img


def create_handbag_image(color_main, color_accent, variant=0):
    """Create a synthetic handbag image."""
    img = Image.new("RGB", (512, 512), (245, 245, 245))
    draw = ImageDraw.Draw(img, "RGBA")

    # Bag body
    draw.rectangle([120, 180, 400, 400], fill=color_main, outline=(0, 0, 0, 255))

    # Handles
    draw.arc([150, 100, 370, 200], 0, 180, fill=(0, 0, 0), width=15)

    # Flap or accent
    if variant % 2 == 0:
        draw.rectangle([150, 160, 370, 190], fill=color_accent, outline=(0, 0, 0, 255))
    else:
        draw.polygon([[150, 160], [370, 160], [360, 190], [160, 190]], fill=color_accent, outline=(0, 0, 0, 255))

    # Add texture
    arr = np.array(img, dtype=np.float32)
    noise = np.random.normal(0, 8, arr.shape)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)

    return img


def create_watch_image(case_color, band_color, variant=0):
    """Create a synthetic watch image."""
    img = Image.new("RGB", (512, 512), (240, 240, 240))
    draw = ImageDraw.Draw(img, "RGBA")

    # Case
    draw.ellipse([180, 150, 380, 350], fill=case_color, outline=(0, 0, 0, 255))

    # Band
    band_width = 40 if variant % 2 == 0 else 50
    draw.rectangle([160, 350, 400, 350+band_width], fill=band_color, outline=(0, 0, 0, 255))
    draw.rectangle([160, 150-band_width, 400, 150], fill=band_color, outline=(0, 0, 0, 255))

    # Watch face details
    center = (280, 250)
    draw.ellipse([center[0]-70, center[1]-70, center[0]+70, center[1]+70], fill=(200, 200, 200), outline=(0, 0, 0, 255))
    draw.line([center, (center[0], center[1]-40)], fill=(0, 0, 0), width=3)  # hour hand
    draw.line([center, (center[0]+35, center[1])], fill=(0, 0, 0), width=2)  # minute hand

    # Add texture
    arr = np.array(img, dtype=np.float32)
    noise = np.random.normal(0, 8, arr.shape)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)

    return img


def augment_image(img, aug_type):
    """Apply realistic counterfeit transformations."""
    if aug_type == "perspective":
        # Slight perspective shift
        w, h = img.size
        coeffs = [1.0 + random.uniform(-0.1, 0.1), 0, random.uniform(-20, 20),
                  0, 1.0 + random.uniform(-0.1, 0.1), random.uniform(-20, 20),
                  random.uniform(-0.0001, 0.0001), random.uniform(-0.0001, 0.0001)]
        img = img.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    elif aug_type == "crop":
        # Crop (as in counterfeit photos)
        w, h = img.size
        crop_size = random.randint(int(w*0.7), int(w*0.9))
        x = random.randint(0, w - crop_size)
        y = random.randint(0, h - crop_size)
        img = img.crop((x, y, x+crop_size, y+crop_size))
        img = img.resize((w, h))
    elif aug_type == "color_shift":
        # Color shift (recoloring)
        arr = np.array(img, dtype=np.float32)
        shift = np.random.uniform(0.8, 1.2, (1, 1, 3))
        arr = np.clip(arr * shift, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
    elif aug_type == "compression":
        # JPEG recompression (typical for counterfeit photos)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=random.randint(60, 80))
        buf.seek(0)
        img = Image.open(buf).copy()
    elif aug_type == "watermark":
        # Add text/watermark overlay (common in counterfeits)
        img_copy = img.copy()
        draw = ImageDraw.Draw(img_copy)
        text = random.choice(["WHOLESALE", "FACTORY", "BEST PRICE", "FREE SHIP"])
        try:
            draw.text((200, 400), text, fill=(100, 100, 100))
        except:
            pass
        img = img_copy
    elif aug_type == "background":
        # Paste on different background
        bg = Image.new("RGB", img.size, random.choice([(200, 200, 200), (150, 150, 200), (200, 180, 150)]))
        # Simple paste with slight transparency effect
        img = Image.blend(bg, img, 0.9)

    return img


def save_image(img, filename):
    """Save as JPEG."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    path = OUTPUT_DIR / filename
    path.write_bytes(buf.getvalue())


def create_dataset():
    """Create realistic synthetic product dataset."""

    print("Creating realistic synthetic product images...")

    # SNEAKERS: Same model, different photos
    print("\n1. Creating sneaker positives (same product, different photos)...")
    shoe_color = (200, 100, 50)
    shoe_accent = (100, 200, 255)

    base_shoe = create_sneaker_image(shoe_color, shoe_accent, variant=0)
    save_image(base_shoe, "pos_sneaker_000_original.jpg")

    augmentations = ["perspective", "crop", "color_shift", "compression", "watermark", "background"]
    for i, aug in enumerate(augmentations, 1):
        aug_shoe = augment_image(base_shoe, aug)
        save_image(aug_shoe, f"pos_sneaker_{i:03d}_{aug}.jpg")

    # SNEAKERS: Different products (hard negatives - same category)
    print("2. Creating sneaker hard negatives (different shoes)...")
    hard_neg_configs = [
        ((100, 50, 200), (255, 200, 100)),
        ((50, 150, 100), (255, 100, 50)),
        ((150, 100, 50), (100, 100, 200)),
    ]

    for i, (main, accent) in enumerate(hard_neg_configs):
        shoe = create_sneaker_image(main, accent, variant=i)
        save_image(shoe, f"hard_neg_sneaker_{i:03d}_variant.jpg")

    # HANDBAGS: Same model, different photos
    print("3. Creating handbag positives (same product, different photos)...")
    bag_color = (139, 69, 19)
    bag_accent = (184, 134, 11)

    base_bag = create_handbag_image(bag_color, bag_accent, variant=0)
    save_image(base_bag, "pos_handbag_000_original.jpg")

    for i, aug in enumerate(augmentations, 1):
        aug_bag = augment_image(base_bag, aug)
        save_image(aug_bag, f"pos_handbag_{i:03d}_{aug}.jpg")

    # HANDBAGS: Different products (hard negatives)
    print("4. Creating handbag hard negatives (different bags)...")
    bag_configs = [
        ((200, 100, 100), (100, 200, 100)),
        ((100, 100, 200), (200, 100, 200)),
    ]

    for i, (main, accent) in enumerate(bag_configs):
        bag = create_handbag_image(main, accent, variant=i)
        save_image(bag, f"hard_neg_handbag_{i:03d}_variant.jpg")

    # WATCHES: Same model, different photos
    print("5. Creating watch positives (same product, different photos)...")
    watch_case = (200, 150, 100)
    watch_band = (50, 50, 50)

    base_watch = create_watch_image(watch_case, watch_band, variant=0)
    save_image(base_watch, "pos_watch_000_original.jpg")

    for i, aug in enumerate(augmentations, 1):
        aug_watch = augment_image(base_watch, aug)
        save_image(aug_watch, f"pos_watch_{i:03d}_{aug}.jpg")

    # WATCHES: Different products (hard negatives)
    print("6. Creating watch hard negatives (different watches)...")
    watch_configs = [
        ((100, 150, 200), (100, 100, 100)),
        ((200, 200, 100), (200, 100, 100)),
    ]

    for i, (case, band) in enumerate(watch_configs):
        watch = create_watch_image(case, band, variant=i)
        save_image(watch, f"hard_neg_watch_{i:03d}_variant.jpg")

    # EASY NEGATIVES: Cross-category
    print("7. Creating easy negatives (cross-category)...")

    # Shoe vs bag
    shoe = create_sneaker_image((200, 50, 50), (100, 200, 100), variant=0)
    save_image(shoe, "easy_neg_001_shoe.jpg")

    # Bag vs watch
    bag = create_handbag_image((100, 100, 100), (200, 200, 200), variant=0)
    save_image(bag, "easy_neg_002_bag.jpg")

    # Watch vs shoe
    watch = create_watch_image((150, 150, 150), (50, 50, 50), variant=0)
    save_image(watch, "easy_neg_003_watch.jpg")

    print(f"\nDataset created in {OUTPUT_DIR}")
    print(f"Positives: 18 images (6 products × 3 variants)")
    print(f"Hard negatives: 6 images (3 categories × 2 different products)")
    print(f"Easy negatives: 3 images (cross-category)")
    print(f"Total: 27 images")


if __name__ == "__main__":
    create_dataset()
