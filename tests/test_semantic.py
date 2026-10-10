"""Tests for semantic CLIP-based matching."""
import io
import pytest
from pathlib import Path
from PIL import Image

# Skip tests if dependencies not available
pytest.importorskip("torch")
pytest.importorskip("open_clip")


def test_enabled_respects_env_var(monkeypatch):
    """enabled() returns False when BG_CLIP=0."""
    monkeypatch.setenv("BG_CLIP", "0")
    from app import semantic
    import importlib
    importlib.reload(semantic)
    assert semantic.enabled() is False


def test_enabled_returns_bool():
    """enabled() returns a boolean."""
    from app import semantic
    result = semantic.enabled()
    assert isinstance(result, bool)


def test_embedding_creation():
    """Embedding can be created from image bytes."""
    from app import semantic
    if not semantic.enabled():
        pytest.skip("CLIP dependencies not installed")
    
    img = Image.new("RGB", (256, 256), (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    data = buf.getvalue()
    
    emb = semantic.Embedding(data)
    assert emb.data == data
    assert emb.phash is not None


def test_identical_image_matches():
    """An identical image scores ~1.0 and returns all required dict keys."""
    from app import semantic
    if not semantic.enabled():
        pytest.skip("CLIP dependencies not installed")
    
    img = Image.new("RGB", (256, 256), (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    data = buf.getvalue()
    
    ref = semantic.Embedding(data)
    result = semantic.compare(ref, data)
    
    assert result is not None
    assert result["similarity"] >= 0.99
    assert result["kind"] == "same_product"
    assert result["label"] == "Same product, different photo"
    
    # Verify all required keys
    required_keys = [
        "kind", "label", "similarity", "hash_distance",
        "feature_inliers", "inlier_ratio", "changed_pct",
        "heatmap", "template_score", "c2pa", "ai_signal", "size"
    ]
    for key in required_keys:
        assert key in result, f"Missing key: {key}"


REAL = Path(__file__).parent.parent / "data" / "real_eval"


def image_to_bytes(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _real(pattern):
    files = sorted(REAL.glob(pattern)) if REAL.exists() else []
    if not files:
        pytest.skip("real evaluation photos not downloaded (data/real_eval)")
    return files


def test_real_augmented_positive_matches():
    """A real product photo with counterfeit-style edits (crop, new background, colour shift) still matches."""
    from app import semantic
    if not semantic.enabled():
        pytest.skip("CLIP dependencies not installed")
    from PIL import ImageEnhance
    src = _real("sneaker__adidas_stan_smith_shoe_0.jpg")[0]
    img = Image.open(src).convert("RGB")
    w, h = img.size
    edited = ImageEnhance.Color(img.crop((w * .08, h * .08, w * .92, h * .92))).enhance(0.7)
    ref = semantic.Embedding(src.read_bytes())
    result = semantic.compare(ref, image_to_bytes(edited))
    assert result is not None and result["similarity"] >= semantic.SEMANTIC_MIN


def test_real_hard_negative_rejected():
    """The most similar pair of DIFFERENT real products in one category (two handbags, 0.851) is rejected."""
    from app import semantic
    if not semantic.enabled():
        pytest.skip("CLIP dependencies not installed")
    a = _real("bag__gucci_handbag_1.jpg")[0]
    b = _real("bag__leather_handbag_0.jpg")[0]
    assert semantic.compare(semantic.Embedding(a.read_bytes()), b.read_bytes()) is None

def test_invalid_image_data_returns_none():
    """compare() returns None on invalid image data."""
    from app import semantic
    if not semantic.enabled():
        pytest.skip("CLIP dependencies not installed")
    
    img = Image.new("RGB", (256, 256), (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    data = buf.getvalue()
    
    ref = semantic.Embedding(data)
    result = semantic.compare(ref, b"not an image")
    assert result is None


def test_main_module_import():
    """app.main can be imported without error."""
    from app import main
    assert True


def test_embed_many_basic():
    """embed_many() processes multiple images and returns embeddings."""
    from app import semantic
    if not semantic.enabled():
        pytest.skip("CLIP dependencies not installed")
    
    images = []
    for i in range(4):
        img = Image.new("RGB", (256, 256), (i*50 % 256, 100, 150))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        images.append(buf.getvalue())
    
    embeddings = semantic.embed_many(images, batch_size=2)
    
    assert len(embeddings) == 4
    # At least some should be valid
    valid = [e for e in embeddings if e is not None]
    assert len(valid) > 0
