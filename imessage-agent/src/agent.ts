// Brand Guard in iMessage: "is this legit?" Text the agent a link, a screenshot of what you're looking at, or a
// store's name; it checks the site or account and replies LEGIT / NOT LEGIT / COULDN'T CHECK with the reasons.
//
// Runs on Photon's Spectrum SDK. With SPECTRUM_PROJECT_ID / SPECTRUM_PROJECT_SECRET set it answers real iMessages;
// without them (or with AGENT_TERMINAL=1) it opens Spectrum's terminal chat, where you drag a screenshot in to test.
import { Spectrum, type Message, type Space } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";
import { terminal } from "spectrum-ts/providers/terminal";
import { BrandGuard } from "./brandguard.js";
import { createBot, SETTLE_MS } from "./bot.js";

const BRAND_GUARD_URL = (process.env.BRAND_GUARD_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const cloud = !!(process.env.SPECTRUM_PROJECT_ID && process.env.SPECTRUM_PROJECT_SECRET) && process.env.AGENT_TERMINAL !== "1";

// credentials come from SPECTRUM_PROJECT_ID / SPECTRUM_PROJECT_SECRET, which Spectrum reads itself
const app = cloud
  ? await Spectrum({ providers: [imessage.config()] })
  : await Spectrum({ providers: [terminal.config({ commands: [{ name: "/help", description: "How to use Brand Guard" }] })] });
const inbox = app.messages as AsyncIterable<[Space, Message]>;
console.log(`Brand Guard agent on ${cloud ? "iMessage (Spectrum Cloud)" : "the terminal"}, API ${BRAND_GUARD_URL}`);

const bot = createBot({
  bg: new BrandGuard(BRAND_GUARD_URL),
  allowedSenders: (process.env.ALLOWED_SENDERS || "").split(",").map(s => s.trim()).filter(Boolean),
  openToAll: process.env.OPEN_TO_ALL === "1",
  checksPerHour: Number(process.env.CHECKS_PER_HOUR ?? 20),
  localFiles: !cloud,  // terminal testing: paste a file's path to send it
  settleMs: SETTLE_MS,
});

for await (const [space, message] of inbox) {
  if (message.direction === "outbound") continue;
  // each conversation is handled on its own, so one chat's slow check never blocks another chat
  bot.handle(space, message).catch(async e => {
    console.error("message handling failed:", e);
    await space.send("Something went wrong on my side. Try again, or send \"help\".").catch(() => {});
  });
}
