# Brand Guard in iMessage

Text the agent a product photo, logo or short video. It scans the web for copies with Brand Guard and replies with
the worst matches, a thumbnail of the top one, and (on request) the PDF evidence report. Send `watch` and it re-checks
the photo every day and messages you when new copies appear.

Built on [Photon Spectrum](https://photon.codes/docs/spectrum-ts/introduction) (`spectrum-ts`, MIT). The agent is a
thin TypeScript service; all scanning happens in the Brand Guard API (`../app`).

## Conversation

| You send | The agent |
|---|---|
| a photo (with the brand as caption, or answer "What's the brand name?") | scans (about 2-3 minutes, typing indicator on, one "Halfway" update), replies with counts and the top 3 matches |
| `brand Acme` | sets the brand; rescans the last photo if there is one |
| `domains acme.com, shop.acme.com` | your own sites, never flagged |
| `report` | the PDF evidence report for the top match |
| `watch` / `stop` | daily monitoring of the last photo; alerts arrive in this chat |
| `help` | the instructions |

iPhone photos arrive as HEIC; Brand Guard reads them through `pillow-heif`.

## Run it

1. Start Brand Guard: `python -m uvicorn app.main:app --port 8000` from the repo root (or `bash start.sh`).
2. In this folder: `npm install`, then `npm start`.

Without credentials the agent opens Spectrum's terminal chat: drag a photo into it to test the whole flow locally.

For real iMessage, create a project on the [Photon dashboard](https://app.photon.codes) (the free plan routes chats
through a shared pool of numbers) and set:

| Variable | Meaning |
|---|---|
| `SPECTRUM_PROJECT_ID`, `SPECTRUM_PROJECT_SECRET` | project credentials; their presence switches the agent from terminal to iMessage |
| `BRAND_GUARD_URL` | Brand Guard API, default `http://127.0.0.1:8000` |
| `PUBLIC_BASE_URL` | public address of the web app, linked under each result (set automatically on Replit) |
| `ALLOWED_SENDERS` | optional: phone numbers or emails that may use the agent, comma separated (`+1 555 010 2345, me@example.com`). Anyone else is told once that the agent is private, and their messages never start a scan. Set it before giving out the number: every scan uses your search API keys |
| `AGENT_TERMINAL=1` | force the terminal chat even when credentials are set |

On Replit, `bash start.sh` runs Brand Guard and this agent together; put the two Spectrum values in Replit Secrets.

## Checks

`npm test` (summary text and stream parsing) and `npm run typecheck`.

Chat settings and watch ownership are kept in `state.json` (git-ignored); the last photo per chat is kept in memory only.
