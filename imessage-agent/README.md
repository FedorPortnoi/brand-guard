# Brand Guard in iMessage

"Is this legit?" Text the agent what you're about to buy from: a link, a screenshot of a post or shop, or just the
store's name. It checks the site or account and replies like a friend would:

```
NOT LEGIT ❌  luxe-kicks.store

- The domain was registered 4 days ago.
  ↳ Registered 2026-10-06: https://lookup.icann.org/en/lookup?name=luxe-kicks.store
- Asks buyers to pay or talk elsewhere: zelle. That removes buyer protection.
  ↳ "DM to order, Zelle only" (from your screenshot)
👥 Checked 5 times this week; 3 came out Not legit.

Don't pay or share your details.

Already paid?
- Zelle: call your bank now and report it as fraud. Zelle works like cash, so speed matters.
- Report it to the FTC: https://reportfraud.ftc.gov
- Report the site to Google: https://safebrowsing.google.com/safebrowsing/report_phish/

The real Adidas: https://www.adidas.com/us
Other places to buy it:
- dickssportinggoods.com: https://www.dickssportinggoods.com/p/...
```

Built on [Photon Spectrum](https://photon.codes/docs/spectrum-ts/introduction) (`spectrum-ts`, MIT). The agent is a
thin TypeScript service; every check happens in the Brand Guard API (`POST /api/listing/ask` in `../app`).

## What it answers

- **Three verdicts.** `LEGIT ✅` / `NOT LEGIT ❌` (risk score 35 or more; a domain under 30 days old is enough on its
  own) / `COULDN'T CHECK ⚠️` (only when there's nothing to judge by: no page, live or archived, no domain age, no
  threat report, nothing about the store elsewhere on the web).
- **Reasons with proof.** Up to three reasons, each with its proof on the line below (`↳`): the registration date,
  Google Safe Browsing's report, the server's owner, a quote of the words seen, the archived copy, the store's
  followers / review page / discussion thread.
- **Crowd signal.** `👥 Checked N times this week; M came out Not legit.` once someone else has checked the site too.
- **Already paid?** Only on Not legit: what to do for each payment method seen (card, PayPal, Zelle, Cash App, Venmo,
  crypto, gift card), then where to report it.
- **The real brand and other places to buy**, on every answer: only stores rated Legit; never for a page that sells
  nothing, and never guessed from a marketplace's or social site's own description.
- **Several things in one message** ("is Lucky Fours legit? and kith.com"): up to three, one answer each, separated
  by `———`.
- **Slow answers.** If an answer isn't ready after 15 seconds, it first sends "Double checking…". Most answers take
  2–40 seconds.
- **Posts it can't see.** Instagram and others usually hide posts behind a login. Then the answer is Couldn't check
  and it asks for a screenshot of the post, which it reads (text, account name) and checks instead.

## Conversation

| You send | The agent |
|---|---|
| a link (a shop, a product, a post, an account) | checks it |
| a screenshot (iPhone HEIC is fine), with or without a caption | reads the text, finds the address or @account, checks it; the words in the screenshot count too ("DM to order, Zelle only") |
| a store's name ("is Lucky Fours legit?") | finds the store's own site with a web search, checks it |
| `hi`, `help` | how to use it |
| `thanks`, `ok` | a short reply, no check |
| a video | asks for a screenshot or the link instead |

## Run it

1. Start Brand Guard from the repo root: `.venv\Scripts\python -m uvicorn app.main:app --port 8000` (or `bash start.sh`).
2. In this folder: `npm install`, then `npm start`.

Without credentials the agent opens Spectrum's terminal chat: type a link or drag a screenshot in to test the whole
flow locally. Restart both after any code change.

For real iMessage, create a project on the [Photon dashboard](https://app.photon.codes) (the free plan routes chats
through a shared pool of numbers) and set:

| Variable | Meaning |
|---|---|
| `SPECTRUM_PROJECT_ID`, `SPECTRUM_PROJECT_SECRET` | project credentials; their presence switches the agent from terminal to iMessage |
| `BRAND_GUARD_URL` | Brand Guard API, default `http://127.0.0.1:8000` |
| `ALLOWED_SENDERS` | optional: phone numbers or emails that may use the agent, comma separated (`+1 555 010 2345, me@example.com`). Anyone else is told once that the agent is private, and their messages never start a check. Set it before giving out the number: every check uses your search API keys |
| `OPEN_TO_ALL` | optional: `1` lets anyone use the agent (judging day); `ALLOWED_SENDERS` then lists the owners, who are never capped |
| `CHECKS_PER_HOUR` | optional, default `20`: checks one sender may run per hour (owners excepted); after that they're told how many minutes to wait. `0`: no cap |
| `AGENT_TERMINAL=1` | force the terminal chat even when credentials are set |

On Replit, `bash start.sh` runs Brand Guard and this agent together; put the two Spectrum values in Replit Secrets.

## Checks

`npm test` (reply text, conversation flow, the 15-second note) and `npm run typecheck`. CI runs both on every pull
request, next to the Python tests.

## Known limits

- Terminal chat shows links with escape codes (`]8;;…`) when copied; that's the terminal's display, not the reply.
- A photo with no text (no address or @account visible) can't be checked yet: it needs reverse image search.
- A fake shop that blocks reading and only hints at a brand in its address (`adidas-outlet.shop`) doesn't get "the real
  brand" yet.
