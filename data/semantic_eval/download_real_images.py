#!/usr/bin/env python3
"""Download real product photos from Wikimedia Commons for threshold calibration."""
import json
import time
import sys
from pathlib import Path

import httpx

QUERIES = [
    "Nike Air Max sneaker",
    "Adidas Superstar shoe",
    "Converse Chuck Taylor",
    "Vans Old Skool",
    "Puma sneaker",
    "leather handbag",
    "Louis Vuitton bag",
    "Coach handbag",
    "wristwatch Rolex",
    "Omega wristwatch",
]

OUTPUT_DIR = Path(__file__).parent / "synthetic"
OUTPUT_DIR.mkdir(exist_ok=True)

def download_images():
    """Download images and save metadata."""
    headers = {"User-Agent": "BrandGuardEval/0.1 (research; contact via repo)"}
    sources = []
    count = 0

    with httpx.Client(timeout=10.0) as client:
        for query in QUERIES:
            print(f"Searching: {query}")
            try:
                # Query Wikimedia Commons
                resp = client.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query",
                        "format": "json",
                        "generator": "search",
                        "gsrnamespace": "6",  # File namespace
                        "gsrlimit": "5",
                        "gsrsearch": query,
                        "prop": "imageinfo",
                        "iiprop": "url|extmetadata",
                        "iiurlwidth": "512",
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

                if "query" not in data or "pages" not in data["query"]:
                    print(f"  No results for: {query}")
                    time.sleep(0.5)
                    continue

                for page_id, page in data["query"]["pages"].items():
                    if "imageinfo" not in page:
                        continue

                    info = page["imageinfo"][0]
                    url = info.get("thumburl")
                    if not url or ".svg" in url.lower():
                        continue

                    # Get license
                    meta = info.get("extmetadata", {})
                    license_name = meta.get("LicenseShortName", {}).get("value", "unknown")

                    # Download image
                    try:
                        img_resp = client.get(url, headers=headers, timeout=10.0)
                        img_resp.raise_for_status()

                        # Save
                        fname = f"{count:03d}_{query.replace(' ', '_')[:30]}.jpg"
                        fpath = OUTPUT_DIR / fname
                        fpath.write_bytes(img_resp.content)

                        sources.append({
                            "file": fname,
                            "url": url,
                            "query": query,
                            "license": license_name,
                        })

                        count += 1
                        print(f"  Downloaded: {fname} ({len(img_resp.content)} bytes)")

                    except Exception as e:
                        print(f"  Failed to download: {e}")

                    time.sleep(0.5)

                    if count >= 50:
                        break

                time.sleep(0.5)

                if count >= 50:
                    break

            except Exception as e:
                print(f"  Error: {e}")
                time.sleep(1.0)

    # Save sources
    sources_path = OUTPUT_DIR / "SOURCES_downloaded.json"
    sources_path.write_text(json.dumps(sources, indent=2))
    print(f"\nDownloaded {count} images")
    print(f"Sources saved to {sources_path}")

if __name__ == "__main__":
    download_images()
