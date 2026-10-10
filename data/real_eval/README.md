# Real evaluation photos

34 photos from Wikimedia Commons (CC BY / CC BY-SA / CC0 / public domain; per-file title, URL and licence in sources.json).
Used to validate thresholds on real images, not for training:
- forensics: every pair (1,190) is unrelated except same-subject pairs; at the shipped thresholds only 3 same-subject pairs match, 0 false positives.
- CLIP (ViT-B-32-quickgelu/openai): counterfeit-style edits median 0.964; same-category different products max 0.856; SEMANTIC_MIN 0.90.
Downloaded 2026-10-09 with a User-Agent that includes a contact URL, as Wikimedia's robot policy requires.
