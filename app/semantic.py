"""Semantic ("same product, different photo") matching with CLIP embeddings.

Contract used by pipeline.py:
    enabled() -> bool
    Embedding(data: bytes)                 # precomputed embedding of the original
    compare(ref: Embedding, data: bytes) -> dict | None   # forensics-shaped dict, or None if not the same product

Batch screening:
    embed_many(datas: list[bytes]) -> list[np.ndarray | None]   # normalised vectors; dot with the reference
"""
import importlib.util
import io
import os
import threading
from typing import Optional

import imagehash
import numpy as np

# Calibrated on 34 real Wikimedia photos (data/real_eval), ViT-B-32-quickgelu/openai:
# counterfeit-style edits median 0.964; different products in the same category max 0.856 (n=92 pairs);
# cross-category max 0.701. At 0.90: 0 hard negatives pass, 86% of edited copies match.
SEMANTIC_MIN = 0.90

_model_lock = threading.Lock()
_model = None
_processor = None


def enabled() -> bool:
    """Check if dependencies are available and feature is enabled via BG_CLIP env var."""
    if os.getenv("BG_CLIP") == "0":
        return False
    torch_spec = importlib.util.find_spec("torch")
    clip_spec = importlib.util.find_spec("open_clip")
    return torch_spec is not None and clip_spec is not None


def _load_model():
    """Lazy load CLIP model on first use (singleton)."""
    global _model, _processor
    if _model is not None:
        return _model, _processor

    import torch
    import open_clip

    device = "cpu"
    torch.set_num_threads(1)

    model, _, preprocess = open_clip.create_model_and_transforms(
        'ViT-B-32-quickgelu', pretrained='openai', device=device
    )
    model.eval()

    _model = model
    _processor = preprocess
    return _model, _processor


class Embedding:
    """Precomputed CLIP embedding of the original image."""

    def __init__(self, data: bytes):
        self.data = data
        self.phash = None
        self.embedding = None

        # Precompute phash for the dict we return
        try:
            from . import forensics
            img = forensics.load(data)
            self.phash = imagehash.phash(img, hash_size=16)
        except ImportError:
            try:
                from app import forensics
                img = forensics.load(data)
                self.phash = imagehash.phash(img, hash_size=16)
            except Exception:
                from PIL import Image
                img = Image.open(io.BytesIO(data))
                img.load()
                self.phash = imagehash.phash(img, hash_size=16)

    def get_embedding(self) -> np.ndarray:
        """Get or compute the normalized CLIP embedding."""
        if self.embedding is not None:
            return self.embedding

        with _model_lock:
            if self.embedding is not None:
                return self.embedding

            model, processor = _load_model()
            import torch

            try:
                from PIL import Image
                img = Image.open(io.BytesIO(self.data))
                img.load()
                if img.mode in ("P", "LA", "RGBA") or "transparency" in img.info:
                    rgba = img.convert("RGBA")
                    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    img = Image.alpha_composite(bg, rgba).convert("RGB")
                else:
                    img = img.convert("RGB")
            except Exception as e:
                raise ValueError(f"Cannot load image: {e}")

            x = processor(img).unsqueeze(0)
            with torch.no_grad():
                features = model.encode_image(x)

            self.embedding = features[0].cpu().numpy()
            self.embedding = self.embedding / (np.linalg.norm(self.embedding) + 1e-8)

            return self.embedding


def embed_many(datas: list[bytes], batch_size: int = 16) -> list[Optional[np.ndarray]]:
    """Batch encode multiple images into normalized CLIP embeddings.

    Args:
        datas: list of image bytes
        batch_size: number of images per forward pass (default 16)

    Returns:
        list of normalized embedding vectors (or None if image fails to load)
    """
    if not enabled():
        return [None] * len(datas)

    model, processor = _load_model()
    import torch

    with _model_lock:
        # the batch runs on its own after the pixel pass, so it can use the cores (single images stay at 1 thread)
        torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
        try:
            return _embed_batches(model, processor, datas, batch_size)
        finally:
            torch.set_num_threads(1)


def _embed_batches(model, processor, datas: list[bytes], batch_size: int) -> list[Optional[np.ndarray]]:
    import torch
    embeddings = []
    for batch_start in range(0, len(datas), batch_size):
        batch_end = min(batch_start + batch_size, len(datas))
        batch_data = datas[batch_start:batch_end]

        # Load images
        imgs = []
        for data in batch_data:
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(data))
                img.load()
                if img.mode in ("P", "LA", "RGBA") or "transparency" in img.info:
                    rgba = img.convert("RGBA")
                    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    img = Image.alpha_composite(bg, rgba).convert("RGB")
                else:
                    img = img.convert("RGB")
                imgs.append(img)
            except Exception:
                imgs.append(None)

        # Preprocess
        batch_tensors = []
        for img in imgs:
            if img is not None:
                batch_tensors.append(processor(img))
            else:
                batch_tensors.append(None)

        # Forward pass
        valid_tensors = [t for t in batch_tensors if t is not None]
        if valid_tensors:
            x = torch.stack(valid_tensors)
            with torch.no_grad():
                features = model.encode_image(x)

            feat_list = features.cpu().numpy()
            feat_idx = 0
            for t_idx, t in enumerate(batch_tensors):
                if t is not None:
                    emb = feat_list[feat_idx]
                    emb = emb / (np.linalg.norm(emb) + 1e-8)
                    embeddings.append(emb)
                    feat_idx += 1
                else:
                    embeddings.append(None)
        else:
            embeddings.extend([None] * len(batch_tensors))

    return embeddings


def compare(ref: Embedding, data: bytes) -> Optional[dict]:
    """Return None unless cosine similarity >= SEMANTIC_MIN. On a match, return forensics-shaped dict."""
    try:
        from . import forensics
        import torch
    except ImportError:
        try:
            from app import forensics
            import torch
        except ImportError:
            return None

    if not enabled():
        return None

    ref_emb = ref.get_embedding()
    if ref_emb is None:
        return None

    with _model_lock:
        model, processor = _load_model()
        torch.set_num_threads(1)

        try:
            from PIL import Image
            cand_img = Image.open(io.BytesIO(data))
            cand_img.load()
            if cand_img.mode in ("P", "LA", "RGBA") or "transparency" in cand_img.info:
                rgba = cand_img.convert("RGBA")
                bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                cand_img = Image.alpha_composite(bg, rgba).convert("RGB")
            else:
                cand_img = cand_img.convert("RGB")
        except Exception:
            return None

        x = processor(cand_img).unsqueeze(0)
        with torch.no_grad():
            features = model.encode_image(x)

        cand_emb = features[0].cpu().numpy()
        cand_emb = cand_emb / (np.linalg.norm(cand_emb) + 1e-8)

    similarity = float(np.dot(ref_emb, cand_emb))

    if similarity < SEMANTIC_MIN:
        return None

    try:
        from . import forensics
        cand_full = forensics.load(data)
    except ImportError:
        try:
            from app import forensics
            cand_full = forensics.load(data)
        except Exception:
            return None

    cand_phash = imagehash.phash(cand_full, hash_size=16)
    hash_distance = int(ref.phash - cand_phash)

    return {
        "kind": "same_product",
        "label": "Same product, different photo",
        "similarity": round(similarity, 3),
        "hash_distance": hash_distance,
        "feature_inliers": 0,
        "inlier_ratio": 0.0,
        "changed_pct": 0.0,
        "heatmap": None,
        "template_score": 0.0,
        "c2pa": forensics.c2pa_status(data),
        "ai_signal": forensics.ai_signal(data, cand_full),
        "size": list(cand_full.size),
    }
