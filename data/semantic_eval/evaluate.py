#!/usr/bin/env python3
"""Evaluate CLIP semantic matcher on realistic synthetic product images."""
import sys
import time
from pathlib import Path
import numpy as np

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.semantic import Embedding, compare, embed_many, SEMANTIC_MIN

eval_dir = Path(__file__).parent / "synthetic"

if not eval_dir.exists():
    print(f"Evaluation dataset not found in {eval_dir}")
    sys.exit(1)

print("Loading realistic synthetic product dataset...")

# Group by product type
products = {}
for fpath in sorted(eval_dir.glob("*.jpg")):
    if fpath.suffix != ".jpg":
        continue
    for prod_type in ["sneaker", "handbag", "watch"]:
        if prod_type in fpath.name:
            if prod_type not in products:
                products[prod_type] = {"pos": [], "hard_neg": []}
            if "pos_" in fpath.name:
                products[prod_type]["pos"].append(fpath)
            elif "hard_neg_" in fpath.name:
                products[prod_type]["hard_neg"].append(fpath)
            break

easy_negs = list(eval_dir.glob("easy_neg_*.jpg"))

all_pos_sims = []
all_hard_sims = []
all_easy_sims = []

print("Evaluating by product category:\n")
for prod_type in ["sneaker", "handbag", "watch"]:
    if prod_type not in products or not products[prod_type]["pos"]:
        continue

    groups = products[prod_type]
    print(f"{prod_type.upper()}:")

    # Use first positive as reference
    ref_data = groups["pos"][0].read_bytes()
    ref = Embedding(ref_data)

    # Compare positives
    pos_sims = []
    print(f"  Positives:")
    for pos_fpath in groups["pos"][1:]:
        data = pos_fpath.read_bytes()
        try:
            result = compare(ref, data)
            sim = result["similarity"] if result else 0.0
            pos_sims.append(sim)
            print(f"    {sim:.3f}")
        except:
            pass

    # Add identical
    result = compare(ref, ref_data)
    if result:
        pos_sims.append(result["similarity"])
        print(f"    1.000 (identical)")

    all_pos_sims.extend(pos_sims)

    # Compare hard negatives
    hard_sims = []
    print(f"  Hard negatives:")
    for hard_fpath in groups["hard_neg"]:
        data = hard_fpath.read_bytes()
        try:
            result = compare(ref, data)
            sim = result["similarity"] if result else 0.0
            hard_sims.append(sim)
            print(f"    {sim:.3f}")
        except:
            pass

    all_hard_sims.extend(hard_sims)
    print()

# Easy negatives
print(f"EASY NEGATIVES (cross-category):")
for easy_fpath in easy_negs:
    data = easy_fpath.read_bytes()
    try:
        result = compare(ref, data)
        sim = result["similarity"] if result else 0.0
        all_easy_sims.append(sim)
        print(f"  {sim:.3f}")
    except:
        pass

# Statistics
def stats(sims, name):
    sims_valid = sorted([s for s in sims if s > 0])
    if not sims_valid:
        return None
    mn, mx = min(sims_valid), max(sims_valid)
    med = np.median(sims_valid)
    p5 = np.percentile(sims_valid, 5) if len(sims_valid) >= 5 else sims_valid[0]
    p95 = np.percentile(sims_valid, 95) if len(sims_valid) >= 5 else sims_valid[-1]
    result = {"min": mn, "p5": p5, "median": med, "p95": p95, "max": mx}
    print(f"{name}:")
    print(f"  min={mn:.3f}, p5={p5:.3f}, median={med:.3f}, p95={p95:.3f}, max={mx:.3f}")
    return result

print("\n=== SIMILARITY DISTRIBUTIONS ===")
pos_stats = stats(all_pos_sims, "Positives (same product, variations)")
hard_stats = stats(all_hard_sims, "Hard negatives (different products)")
easy_stats = stats(all_easy_sims, "Easy negatives (cross-category)")

# Recall at current threshold
pos_pass = sum(1 for s in all_pos_sims if s >= SEMANTIC_MIN)
pos_recall = 100 * pos_pass / len(all_pos_sims) if all_pos_sims else 0
hard_fail = sum(1 for s in all_hard_sims if s < SEMANTIC_MIN)
hard_specificity = 100 * hard_fail / len(all_hard_sims) if all_hard_sims else 0

print(f"\n=== PERFORMANCE AT SEMANTIC_MIN={SEMANTIC_MIN} ===")
print(f"Positive recall: {pos_recall:.0f}% ({pos_pass}/{len(all_pos_sims)})")
print(f"Hard negative rejection: {hard_specificity:.0f}% ({hard_fail}/{len(all_hard_sims)})")

if pos_recall < 40:
    print(f"\nWARNING: Positive recall <40%. Plain CLIP cannot reliably separate")
    print(f"same-model from same-category products on this synthetic data.")

# Batch throughput
print("\n=== BATCH THROUGHPUT ===")
test_images = [f.read_bytes() for f in list(eval_dir.glob("*.jpg"))[:32]]

start = time.time()
embeddings = embed_many(test_images, batch_size=16)
batch_time = time.time() - start

count = sum(1 for e in embeddings if e is not None)
if batch_time > 0:
    throughput = count / batch_time
    print(f"Batch: {count} images in {batch_time:.2f}s")
    print(f"Throughput: {throughput:.1f} images/sec (batch_size=16)")
    print(f"Per-image: {1000*batch_time/count:.1f} ms")
