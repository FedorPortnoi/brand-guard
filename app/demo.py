"""Self-made doctored copies, so a demo always has guaranteed catches. Always labelled as DEMO SAMPLES."""
import io

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


def _jpg(img: Image.Image, q=85) -> bytes:
    b = io.BytesIO(); img.convert("RGB").save(b, "JPEG", quality=q); return b.getvalue()


def doctored_copies(original: bytes) -> list[dict]:
    from .forensics import load
    base = load(original)
    w, h = base.size
    out = []

    r, g, b = base.split()
    out.append(("recoloured knockoff", "https://replica-outlet.shop/products/1-1-copy-super-cheap", "Brand 1:1 replica - cheap outlet",
                _jpg(Image.merge("RGB", (b, g, r)))))

    crop = base.crop((int(w * .1), int(h * .1), int(w * .9), int(h * .85))).resize((w, h))
    out.append(("cropped and reframed", "https://www.dhgate.com/product/brand-style-wholesale/123.html", "Wholesale brand style factory price",
                _jpg(crop, 70)))

    t = base.copy(); d = ImageDraw.Draw(t)
    d.rectangle((0, int(h * .78), w, h), fill=(220, 20, 20)); d.text((int(w * .08), int(h * .84)), "SALE -70%  BUY NOW", fill="white")
    out.append(("fake promo overlay", "https://www.aliexpress.com/item/1005001234567.html", "Brand discount sale 70% off",
                _jpg(t)))

    p = base.copy(); box = (int(w * .3), int(h * .3), int(w * .7), int(h * .7))
    p.paste(p.crop(box).filter(ImageFilter.GaussianBlur(8)).transpose(Image.FLIP_LEFT_RIGHT), box[:2])
    out.append(("emblem tampered", "https://brand-shop-official-store.xyz/", "Official store", _jpg(p)))

    low = ImageEnhance.Contrast(base.resize((w // 2, h // 2))).enhance(1.2).resize((w, h))
    out.append(("re-compressed repost", "https://www.pinterest.com/pin/987654321/", "Inspiration board", _jpg(low, 40)))

    return [{"image_url": "demo://" + label.replace(" ", "-"), "page_url": url, "title": title, "source": "DEMO SAMPLE",
             "blob": blob, "demo": True} for label, url, title, blob in out]
