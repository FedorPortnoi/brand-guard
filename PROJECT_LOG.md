# Brand Guard: Project Log

Brand authenticity guard for a hackathon. Drop in a brand image, find likely copies on the web, check which are altered or fake, group them by seller, draft takedown notices, and keep watching for new copies. Everything runs free, with no paid keys required.

Last updated: 2026-10-10

## How to run

```
cd C:\Users\fedor\projects\brand-guard
.venv\Scripts\python -m uvicorn app.main:app --port 8000
.venv\Scripts\python -m pytest tests -q      # 183 tests, offline, about 40 s (tests/conftest.py turns every network source off)
```
Open http://localhost:8000.

Optional environment variables (all off by default). Put them in `.env` in the project root (loaded at startup; real environment variables take precedence; `.env` is in `.gitignore`):
- `SERPAPI_KEY`: reverse image search (Google Lens exact and visual matches, plus Yandex). `SERPAPI_ENGINES=lens_exact,lens_visual,yandex` rations the free quota of about 100 searches a month.
- `PUBLIC_BASE_URL`: public address of this server (e.g. a tunnel), so reverse search can fetch an uploaded image when no public image URL is typed in
- `BRAVE_API_KEY`: official Brave Search API. **Set in `.env` and confirmed working 2026-10-10.**
- `YDC_API_KEY`: You.com web search (web pages and thumbnails only; no image or reverse search). Up to 6 queries per scan. **Set in `.env` and confirmed working 2026-10-10.**
- `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (and `EBAY_MARKETPLACE`, default `EBAY_US`): eBay image search (Browse API `search_by_image`). Free developer account. Not yet run live.
- `GOOGLE_SAFE_BROWSING_KEY`: Safe Browsing check on each match's page. Lookup API is non-commercial only; commercial use needs Google Web Risk. **Set in `.env` and confirmed working 2026-10-10** (Google Cloud project `brand-guard-511212`, key `brand-guard-safe-browsing`, restricted to the Safe Browsing API).
- `IA_ACCESS_KEY`, `IA_SECRET_KEY`: optional archive.org keys for more Wayback saves. Saving works anonymously without them.
- `BG_ALIEXPRESS=0` turns off AliExpress search (OneFindMe, no key, on by default). `BG_ALIEXPRESS_COUNTRY` (default US).
- `BG_PHISHSTATS=0` turns off the PhishStats check (no key, on by default).
- `GEMINI_API_KEY` (and `GEMINI_MODEL`): page-context classifier
- `BG_MONITOR=0`: turn off the monitoring scheduler. `BG_CLIP=0`: turn off same-product matching.

Optional install for "same product, different photo" (about 1 GB): see `requirements-clip.txt`.

## Current state (2026-10-09)

**Status:** working end to end on a local machine. 125 offline tests pass. Last full browser run: live scan of 299 candidates → matches, Sellers tab, takedown drafts and ZIP, label buttons, and a monitoring watch to its baseline alert, with no errors.

| Feature | State | Verified how |
|---|---|---|
| Scan, verify, risk score, PDF evidence | Working | Live scans in the browser and via the API |
| Pixel forensics (copy, crop, edit detection) | Working, calibrated | 0 false positives on 1,190 real photo pairs; synthetic precision 0.986 / recall 0.89 |
| Seller grouping and domain age | Working | Live, on the recorded Adidas scan and the browser scan |
| Takedown drafts and evidence ZIP | Working | Browser run; drafts read by hand; 6 of 18 report links confirmed |
| Monitoring and alerts | Working | Browser run to the baseline alert; 21 tests including concurrency |
| Label buttons (feedback for calibration) | Working | Browser run; saved record checked |
| Same product, different photo (CLIP) | Working, optional install | Offline on real photos only; 0 hits in the one live scan |
| Reverse image search (Lens + Yandex) | Built, not run live | Offline tests only; needs `SERPAPI_KEY` |
| Gemini page classifier | Built, not run live | Needs `GEMINI_API_KEY` |
| C2PA validation | Working | Real signed sample: valid, stripped and tampered all detected (2026-10-10) |

**Not production-ready:** runs on one local machine; no login; scan sessions are in memory and are lost on restart (saved scans can be replayed); monitoring runs only while the server is up.

**Data on disk:** `data/demo.json` (good Adidas scan for the offline demo), `data/last_scan.json` (latest scan), `data/monitor/` (watches and alerts, currently empty), `data/labels/` (user labels, currently empty), `data/calibration/` and `data/real_eval/` (evaluation sets).

## Done

| Date | Item | Notes |
|---|---|---|
| 2026-10-09 | Core pipeline: upload, discover, verify, score, stream results | FastAPI with server-sent events, single-page UI |
| 2026-10-09 | Image forensics | Perceptual hashes, ORB feature matching, difference heatmap, transparent PNGs flattened onto white |
| 2026-10-09 | Risk scoring (0-100) | Marketplace domains, suspicious wording, low-trust TLDs, edit severity, official-domain allowlist |
| 2026-10-09 | Discovery v2 | 12 expanded queries, several image backends, crawl of shop and marketplace pages, about 250-300 candidates per scan |
| 2026-10-09 | Edge-template matching, C2PA validation, synthetic-image hints | See forensics.py |
| 2026-10-09 | Blocked-download handling, safe fetching (SSRF), PDF evidence report | |
| 2026-10-09 | Offline demo | `data/demo.json` (Adidas scan) and `data/last_scan.json`, replay button, synthetic DEMO copies |
| 2026-10-09 | Refactor | Scan logic moved to `pipeline.py` (used by the API and by monitoring); shared state in `store.py`; RDAP lookups in `rdap.py` |
| 2026-10-09 | 1. Reverse image search | `search.reverse_candidates`: Lens exact, Lens visual and Yandex via SerpApi, run in parallel and deduplicated. Rows tagged "reverse". Tested offline only (no key) |
| 2026-10-09 | 2. Same product, different photo (CLIP) | Opt-in checkbox. Batched screen over non-pixel matches after the main pass. ViT-B-32-quickgelu; threshold 0.90 set on 34 real photos (see `data/real_eval/README.md`) |
| 2026-10-09 | 3. Seller intelligence | Sellers tab: groups matches by store, marketplace seller or domain; domain age and registrar via RDAP; seller score and plain-English flags |
| 2026-10-09 | 4. Takedown drafts | Per-channel notices (copyright / trademark / both), observational wording, CONFIRM placeholders, report-form links, abuse contact via RDAP, mailto, ZIP evidence pack. Nothing is sent automatically |
| 2026-10-09 | 5. Monitoring | Watches re-scan on a schedule; baseline first, then alerts for new matches above a risk floor; optional Slack/Discord webhook; Alerts badge in the header |
| 2026-10-09 | 6. Calibration | Named thresholds, homography sanity check, `python -m app.calibrate`, label buttons in the UI (`data/labels/real.jsonl`) |
| 2026-10-09 | Full browser test | Live scan (299 candidates), sellers, takedown modal, ZIP, labels, a monitoring watch to baseline alert: all work |
| 2026-10-10 | `.env` loading + Brave API key | `python-dotenv` in `app/main.py`; key tested live on image and web search (HTTP 200); 125 tests still pass |
| 2026-10-10 | You.com search source | `search.you_api`, wired into `discover()`; shopping-style queries first; pages go to the crawl, thumbnails become candidates. 5 offline tests; live check with 2 queries: 20 pages, 4 images, no errors |
| 2026-10-10 | AliExpress source (OneFindMe) | `marketplaces.aliexpress_candidates`: listings with store, price, orders; store name feeds the Sellers tab. Affiliate short links are resolved to `aliexpress.com/item/<id>.html` only for listings that match. Live: 20 listings, link resolved |
| 2026-10-10 | eBay image search | `marketplaces.ebay_image_candidates`: sends the scanned image (not a URL), returns listings with seller and price. Offline tests only; needs eBay keys |
| 2026-10-10 | PhishStats lookalike domains | `reputation.phishstats_reports`: "Lookalike domains" panel (brand name in domain, reported as phishing; shown as text, not links) and +20 risk for matches on reported hosts. Shared hosts (e.g. sites.google.com) are flagged only by exact host. Live: found ww25.adidas-mx.club |
| 2026-10-10 | Google Safe Browsing | `reputation.ScanReputation.safe_browsing`: +25 risk and a reason when Google flags a match's page. Live: Google's phishing and malware test pages flagged, adidas.com clean |
| 2026-10-10 | Wayback Machine archiving | `archive.py` + `POST /api/archive/{scan_id}`: "Save to Wayback Machine" in the detail view and takedown modal. Archived link goes into notices, PDFs and the ZIP. Falls back to the newest existing snapshot when a save is rate-limited. User-triggered only (it publishes a copy) |
| 2026-10-10 | Live end-to-end check of the new sources | Adidas logo rescan: 512 candidates (was 256), 8 verified copies (was 3), about 1 min. Per source: Bing 186, Brave API 166, Brave (ddgs) 60, page crawl 59, You.com 21, AliExpress 20. Lookalike panel, detail view and Wayback save (about 90 s) all worked in the browser |
| 2026-10-10 | Scan quality fixes | One unverified row per page (blocked images on the same page were repeated rows); `done` event reports candidates per source; every scan is also kept in `data/scans/` (newest 20, replay with name `scans/<file>`) |
| 2026-10-10 | Takedown report links checked | 17 of 18 confirmed in a browser. Amazon's old link was dead; it now points at amazon.com/report/infringement, which errors when signed out (unconfirmed) |
| 2026-10-10 | C2PA tested on a real signed image | Official c2pa-rs sample: valid (signer named), stripped copy = not_present with the 'original has credentials, copy has none' reason, tampered copy = invalid (+15 risk). Kept as `tests/fixtures/c2pa_signed.jpg` with 3 offline tests |
| 2026-10-10 | New demo scan | Official adidas.com Stan Smith photo (M20325), 16 official Adidas domains, same-product search and 5 synthetic copies on. 530 candidates: 26 verified copies, 10 same-product (first real CLIP hits, incl. AliExpress 'BoundHub Store' and Walmart Mexico), eBay listing via You.com, official regional sites score 0. Saved as `data/demo.json` (replay with name `demo`); old logo demo kept as `data/demo_logo_2026-10-09.json` |
| 2026-10-10 | Neutral UI wording | Hints no longer say Nike / sneakers (now "Acme", "product or model name, campaign, slogan"); header and image label mention ads, listing photos, marketplaces, social media and websites |
| 2026-10-10 | Scam signals | `risk.SCAM_WORDS` (fake jobs, giveaways, investments, rentals): +25 risk, category `possible_scam`, trademark-style notice. Classified sites (Craigslist, Gumtree, Kijiji, OfferUp and others) count as marketplaces. Discovery adds "<brand> giveaway" and "<brand> job offer" searches. Wording is checked in titles and URLs only |
| 2026-10-10 | Video as the scanned original | `video.key_frames` picks up to 8 frames (one per scene, blank frames skipped); `forensics.compare_best` matches each candidate against all of them; matches record which frame (`ref_frame`), and PDFs, ZIPs and notices show that frame and its time. Key-frame strip in the UI. Same-product (CLIP) search is off for videos. Not yet run as a live web scan |
| 2026-10-10 | Video check tab | `video.compare` + `POST /api/video/compare`: suspect clip vs original, frame by frame (2 per second). Reports verdict, % matched, time ranges, and edits: mirrored, zoomed, reframed to vertical 9:16, sped up or slowed down, plus a pixel check of the closest frame. Checked in the browser with generated videos |
| 2026-10-10 | Video links (experimental) | `video_fetch` downloads from a link with yt-dlp (public addresses only, 200 MB, 10 min, 2 min timeout). Live: a YouTube link worked in about 5 s; an Instagram reel link got the "wants a login, upload the file instead" message (that test used a made-up reel address) |
| 2026-10-10 | Live browser test: Instagram commercial | Nike "Let It Rip" (official YouTube) vs a Catsuka Instagram repost, by link in the Video check tab. First result was poor (26 %, 11 fragments, false 1.2x speed). Fixed: original sampled at 6 fps, weak matches accepted only when they agree with the time offset, segments merged, robust speed estimate. Now: "Contains your video", 82 %, one segment 0:00-0:54 = 0:06-1:00. Two different Nike ads: no match |
| 2026-10-10 | Live browser test: video scan | Same commercial as the scan original. Fixed: key frames bunched in the first 8 s (now spread over the video, near-duplicates skipped), title / end cards (logo on plain background) matched every Nike logo online (now skipped), multi-frame comparison 8x the cost (now a cheap pre-check). Result: about 5 min -> 115 s, logo noise gone; real hits LBB Online, Ads of the World, FilmAffinity, MarketingOops |
| 2026-10-10 | Live browser test: product listings | AliExpress "STANLEY Sales Store" tumbler (WebP, likely one-off composite photo): no copies, lookalike phishing domain found. eBay AirPods listing without "Apple" in the title: photo traced to apple.com; same-product found a Sears Mexico "AirPods Pro replica" listing (risk 65). Fixed: takedown addressed "com.mx" instead of sears.com.mx (one shared domain helper now, more country endings); same-product pass used 1 CPU thread with no progress (now all cores, 2x faster, progress shown); duplicate same-product rows per page (now one, best first); misleading Instagram "login" message for deleted posts; flaky DNS-dependent webhook test |
| 2026-10-10 | Authorised resellers | New "Authorised resellers" field (scan and watches): matches on those domains get category `authorised_reseller`, risk 5, no text-only rows, and are left out of the Sellers tab, alerts and reputation checks. `risk.parse_domains` shared by scan and watch forms. Live: AirPods rescan with target.com, amazon.com, verizon.com -> those 4 rows at risk 5, Sears Mexico replica still 65 and the only seller |
| 2026-10-10 | Unverified rows hidden by default | "Show unverified rows (N)" toggle above the Matches table; hides text-only rows in the table, graph and Sellers tab; "flagged" count and Select all cover visible rows only. Checked with real clicks in the browser |
| 2026-10-10 | Narrow-screen graph fix | Under 1000 px wide the graph was sized as 55 % of a column that grows with the table, so it stretched (300 -> 880 px) and its nodes sat off-screen ("empty graph"). Now a fixed 340 px there |
| 2026-10-10 | Put on GitHub | Public repo FedorPortnoi/brand-guard. Not committed: `.env` (API keys), `.venv`, runtime data (`data/scans/`, `data/last_scan.json`, `data/monitor/`, `data/labels/`, `data/rdap_cache.json`) |

## Calibration results (forensics)

Validated on 34 real Wikimedia photos (1,190 unrelated pairs) plus 159 synthetic labelled pairs.
- Real photos: 0 false positives. Only 3 same-subject pairs match (two Nike logos, two Puma logos, two photos of one watch), which is correct.
- Synthetic set: precision 0.986, recall 0.89.
- Real Adidas copies (narodnatribuna, fabrikbrands) still match. The ACME-vs-wholesale-shoes false positive is gone. shop.overtime.tv, an Overtime hoodie with no Adidas logo that the old thresholds flagged, is now rejected.
- Key settings: homography sanity check ON (real false positives had impossible geometry); TEMPLATE_MIN 0.70 (0.5 flagged 16 unrelated real pairs); INLIER_RATIO_MIN 0.4 (real copies score 0.41-0.51).

## Known limits

- Reverse search (SerpApi) is untested against the live API, since there is no key. The response parsing follows SerpApi's docs.
- CLIP threshold is based on a small real set (34 photos, 92 same-category pairs). Live on the official Stan Smith photo it found 8-10 same-product listings, all plausible on inspection of the hosts; not yet labelled.
- Monitoring runs in-process: watches only run while the server is up, one at a time, 2-3 minutes each.
- Takedown report URLs: 17 of 18 confirmed in a browser on 2026-10-10. Amazon's form needs a signed-in account and could not be confirmed. Facebook links its Intellectual Property help hub; the forms inside need a login.
- The DuckDuckGo backend fails repeatedly; Bing carries image search.
- You.com results lean toward official and large retail sites (for Adidas, "replica" means official replica jerseys). The crawl takes only the first 30 pages, and You.com pages come last, so they may often be left out. Not yet measured in a full scan.
- eBay image search is untested against the live API (developer account pending approval).
- OneFindMe is a small third-party service; reliability unknown. Resolving a matched listing's link counts as a click on its affiliate link.
- PhishStats covers phishing reports, not counterfeit shops, so most brands get few or no lookalike domains. Brand names under 4 characters are skipped (too many false hits).
- Anonymous Wayback saves are rate-limited (a 429 came back on the first test); saves are capped at 2 at a time and 20 per request.
- Video: no free service searches the web by video, so a video scan finds copies through stills and thumbnails from the normal searches; the Video check tab handles clips the user brings. Videos are limited to 200 MB and the first 10 minutes.
- Video links: Instagram usually needs a login and fails; downloading can conflict with platform terms. Uploading a saved file always works.
- Scam wording is matched only in titles and URLs, not in page text, so scam pages with neutral titles are missed.
- Unverified (image blocked, text-only) rows are often noise from ambiguous brand words ("Apple" + "replica" finds an Apple-1 computer replica kit); they are hidden by default.
- Instagram profile pages need a login in a browser; single public reels open and download fine.
- AI-image detection is hints only. Results are signals for human review, not findings of infringement.
- `data/last_scan.json` now holds the 2026-10-10 Adidas logo rescan; the older 10-match Stan Smith scan was overwritten and lost (scan history in `data/scans/` prevents this from now on). `data/demo.json` (Adidas logo, 3 real copies + 5 synthetic) is unchanged.
- A flat logo is a weak demo image: its copies are mostly logo/wallpaper sites, scored low (correctly). The demo needs a product photo that sellers reuse.

## Plan (decided 2026-10-10)

A mentor from Cox Automotive suggested a car-to-car / car-to-road communication project (V2X: V2V and V2I).
Decision: **finish Brand Guard first; it is the safe project to present.** Then build a V2X prototype. If the V2X
prototype is ready in time, present it; if not, present Brand Guard.
- V2X starting point: real connected-vehicle pilot data on the USDOT ITS DataHub (Wyoming, Tampa, NYC pilots;
  SAE J2735 Basic Safety Messages and traffic-light SPaT/MAP messages).
- Alternative raised but not chosen: retarget Brand Guard to fake car listings that use stolen dealer photos (Cox owns Autotrader / KBB / Manheim).

## Next steps (priority order)

1. Get a SerpApi free key and run one scan with a public image URL to confirm reverse search live.
1b. eBay developer account registered 2026-10-10, **pending approval** (at least one business day). Once approved: create the Production keyset at developer.ebay.com/my/keys (opt out of marketplace account deletion notices, since no eBay user data is stored), put `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` in `.env`, and run one scan to confirm eBay image search live. 
2. Label 20-50 real matches with the UI buttons, then run `python -m app.calibrate --real`.
3. ~~Test same-product search on a real case~~ Done 2026-10-10: 8-10 real same-product listings on the Stan Smith scan.
4. ~~Check the takedown report links~~ Done 2026-10-10 (17 of 18). Remaining: confirm Amazon's form while signed in.
5. ~~Test C2PA with a signed image~~ Done 2026-10-10.
6. ~~Pick a demo product photo~~ Done 2026-10-10 (Stan Smith official photo). Note: the UI has no button for `demo.json`; "Replay last scan" replays `last_scan.json`, which currently holds the same scan.

## Ideas backlog

| Idea | Value | Effort |
|---|---|---|
| Live listing details: price, seller, "ships from" | Medium | Medium |
| ML detector for AI-generated images (needs a trusted pinned model) | Medium | Medium |
| Batch mode for a whole product catalogue | Medium | Medium |
| Trust list for C2PA signers | Low | Small |
| Run monitoring outside the web process (scheduled task) | Medium | Small |

## Demo checklist

- [ ] Pick a branded product photo with known fakes (better than a flat logo)
- [ ] Run a scan with "Add 5 synthetic demo copies" ticked
- [ ] Confirm `data/demo.json` holds a good scan for offline replay
- [ ] Show the Sellers tab, then "Takedown all" on the top seller, then download the evidence pack
- [ ] Show Monitoring: start a watch and point at the Alerts badge
- [ ] Rehearse the line: "Brands spend millions on creative, then lose control of it online. We find likely copies, show which look fake or stolen, group them by seller, and draft the takedown."
