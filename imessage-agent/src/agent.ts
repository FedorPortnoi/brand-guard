// Brand Guard in iMessage: text the agent a product photo, it scans the web for copies and replies with the worst
// matches. "watch" turns the photo into a daily monitoring watch whose alerts arrive in the same chat.
//
// Runs on Photon's Spectrum SDK. With SPECTRUM_PROJECT_ID / SPECTRUM_PROJECT_SECRET set it answers real iMessages;
// without them (or with AGENT_TERMINAL=1) it opens Spectrum's terminal chat, where you drag a photo in to test.
import { Spectrum, type Message, type Space } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";
import { terminal } from "spectrum-ts/providers/terminal";
import { BrandGuard } from "./brandguard.js";
import { createBot } from "./bot.js";

const BRAND_GUARD_URL = (process.env.BRAND_GUARD_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const ALERT_POLL_MS = 60_000;
const cloud = !!(process.env.SPECTRUM_PROJECT_ID && process.env.SPECTRUM_PROJECT_SECRET) && process.env.AGENT_TERMINAL !== "1";

// credentials come from SPECTRUM_PROJECT_ID / SPECTRUM_PROJECT_SECRET, which Spectrum reads itself
let inbox: AsyncIterable<[Space, Message]>;
let lookupSpace: (id: string) => Promise<Space | undefined>;
if (cloud) {
  const app = await Spectrum({ providers: [imessage.config()] });
  inbox = app.messages as AsyncIterable<[Space, Message]>;
  lookupSpace = id => imessage(app).space.get(id) as Promise<Space | undefined>;
} else {
  const app = await Spectrum({ providers: [terminal.config({ commands: [{ name: "/help", description: "How to use Brand Guard" }] })] });
  inbox = app.messages as AsyncIterable<[Space, Message]>;
  lookupSpace = id => terminal(app).space.get(id) as Promise<Space | undefined>;
}
console.log(`Brand Guard agent on ${cloud ? "iMessage (Spectrum Cloud)" : "the terminal"}, API ${BRAND_GUARD_URL}`);

const bot = createBot({
  bg: new BrandGuard(BRAND_GUARD_URL),
  appUrl: (process.env.PUBLIC_BASE_URL || "").replace(/\/$/, ""),
  statePath: process.env.AGENT_STATE_FILE || new URL("../state.json", import.meta.url),
  lookupSpace,
  allowedSenders: (process.env.ALLOWED_SENDERS || "").split(",").map(s => s.trim()).filter(Boolean),
});

setInterval(() => bot.forwardAlerts().catch(e => console.error("alert forwarding failed:", e)), ALERT_POLL_MS);

for await (const [space, message] of inbox) {
  if (message.direction === "outbound") continue;
  // each conversation is handled on its own, so one chat's 3-minute scan never blocks another chat
  bot.handle(space, message).catch(async e => {
    console.error("message handling failed:", e);
    await space.send("Something went wrong on my side. Try again, or send \"help\".").catch(() => {});
  });
}
