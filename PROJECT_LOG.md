# Brand Guard: Project Log

Brand authenticity guard for a hackathon. Drop in a brand image, find likely copies on the web, check which are altered or fake, group them by seller, draft takedown notices, and keep watching for new copies. Everything runs free, with no paid keys required.

Last updated: 2026-10-11 ~01:40 ET. Day-by-day story (tests, incidents, decisions): `session-logs/` (latest: `session-logs/2026-10-11.md`).

## How to run

```
cd D:\brand-guard
.venv\Scripts\python -m uvicorn app.main:app --port 8000
.venv\Scripts\python -m pytest tests -q      # 319 tests, offline, about 55 s (tests/conftest.py turns every network source off)
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

**On Replit:** import the repo; the Run button runs `bash start.sh` (installs `requirements.txt` on first run, serves on port 8000, uses the Repl's public address as `PUBLIC_BASE_URL`). Put API keys in Replit Secrets. Publish as a **Reserved VM**: monitoring and the iMessage agent must keep running. Leave out the optional CLIP install there.

**iMessage agent** (`imessage-agent/`, Photon Spectrum): `cd imessage-agent && npm install && npm start` next to a running Brand Guard. Without `SPECTRUM_PROJECT_ID` / `SPECTRUM_PROJECT_SECRET` it opens Spectrum's terminal chat for testing; with them it answers real iMessages. `start.sh` starts it automatically when both are set. See `imessage-agent/README.md`.

## Current state (2026-10-11)

**The product is now the "is it legit?" shopper agent in iMessage.** `main` = `b00973d`, CI green, 319 Python + 21
agent tests. Built tonight as steps 2–11 (PRs #3–#12); full story in `session-logs/2026-10-11.md`.

| Input the shopper texts | What happens |
|---|---|
| a link (shop, product, post, account) | checked: verdict + reasons with proof |
| a store's name ("is Lucky Fours legit?") | its own site found by search, then checked |
| a screenshot (HEIC fine) | text read; the address or @account found and checked; its words count ("Zelle only") |
| a video (screen recording, saved reel) | up to 8 distinct frames read like screenshots |
| a PDF (receipt, invoice, order confirmation) | text + link targets read (scans OCR'd); the seller (site named most, not PayPal/Shopify…) checked |
| a product photo with a price but no seller | Couldn't check (no seller) + trusted stores + "price far below trusted stores" warning |
| Facebook / TikTok / Instagram / X / YouTube / Pinterest / Threads links | read (oEmbed, profile followers from search) or Couldn't check + screenshot request |

Every answer: **LEGIT / NOT LEGIT / COULDN'T CHECK** (score ≥ 35 = Not legit; a domain under 30 days = Not legit on its
own) · up to 3 reasons, each with proof (registry date, Safe Browsing, server owner, quotes, Wayback, profiles) ·
"👥 Checked N times this week; M came out Not legit" · on Not legit, "Already paid?" steps by payment method + where to
report · the real brand and trusted places to buy · "Double checking…" if it takes over 15 s.

Open: Replit not redeployed since step 1; deadline time (CDT vs ET) to confirm on Devpost; demo video + Devpost;
phone test on real iMessage last. Sections below this one describe the earlier brand-scanner state.

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
| 2026-10-10 | Brand book applied to the product UI | Re-skinned the existing interface with the supplied Obsidian, Archive Ivory and Alert Crimson system; added the shield lockup, editorial / interface / evidence type hierarchy, numbered investigation fields, archival paper texture, square severity labels and a dark evidence rail. Preserved all IDs and product flows. Browser-checked at 1440 px and 390 px; 183 tests pass. |
| 2026-10-10 | Visual evidence map | Rebuilt the scan graph as an ink-black investigation canvas: large evidence thumbnails, a crimson original node, labeled domain hubs with signal counts, risk-weighted directional paths, live source/signal/high-risk totals, fit controls and animated focus when a table row or node is selected. Added visible running/progress/failure states. Checked with the Lucky Fours scan at desktop and mobile widths; 183 tests pass. |
| 2026-10-10 | Branded investigation sequence | Turned the identity system into the live scan experience: rotating evidence sweep, moving crimson scan line, animated evidence marks, four-stage Discover/Investigate/Verify/Act rail, active primary action, and a live black-ink briefing rail using "The hunt is on" and "Evidence over assumption." Motion reflects real scan state and stops on completion; reduced-motion preferences are respected. |
| 2026-10-10 | Review fixes (10) | Platform names match whole hostname parts (betsyjohnson.com is not Etsy, fedex.com is not x.com); a failing watch waits a full interval; "verified" no longer counts same-product hits; mailto links use %20; same-product leftovers kept at 800 px; newest 50 scans kept in memory; DuckDuckGo image search dropped; seller merges keep the full date range |
| 2026-10-10 | Faster scans | Separate limits for downloads (32) and comparisons (one per core), each image decoded once, duplicate image+page dropped across sources, identical bytes compared once, reverse/marketplace candidates checked first, per-stage timings in the done event and status line. Live "lucky fours" scan: 371 s before, 157 s and 153 s after, same findings. Downloads are now the bottleneck (about 12 s per image summed) |
| 2026-10-10 | Collapsible scan setup | "Hide scan setup" folds the form into a 46 px rail; remembered per browser. Checked in the browser at 1440 px; not checked at phone width |
| 2026-10-10 | Replit + iMessage agent | `.replit` + `start.sh` for Replit. `imessage-agent/` on Photon Spectrum: photo in, top matches + thumbnail out, `report` sends the PDF, `watch` forwards monitoring alerts to the chat. HEIC photos supported (`pillow-heif`). Client checked live against a real scan; agent boots on Spectrum's terminal provider; real iMessage not yet run (needs Photon project credentials) |
| 2026-10-10 | Review of the live test, fixed | One count for every number on screen (stats, map header, live panel, map source boxes, status line all agreed in the browser in every toggle state). The brand's own sites and resellers: rows hidden behind their own toggle, never counted, no takedown; their images are no longer even downloaded. Scans: Lucky Fours 146 s -> 28 s, same findings (the brand's own shop throttled 116 parallel downloads; now skipped), Adidas 45 s. Hosts that time out 3 times are given up for the scan (thumbnails checked instead); 15 s download budget per image. Replay picker lists the demo and history with their findings (`/api/recordings`). Layout: 900 px form no longer a scroll box, map legend/stages no longer overlap at any width (390-1600 checked), phone arrow points up/down, map refits after collapse and after a scan (settled layout, fit computed from node boxes). Email links: notices too long for a mailto now copy the full text to the clipboard instead of a misleading "See attached" body. iMessage: one "Halfway" message during a scan; `ALLOWED_SENDERS` keeps strangers from running scans. 199 Python + 10 agent tests pass |
| 2026-10-10 | Investigation log with server IPs | Every scan (web, monitoring, iMessage) is logged permanently in `data/investigations.db` (SQLite): brand, settings, origin, counts, and every listing found with its site's IP addresses and the network that owns them (free RDAP, cached in `data/ipnet_cache.json`). A listing whose site, URL or server IP is already in the log gets a "seen before" tag, a Server section in the detail panel and a reason in the evidence PDF. Shared platforms (Shopify, Cloudflare, CloudFront and others) are recorded but never count as a link; marketplace and social listings are matched by their URL only (their IPs are the platform's). New "Investigations" tab: servers seen more than once (dedicated first, a CDN site's IPs merged into one row) and past investigations with their listings. API: `/api/investigations`, `/api/investigations/{id}`, `/api/servers`. Live: two Lucky Fours scans then Adidas; the second run flagged wethrift.com and a reddit.com listing as found before, wethrift.com shown on shared Cloudflare. Lookups add about 1 s per scan. 206 Python + 11 agent tests pass |
| 2026-10-10 | Three layers from one link | Built on the URL-first listing flow another session had added (`app/listing.py`, quick check + deep research + evidence lab). Layer 1, check a link: optional brand settings (official domains, resellers) make it say "the brand's own site" / "authorised reseller"; social posts are read with yt-dlp (account, followers, verified badge, date, caption) and checked for off-platform payment (DM to order, WhatsApp, Zelle); a social or marketplace page is no longer judged by tiktok.com's or amazon.com's domain age; brand name inside a longer address flagged; brand-protection registrars (CSC, MarkMonitor...) and a company's own network count as good signs, so adidas.com and allbirds.com come out green even when they block page reads; short links followed; server IP recorded and every check logged (origin "link"); wording matched as whole words, weak words (copy, cheap, outlet) only in titles (a YouTube description with "copyright" / "get your copy" was flagged before). Layer 2, similar listings: Brave instead of the failing DuckDuckGo; retailer pages that block reads (Amazon, Dick's, eBay) kept by marketplace / product-URL pattern; product photo with an "Open in Google Lens" link (checked: opens Lens with the photo); Lens results automatic once SERPAPI_KEY is set; "Scan the web for copies of this photo" runs the full scan on the listing's own photo (`photo_url` on `/api/scan`), with the starting site treated as the brand's own. Layer 3, manufacturers, user's choice (`/api/listing/suppliers`): "Who makes it" (maker named on the page or the site's own name, plus factory listings) and "Where copies are sourced" (wholesale listings on Alibaba, 1688, Made-in-China, DHgate, Global Sources, IndiaMART, searched without the brand, kept only when they share the product's distinctive words). Live on Allbirds Tree Runner: green; similar listings Allbirds, Dick's, Amazon, eBay; sources found a DHgate "TREEPERI" Tree Runner lookalike listing. 230 Python + 11 agent tests pass |

## Test log: browser run 2026-10-10

Tested with real clicks in Chrome (Claude in Chrome, the thiofedor Chrome profile), local app on port 8011, commits 89e1a85 and 808f668 (both on `main`).

### Web app (local): works, with problems
| Check | Result |
|---|---|
| Upload photo, brand "lucky fours", official domain luckyfours.com, Scan | Done in 146 s (search 17 s, checking 127 s, first match at 50 s). 284 candidates: 1 verified, 3 low-res, 11 blocked |
| Findings | One real outside hit: valeen.co, risk 60, "lookalike", low-res check. 0 high risk |
| Collapse "Hide scan setup" at 1366 px | Folds to a 46 px rail, chevron points right, state remembered after reload |
| Collapse at 390 px (app in a 390 px frame; the window could not be resized) | Becomes a full-width "Scan setup" bar above the map, no sideways scroll |
| Takedown draft for valeen.co | Draft generated. No abuse email found, so no mailto link to check the %20 fix in the UI (unit test only) |

Problems found, and their status after the fix round the same day (see "Review of the live test, fixed" above):
1. **Counts disagree on one screen**: counter "4 flagged" vs status line "5 flagged"; map header "2 sources / 4 signals" vs side panel "3 sources / 5 signals". **Fixed**: one shared count; checked in the browser in every toggle state.
2. **The brand's own site appears as matches** in the table and map (3 of 4 rows were luckyfours.com, category "official") and counts toward "flagged", even with the official domain entered. **Fixed**: own-site and reseller pages are skipped before download and only counted; older recordings hide such rows behind "Show your own sites".
3. Weak demo result for this brand (one outside hit, nothing high risk). **Worked around**: the Replay list now offers "Demo: Adidas · 22 flagged, 2 high" (`data/demo.json`). Live results stay weak while Brave Search is out of credit (see Known limits).
4. A scan takes about 2.5 minutes, long for a live demo. **Fixed**: the time went to 116 downloads from luckyfours.com itself, which throttled us. Now 28 s for the same scan, 45 s for Adidas.
5. At about 900 px wide the scan form is squeezed into a small scroll box. **Fixed**: full-height form under 1180 px; map legend and stage strip no longer overlap (checked 390-1600 px).
6. Minor: the map does not re-spread when the form collapses; on phones the chevron points right while the panel opens downward. **Fixed**: the map settles and refits after a collapse and after every scan; the phone arrow points up/down.
7. Found during the fix round: notices too long for an email link were sent with the body "See attached takedown draft and evidence PDFs." although a mailto link cannot attach anything. **Fixed**: "Open in email" copies the full notice to the clipboard and the email body says to paste it. The %20 encoding was confirmed in the browser on a real draft (rarefoot.com, abuse@namecheap.com).

### iMessage agent: logic works live; real iMessage not tested
| Check | Result |
|---|---|
| Agent boots on Spectrum (terminal provider, piped input) | Yes, replies to "help" |
| Full conversation with stand-in chat against the live server, iPhone-style HEIC photo | domains -> photo -> "What's the brand name?" -> brand -> scan (145 s) -> summary + top-match.jpg + PDF report (35 KB) -> watch -> stop. All worked |
| Photo through Spectrum's terminal chat | Not tested (needs drag-and-drop in a real terminal; readline mode passes paths as text) |
| Real iMessage | Not tested. Photon project "brand-guard" created (Free plan, iMessage), but Photon shows "NO PHONE ON YOUR ACCOUNT": a phone number must be added to the Photon account first |

Bugs found and fixed (commit 808f668, `npm test` 6 pass):
- The reply listed the brand's own sites as "matches" (thumbnail and PDF were the brand's own product). Official and reseller matches are now counted, not listed.
- A caption on a later photo did not change the brand.
- Every unrecognised message re-sent the full help text; now a short hint after the first help.

Open weaknesses: shared-pool numbers on the free plan; needs an always-on server. Addressed in the fix round (tests only, real iMessage still untested): one "Halfway" message during a scan, and `ALLOWED_SENDERS` so only listed numbers can run scans (set it before giving out the number).

### Replit: not working yet
- Imported from GitHub as `@portnoif/brand-guard` (Replit account portnoif, chosen by the user; not thiofedor). `main` was fast-forwarded to the test branch first so the import has today's work.
- The workspace stayed on "Loading project..." for over 2 minutes, including after a reload; the Tools panel kept spinning. `start.sh` never ran. **Replit setup unproven.**
- Free Starter plan: "Publish 1 live project". Whether a Reserved VM needs a paid plan was not checked.

### Incident
While closing Replit's "Compare plans" dialog, a click aimed at its "Dismiss" button (found by the page reader, clicked without a screenshot) opened a Stripe checkout for Replit Pro ($100/month). Nothing was typed there and no payment was made; the page was left immediately. **Check Replit billing to confirm no charge.** From then on: screenshot before every click, no billing or plan screens.

### Open items from this run
1. ~~Web app: hide or separate own-site rows, and make the counts agree.~~ Done (fix round).
2. Photon: add a phone number to the account, then test real iMessage.
3. Replit: open the Repl again to see whether it loads; otherwise demo locally.
4. ~~Pick a demo brand with strong real copies, or use the synthetic demo copies.~~ Demo from the "Demo: Adidas" recording; for live scans, restore Brave Search first.
5. ~~Top up Brave Search~~ Done: new prepaid key.

## Calibration results (forensics)

Validated on 34 real Wikimedia photos (1,190 unrelated pairs) plus 159 synthetic labelled pairs.
- Real photos: 0 false positives. Only 3 same-subject pairs match (two Nike logos, two Puma logos, two photos of one watch), which is correct.
- Synthetic set: precision 0.986, recall 0.89.
- Real Adidas copies (narodnatribuna, fabrikbrands) still match. The ACME-vs-wholesale-shoes false positive is gone. shop.overtime.tv, an Overtime hoodie with no Adidas logo that the old thresholds flagged, is now rejected.
- Key settings: homography sanity check ON (real false positives had impossible geometry); TEMPLATE_MIN 0.70 (0.5 flagged 16 unrelated real pairs); INLIER_RATIO_MIN 0.4 (real copies score 0.41-0.51).

## Known limits

- Brave Search API: the old key hit its $1 monthly usage limit on 2026-10-10 (HTTP 402). Replaced the same day with a new prepaid key (one-off credits, automatic reload off): when the balance reaches $0 Brave stops and scans continue on the other sources. Prepaid credits expire one year after purchase. About $0.06 a scan (up to 12 requests at about $5 per 1,000).
- Live Stan Smith rescan with Brave restored (2026-10-10): 537 candidates, no third-party pixel copies. The old and new pipeline were run on the same captured candidate list: identical third-party results; the old code's only extra matches were 3 images on Adidas's own sites, which are now skipped on purpose. So the demo's third-party copies are simply no longer in today's search results.
- Layer 3 supplier search is text search on supplier sites: results are leads, not proof, and many are category pages (labelled "search page"). Matching the product photo against supplier listings is not done yet. Lens results in the app need a SerpApi key (free plan: 250 searches a month, about 80 checks); without it the "Open in Google Lens" link does the same by hand.
- Instagram usually needs a login, so its posts often cannot be read; TikTok and YouTube posts can.
- Server IPs link sites only off shared platforms. Of 8 sample listing sites checked on 2026-10-10, all were on Shopify, Cloudflare, Amazon CloudFront or Alibaba Cloud, where unrelated sites share addresses; such repeats are recorded but not counted as links. Expect real "same server" links mostly from shops on rented servers (VPS / dedicated hosting). The IP is the one at scan time (sites move), and only the first IP's network is looked up.
- Replay history (`data/scans/`) keeps only the newest 20 scans and deletes older files outright; the investigation log keeps every scan's listings but not their images. On 2026-10-10 a new test posted to `/api/scan` and wrote 3 fake "Acme" scans into the real history, which pushed out 3 real ones (`20261010-142251-nike`, `20261010-142737-stanley`, `20261010-143753-apple`); they could not be recovered (no shadow copies or File History). The test now writes to a temporary folder.
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
- `data/last_scan.json` holds a live Adidas Stan Smith test scan from the fix round (0 matches, because Brave was out of credit). Every scan is also kept in `data/scans/` (newest 20) and appears in the Replay list. `data/demo.json` (Adidas Stan Smith, 22 flagged including 5 synthetic copies, 2 high risk) is unchanged and is the one to demo.
- Images on pages of the official domains and authorised resellers are not downloaded, so the app never says whether the brand's own pages carry the image; it only counts them.
- Live search results change from day to day: the third-party Stan Smith copies in `data/demo.json` no longer come back from Bing. Demo from the recording, not a live scan.
- A flat logo is a weak demo image: its copies are mostly logo/wallpaper sites, scored low (correctly). The demo needs a product photo that sellers reuse.

## Plan (decided 2026-10-10)

A mentor from Cox Automotive suggested a car-to-car / car-to-road communication project (V2X: V2V and V2I).
Decision: **finish Brand Guard first; it is the safe project to present.** Then build a V2X prototype. If the V2X
prototype is ready in time, present it; if not, present Brand Guard.
- V2X starting point: real connected-vehicle pilot data on the USDOT ITS DataHub (Wyoming, Tampa, NYC pilots;
  SAE J2735 Basic Safety Messages and traffic-light SPaT/MAP messages).
- Alternative raised but not chosen: retarget Brand Guard to fake car listings that use stolen dealer photos (Cox owns Autotrader / KBB / Manheim).

## Next steps (priority order)

0. ~~Brave Search API: restore it~~ Done 2026-10-10: new prepaid key in `.env`, checked live.
1. Get a SerpApi free key and run one scan with a public image URL to confirm reverse search live.
1b. eBay developer account registered 2026-10-10, **pending approval** (at least one business day). Once approved: create the Production keyset at developer.ebay.com/my/keys (opt out of marketplace account deletion notices, since no eBay user data is stored), put `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` in `.env`, and run one scan to confirm eBay image search live. 
2. Label 20-50 real matches with the UI buttons, then run `python -m app.calibrate --real`.
3. ~~Test same-product search on a real case~~ Done 2026-10-10: 8-10 real same-product listings on the Stan Smith scan.
4. ~~Check the takedown report links~~ Done 2026-10-10 (17 of 18). Remaining: confirm Amazon's form while signed in.
5. ~~Test C2PA with a signed image~~ Done 2026-10-10.
6. ~~Pick a demo product photo~~ Done 2026-10-10 (Stan Smith official photo). The Replay list under "Scan the web" now plays `demo.json` ("Demo: Adidas"), the last scan, or any scan in the history.

## Ideas backlog

| Idea | Value | Effort |
|---|---|---|
| Live listing details: price, seller, "ships from" | Medium | Medium |
| ML detector for AI-generated images (needs a trusted pinned model) | Medium | Medium |
| Batch mode for a whole product catalogue | Medium | Medium |
| Trust list for C2PA signers | Low | Small |
| Run monitoring outside the web process (scheduled task) | Medium | Small |

## Demo checklist

- [x] Pick a branded product photo with known fakes (better than a flat logo): Adidas Stan Smith
- [x] Run a scan with "Add 5 synthetic demo copies" ticked
- [x] Confirm `data/demo.json` holds a good scan for offline replay: 22 flagged, 2 high risk; plays from the Replay list in a few seconds
- [ ] Show the Sellers tab, then "Takedown all" on the top seller, then download the evidence pack
- [ ] Show Monitoring: start a watch and point at the Alerts badge
- [ ] Rehearse the line: "Brands spend millions on creative, then lose control of it online. We find likely copies, show which look fake or stolen, group them by seller, and draft the takedown."
