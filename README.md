# Brand Guard

**Is it legit? Text it before you trust it.**

Brand Guard checks a store before you pay. Send it a link, a store's name, a screenshot, a screen recording, a receipt
PDF or a product photo, in iMessage or on the web page, and it answers like a friend who did the homework:

- **LEGIT ✅**, **NOT LEGIT ❌** or **COULDN'T CHECK ⚠️**, never a guess dressed up as an answer;
- **up to three reasons, each with proof** you can open yourself (the domain's registration date, Google Safe
  Browsing's report, a quote of the words in your screenshot, the store's reviews and followers);
- **the real brand and trusted places to buy**, with their prices;
- **what to do if you already paid**, for the payment method the seller asked for (card, PayPal, Zelle, Cash App,
  Venmo, crypto, gift cards);
- **a crowd signal**: "👥 Checked 37 times this week; 31 came out Not legit."

Built at **Hacklanta '26** (Oct 9–11, 2026). Demo video, pitch and judge notes are on the Devpost page.

## Why

Americans reported losing **$15.9 billion** to fraud in 2025 (FTC). Scams that started on social media cost
**$2.1 billion**, eight times more than in 2020, and fake shopping ads are the most reported kind (FTC Data Spotlight,
April 2026). Generative AI makes fake stores look exactly like real ones; Deloitte expects AI-enabled fraud losses in
the US to reach **$40 billion by 2027**. The moment that matters is the few seconds before someone pays. Brand Guard
lives where that moment happens: in the messages app, next to the link a friend just sent.

## What it checks

| Signal | Source |
|---|---|
| Domain age and registrar (a domain under 30 days old is Not legit on its own) | RDAP |
| Known phishing / malware / social engineering | Google Safe Browsing, PhishStats |
| Server owner and hosting | DNS + IP lookups |
| The page itself: what it sells, how it asks to be paid ("DM to order, Zelle only") | page fetch, Wayback Machine when the live page hides |
| Reviews, followers, discussion threads about the store | Brave Search, You.com, DuckDuckGo |
| Same product photo on other shops; price vs trusted stores | reverse image search, web search |
| Social posts and accounts (Instagram, TikTok, Facebook, X, YouTube, Pinterest, Threads) | oEmbed, search snippets |
| Text in screenshots, video frames and scanned PDFs | RapidOCR (on device), OpenCV, PyMuPDF |
| What other people asked about the same site this week | Brand Guard's own log |

The rules are plain code with tests (`app/listing.py`, `app/ask.py`, `app/paid.py`): a score of 35 or more is Not
legit, and Couldn't check is used only when there is nothing to judge by.

## Try it

**Web page:** run the server (below) and open http://localhost:8000. Paste a link, type a store's name, or attach a
screenshot, video or PDF. The "Try" buttons run three real examples, including a known phishing test page.

**iMessage:** the agent in [`imessage-agent/`](imessage-agent/README.md) runs on
[Photon Spectrum](https://photon.codes/docs/spectrum-ts/introduction). Without Spectrum credentials it opens a
terminal chat for local testing; paste a link or a file's path there.

## Run it

Python 3.10+ (tested on 3.12) and Node 20+.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt            # Windows: .venv\Scripts\pip ...
.venv/bin/pip install --no-deps rapidocr_onnxruntime  # screenshot reading
.venv/bin/python -m uvicorn app.main:app --port 8000  # http://localhost:8000

cd imessage-agent && npm install && npm start         # iMessage agent (terminal chat without credentials)
```

Or `bash start.sh`, which does all of it (it is what Replit runs).

Keys go in `.env` in the repo root (git-ignored). Everything works without them, with fewer sources:

| Variable | What it adds |
|---|---|
| `BRAVE_API_KEY` | web search for reviews, followers and the store's own site |
| `YDC_API_KEY` | You.com web search (second source) |
| `GOOGLE_SAFE_BROWSING_KEY` | phishing and malware lists |
| `SERPAPI_KEY` | reverse image search |
| `SPECTRUM_PROJECT_ID`, `SPECTRUM_PROJECT_SECRET` | real iMessage instead of the terminal chat |
| `ALLOWED_SENDERS` | optional: only these phone numbers / emails may use the iMessage agent |

## Tests

```bash
.venv/bin/python -m pytest tests -q     # 324 tests, offline (network sources are switched off in tests/conftest.py)
cd imessage-agent && npm test           # agent tests
```

CI runs both on every pull request (`.github/workflows`).

## Layout

```
app/              FastAPI server
  ask.py          one question in, one answer out: links, names, screenshots, videos, PDFs, photos
  listing.py      the checks and the verdict for a site or a social account
  paid.py         "Already paid?" steps per payment method
  investigations.py  the crowd log
  rdap.py, infra.py, reputation.py, archive.py, search.py   the sources
static/ask.html   the web page
imessage-agent/   the iMessage agent (TypeScript, Photon Spectrum)
tests/            pytest suite
PROJECT_LOG.md, session-logs/   how it was built, step by step
```

The repo also holds the project's first idea, still reachable at `/lab`: a brand uploads a product image, Brand Guard
finds copies across the web, groups them by seller and drafts takedown notices.

## Limits

This is a hackathon prototype. Answers report observable signals, not a guarantee or a legal finding. Some
platforms hide posts behind a login; then the agent asks for a screenshot. Search APIs have free-tier limits.

## Built with

Python, FastAPI, httpx, OpenCV, PyMuPDF, RapidOCR, Pillow, BeautifulSoup, yt-dlp, TypeScript, Node,
[Photon Spectrum](https://photon.codes), Google Safe Browsing, RDAP, Brave Search, You.com, DuckDuckGo, Wayback
Machine, GitHub Actions. Written with help from AI coding assistants (Claude Code).
