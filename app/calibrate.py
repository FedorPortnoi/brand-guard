#!/usr/bin/env python
"""Calibrate forensics thresholds on labelled data. CLI: python -m app.calibrate [--apply] [--real]"""
import argparse
import json
import re
from pathlib import Path
from itertools import product
import numpy as np

from .forensics import Reference, features
from .store import DATA

CALIB_DIR = DATA / "calibration"
LABELS_FILE = CALIB_DIR / "labels.json"
FEATURES_FILE = CALIB_DIR / "features.json"
REAL_LABELS_FILE = DATA / "labels" / "real.jsonl"
REPORT_FILE = CALIB_DIR / "report.md"

# Thresholds to search (fine grid for precision >= 0.97 target)
HASH_RELATED_RANGE = range(0, 81, 5)  # 0 to 80 step 5
INLIERS_MIN_RANGE = range(8, 41, 1)  # 8 to 40 step 1
INLIER_RATIO_RANGE = [round(x, 2) for x in np.arange(0.3, 0.95, 0.05)]
TEMPLATE_MIN_RANGE = [round(x, 2) for x in np.arange(0.5, 1.02, 0.05)]
HOMOGRAPHY_CHECK_RANGE = [True, False]  # Toggle homography sanity check

def compute_all_features():
    """Compute features for all labelled pairs, caching in features.json."""
    if FEATURES_FILE.exists():
        return json.loads(FEATURES_FILE.read_text(encoding="utf-8"))

    labels = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    feats_dict = {}

    print(f"Computing features for {len(labels)} pairs...")
    for i, pair in enumerate(labels):
        key = f"{pair['original']}|{pair['candidate']}"
        orig_path = CALIB_DIR / pair["original"]
        cand_path = CALIB_DIR / pair["candidate"]

        if not orig_path.exists() or not cand_path.exists():
            print(f"  Skipping {key}: file not found")
            continue

        ref = Reference(orig_path.read_bytes())
        feats = features(ref, cand_path.read_bytes())
        feats_dict[key] = {}
        for k, v in feats.items():
            if v is None:
                feats_dict[key][k] = None
            elif isinstance(v, bool):
                feats_dict[key][k] = bool(v)
            elif isinstance(v, (int, np.integer)):
                feats_dict[key][k] = int(v)
            elif isinstance(v, (float, np.floating)):
                feats_dict[key][k] = float(v)
            else:
                feats_dict[key][k] = v

        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(labels)}")

    FEATURES_FILE.write_text(json.dumps(feats_dict, indent=2), encoding="utf-8")
    return feats_dict

def load_real_labels() -> list[dict]:
    """Load real user labels from data/labels/real.jsonl."""
    if not REAL_LABELS_FILE.exists():
        return []
    return [json.loads(line) for line in REAL_LABELS_FILE.read_text(encoding="utf-8").split("\n") if line.strip()]

def merge_labels(calib_labels: list, real_labels: list) -> list:
    """Merge calibration and real labels."""
    merged = calib_labels.copy()
    for rl in real_labels:
        label = "confirmed" if rl["label"] == "confirmed" else ("not related" if rl["label"] == "false_positive" else None)
        if label is None:
            continue
        merged.append({
            "original": rl.get("original", "real"),
            "candidate": rl.get("candidate", "real"),
            "related": rl["label"] == "confirmed",
            "kind": rl.get("kind"),
            "note": f"Real user label: {rl.get('note', '')}"
        })
    return merged

def decide_related(hash_dist, inliers, ratio, tmpl, degenerate, check_homography,
                   hash_max, inliers_min, ratio_min, template_min):
    """Decide if related based on thresholds."""
    # If homography check is enabled and marked degenerate, it's not related
    if check_homography and degenerate:
        return False

    strong_features = inliers >= inliers_min and ratio >= ratio_min
    return hash_dist <= hash_max or strong_features or tmpl >= template_min

def evaluate_thresholds(labels, feats_dict, config):
    """Evaluate a threshold configuration. Returns metrics and error details."""
    correct = 0
    tp = 0
    fp = 0
    fn = 0
    fps = []
    fns = []

    for pair in labels:
        key = f"{pair['original']}|{pair['candidate']}"
        if key not in feats_dict:
            continue

        f = feats_dict[key]
        is_related = decide_related(
            f["hash_dist"], f["inliers"], f["ratio"], f["tmpl"],
            f.get("degenerate", False), config["check_homography"],
            config["hash_max"], config["inliers_min"],
            config["ratio_min"], config["template_min"]
        )

        if is_related == pair["related"]:
            correct += 1
            if is_related:
                tp += 1
        else:
            if is_related and not pair["related"]:
                fp += 1
                fps.append((pair, f))
            else:
                fn += 1
                fns.append((pair, f))

    precision = tp / (tp + fp) if tp + fp > 0 else 1.0
    recall = tp / (tp + fn) if tp + fn > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "fps": fps,
        "fns": fns,
    }

def grid_search(labels, feats_dict):
    """Find best thresholds: precision >= 0.97, maximize recall, tie-break by proximity to old values."""
    best = None
    best_score = -1
    current = current_thresholds()

    configs_tried = 0
    for hash_max, inliers_min, ratio_min, template_min, check_homo in product(
        HASH_RELATED_RANGE, INLIERS_MIN_RANGE, INLIER_RATIO_RANGE, TEMPLATE_MIN_RANGE, HOMOGRAPHY_CHECK_RANGE
    ):
        config = {
            "hash_max": hash_max,
            "inliers_min": inliers_min,
            "ratio_min": ratio_min,
            "template_min": template_min,
            "check_homography": check_homo,
        }
        eval = evaluate_thresholds(labels, feats_dict, config)
        configs_tried += 1

        # Primary: precision >= 0.97
        if eval["precision"] >= 0.97:
            # Tie-break: recall, then proximity to current values
            distance = (abs(config["hash_max"] - current["HASH_RELATED_MAX"]) +
                       abs(config["inliers_min"] - current["INLIERS_MIN"]) * 5 +
                       abs(config["ratio_min"] - current["INLIER_RATIO_MIN"]) * 100)
            score = eval["recall"] - distance * 0.0001
            if score > best_score:
                best = (config, eval)
                best_score = score

    print(f"Searched {configs_tried} configurations")
    return best

def current_thresholds():
    """Return current thresholds from forensics.py."""
    from . import forensics
    return {k: getattr(forensics, k) for k in ("HASH_RELATED_MAX", "INLIERS_MIN", "INLIER_RATIO_MIN", "TEMPLATE_MIN")}

def apply_thresholds(config):
    """Rewrite constants in forensics.py."""
    forensics_path = Path(__file__).parent / "forensics.py"
    content = forensics_path.read_text(encoding="utf-8")

    replacements = {
        "HASH_RELATED_MAX": config["hash_max"],
        "INLIERS_MIN": config["inliers_min"],
        "INLIER_RATIO_MIN": config["ratio_min"],
        "TEMPLATE_MIN": config["template_min"],
        "CHECK_HOMOGRAPHY": "True" if config["check_homography"] else "False",
    }

    for key, val in replacements.items():
        if key == "CHECK_HOMOGRAPHY":
            pattern = rf"{key}\s*=\s*(True|False)"
            replacement = f"{key} = {val}"
        else:
            pattern = rf"{key}\s*=\s*[\d.]+"
            replacement = f"{key} = {val}"
        content = re.sub(pattern, replacement, content)

    forensics_path.write_text(content, encoding="utf-8")
    print(f"Updated forensics.py with new thresholds")

def main():
    parser = argparse.ArgumentParser(description="Calibrate forensics thresholds")
    parser.add_argument("--apply", action="store_true", help="Apply recommended thresholds (only if precision >= 0.97)")
    parser.add_argument("--real", action="store_true", help="Include real user labels")
    args = parser.parse_args()

    # Load data
    labels = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    if args.real:
        real = load_real_labels()
        labels = merge_labels(labels, real)
        print(f"Merged {len(real)} real labels: {len(labels)} total")

    feats = compute_all_features()

    # Filter to pairs we have features for
    pairs_with_feats = [p for p in labels if f"{p['original']}|{p['candidate']}" in feats]
    print(f"Have features for {len(pairs_with_feats)}/{len(labels)} pairs\n")

    # Evaluate current
    current = current_thresholds()
    current_config = {
        "hash_max": current["HASH_RELATED_MAX"],
        "inliers_min": current["INLIERS_MIN"],
        "ratio_min": current["INLIER_RATIO_MIN"],
        "template_min": current["TEMPLATE_MIN"],
        "check_homography": True,
    }
    current_eval = evaluate_thresholds(pairs_with_feats, feats, current_config)
    print(f"Current thresholds (with homography check enabled):")
    print(f"  HASH_RELATED_MAX: {current['HASH_RELATED_MAX']}")
    print(f"  INLIERS_MIN: {current['INLIERS_MIN']}")
    print(f"  INLIER_RATIO_MIN: {current['INLIER_RATIO_MIN']}")
    print(f"  TEMPLATE_MIN: {current['TEMPLATE_MIN']}")
    print(f"  Precision: {current_eval['precision']:.3f}, Recall: {current_eval['recall']:.3f}, F1: {current_eval['f1']:.3f}")
    print(f"  TP: {current_eval['tp']}, FP: {current_eval['fp']}, FN: {current_eval['fn']}\n")

    # Grid search
    print("Grid searching thresholds (target: precision >= 0.97)...")
    result = grid_search(pairs_with_feats, feats)

    if not result:
        print("\n" + "!"*80)
        print("NO THRESHOLD CONFIGURATION MET PRECISION >= 0.97")
        print("!"*80)
        print("Keeping current thresholds. Consider improving the dataset or adjusting targets.")
        return

    config, eval = result
    print(f"\nBest configuration found:")
    print(f"  HASH_RELATED_MAX: {config['hash_max']}")
    print(f"  INLIERS_MIN: {config['inliers_min']}")
    print(f"  INLIER_RATIO_MIN: {config['ratio_min']}")
    print(f"  TEMPLATE_MIN: {config['template_min']}")
    print(f"  CHECK_HOMOGRAPHY: {config['check_homography']}")
    print(f"\nMetrics:")
    print(f"  Precision: {eval['precision']:.3f}")
    print(f"  Recall: {eval['recall']:.3f}")
    print(f"  F1: {eval['f1']:.3f}")
    print(f"  TP: {eval['tp']}, FP: {eval['fp']}, FN: {eval['fn']}")

    if args.apply:
        apply_thresholds(config)

    # Write report
    report_lines = [
        "# Forensics Threshold Calibration Report\n\n",
        "## Dataset\n",
        f"- Total pairs: {len(pairs_with_feats)}\n",
        f"- Positives: {sum(1 for p in pairs_with_feats if p['related'])}\n",
        f"- Negatives: {sum(1 for p in pairs_with_feats if not p['related'])}\n\n",
        "## Current Thresholds\n",
        f"- HASH_RELATED_MAX: {current['HASH_RELATED_MAX']}\n",
        f"- INLIERS_MIN: {current['INLIERS_MIN']}\n",
        f"- INLIER_RATIO_MIN: {current['INLIER_RATIO_MIN']}\n",
        f"- TEMPLATE_MIN: {current['TEMPLATE_MIN']}\n",
        f"- Precision: {current_eval['precision']:.3f}\n",
        f"- Recall: {current_eval['recall']:.3f}\n",
        f"- F1: {current_eval['f1']:.3f}\n\n",
        "## Recommended Thresholds\n",
        f"- HASH_RELATED_MAX: {config['hash_max']}\n",
        f"- INLIERS_MIN: {config['inliers_min']}\n",
        f"- INLIER_RATIO_MIN: {config['ratio_min']}\n",
        f"- TEMPLATE_MIN: {config['template_min']}\n",
        f"- CHECK_HOMOGRAPHY: {config['check_homography']}\n",
        f"- Precision: {eval['precision']:.3f}\n",
        f"- Recall: {eval['recall']:.3f}\n",
        f"- F1: {eval['f1']:.3f}\n\n",
        "## Confusion Matrix\n\n",
        "| | Related | Unrelated |\n",
        "|---|---|---|\n",
        f"| **Predicted Related** | {eval['tp']} | {eval['fp']} |\n",
        f"| **Predicted Unrelated** | {eval['fn']} | {len(pairs_with_feats) - eval['tp'] - eval['fp'] - eval['fn']} |\n\n",
        "## Errors\n\n",
        "### False Positives (predicted related, actually unrelated)\n\n",
    ]

    if eval["fps"]:
        report_lines.append("| Original | Candidate | Hash Distance | Inliers | Ratio | Template | Degenerate | Note |\n")
        report_lines.append("|---|---|---|---|---|---|---|---|\n")
        for pair, f in eval["fps"]:
            report_lines.append(f"| {pair['original']} | {pair['candidate']} | {f['hash_dist']} | {f['inliers']} | {f['ratio']:.2f} | {f['tmpl']:.2f} | {f.get('degenerate', False)} | {pair.get('note', '')} |\n")
    else:
        report_lines.append("None\n")

    report_lines.append("\n### False Negatives (predicted unrelated, actually related)\n\n")
    if eval["fns"]:
        report_lines.append("| Original | Candidate | Hash Distance | Inliers | Ratio | Template | Degenerate | Kind | Note |\n")
        report_lines.append("|---|---|---|---|---|---|---|---|---|\n")
        for pair, f in eval["fns"]:
            report_lines.append(f"| {pair['original']} | {pair['candidate']} | {f['hash_dist']} | {f['inliers']} | {f['ratio']:.2f} | {f['tmpl']:.2f} | {f.get('degenerate', False)} | {pair.get('kind', '?')} | {pair.get('note', '')} |\n")
    else:
        report_lines.append("None\n")

    REPORT_FILE.write_text("".join(report_lines), encoding="utf-8")
    print(f"\nReport written to {REPORT_FILE}")

if __name__ == "__main__":
    main()
