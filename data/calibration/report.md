# Forensics Threshold Calibration Report

## Dataset
- Total pairs: 159
- Positives: 80
- Negatives: 79

## Current Thresholds
- HASH_RELATED_MAX: 50
- INLIERS_MIN: 13
- INLIER_RATIO_MIN: 0.7
- TEMPLATE_MIN: 0.6
- Precision: 0.963
- Recall: 0.650
- F1: 0.776

## Recommended Thresholds
- HASH_RELATED_MAX: 35
- INLIERS_MIN: 13
- INLIER_RATIO_MIN: 0.7
- TEMPLATE_MIN: 0.5
- CHECK_HOMOGRAPHY: False
- Precision: 0.987
- Recall: 0.925
- F1: 0.955

## Confusion Matrix

| | Related | Unrelated |
|---|---|---|
| **Predicted Related** | 74 | 1 |
| **Predicted Unrelated** | 6 | 78 |

## Errors

### False Positives (predicted related, actually unrelated)

| Original | Candidate | Hash Distance | Inliers | Ratio | Template | Degenerate | Note |
|---|---|---|---|---|---|---|---|
| logo_circle.jpg | logo_circles.jpg | 88 | 0 | 0.00 | 0.88 | False | Hard negative: circle vs circles logos |

### False Negatives (predicted unrelated, actually related)

| Original | Candidate | Hash Distance | Inliers | Ratio | Template | Degenerate | Kind | Note |
|---|---|---|---|---|---|---|---|---|
| logo_stripes.jpg | logo_stripes_embedded.jpg | 97 | 0 | 0.00 | 0.34 | True | modified | Logo embedded small in larger scene |
| logo_stripes.jpg | logo_stripes_rot.jpg | 50 | 0 | 0.00 | 0.39 | True | modified | Slightly rotated |
| logo_circle.jpg | logo_circle_embedded.jpg | 114 | 0 | 0.00 | 0.22 | True | modified | Logo embedded small in larger scene |
| logo_stripes_alt.jpg | logo_stripes_alt_embedded.jpg | 89 | 0 | 0.00 | 0.40 | True | modified | Logo embedded small in larger scene |
| logo_circles.jpg | logo_circles_embedded.jpg | 70 | 0 | 0.00 | 0.38 | True | modified | Logo embedded small in larger scene |
| logo_chevron.jpg | logo_chevron_rot.jpg | 43 | 0 | 0.00 | 0.44 | True | modified | Slightly rotated |
