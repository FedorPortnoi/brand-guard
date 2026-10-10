// The conversation logic, independent of the messaging platform: agent.ts wires it to Spectrum, and tests drive it
// with stand-in spaces and messages.
import { readFileSync, writeFileSync } from "node:fs";
import { attachment, type Message, type Space } from "spectrum-ts";
import { BrandGuard, evidenceMatches, summarize, type ScanRequest } from "./brandguard.js";

export const HELP = [
  "Brand Guard finds copies of your product photos online.",
  "",
  "1. Send me a product photo, logo or short video.",
  "2. Tell me the brand name (or send \"brand Acme\" first).",
  "I check a few hundred images across search engines and marketplaces (about 2-3 minutes) and send the worst matches.",
  "",
  "Other commands:",
  "domains acme.com, shop.acme.com: your own sites, never flagged",
  "watch: re-check the last photo every day and message me new copies",
  "stop: stop watching",
  "report: the PDF evidence report for the top match",
].join("\n");


export type Chat = {
  platform: string;
  spaceId: string;
  brand?: string;
  domains?: string;
  watchIds: string[];
  lastScanId?: string;
  lastTopIdx?: number;
  greeted?: boolean;
};
export type State = { chats: Record<string, Chat>; forwardedAlerts: string[] };

export type BotOptions = {
  bg: BrandGuard;
  appUrl?: string;  // public web app, linked under results
  statePath: string | URL;  // chat settings and watch ownership (JSON)
  lookupSpace: (id: string) => Promise<Space | undefined>;  // a chat by id, to deliver alerts after a restart
};

export function createBot({ bg, appUrl: APP_URL = "", statePath, lookupSpace }: BotOptions) {
  const state: State = (() => {
    try {
      return JSON.parse(readFileSync(statePath, "utf8")) as State;
    } catch {
      return { chats: {}, forwardedAlerts: [] };
    }
  })();
  const saveState = () => writeFileSync(statePath, JSON.stringify(state, null, 2));

  // kept in memory only: the last photo per chat, and the live Space handle to reply into
  const photos = new Map<string, Omit<ScanRequest, "brand" | "officialDomains">>();
  const spaces = new Map<string, Space>();
  const busy = new Set<string>();

  const chatKey = (platform: string, spaceId: string) => `${platform}:${spaceId}`;

  function chatFor(space: Space, platform: string): Chat {
    const key = chatKey(platform, space.id);
    spaces.set(key, space);
    return (state.chats[key] ??= { platform, spaceId: space.id, watchIds: [] });
  }

  /** The image or video in a message, if any (an iMessage photo with a caption can arrive as a group). */
  async function mediaOf(message: Message): Promise<{ image: Buffer; filename: string; mimeType: string } | null> {
    const items = message.content.type === "group" ? message.content.items.map(m => m.content) : [message.content];
    for (const c of items) {
      if (c.type === "attachment" && /^(image|video)\//.test(c.mimeType)) {
        return { image: await c.read(), filename: c.name || "photo", mimeType: c.mimeType };
      }
    }
    return null;
  }

  function textOf(message: Message): string {
    if (message.content.type === "text") return message.content.text.trim();
    if (message.content.type === "group") {
      const t = message.content.items.find(m => m.content.type === "text");
      if (t && t.content.type === "text") return t.content.text.trim();
    }
    return "";
  }

  async function runScan(space: Space, chat: Chat) {
    const key = chatKey(chat.platform, chat.spaceId);
    const photo = photos.get(key);
    if (!photo || !chat.brand) return;
    if (busy.has(key)) {
      await space.send("A scan is already running for this chat. I'll message you when it's done.");
      return;
    }
    busy.add(key);
    try {
      await space.send(`Scanning the web for copies of your ${chat.brand} image. This takes about 2-3 minutes.`);
      const result = await space.responding(() => bg.scan({ ...photo, brand: chat.brand!, officialDomains: chat.domains }));
      const top = evidenceMatches(result)[0];
      chat.lastScanId = result.scanId;
      chat.lastTopIdx = top?.idx;
      saveState();
      await space.send(summarize(result, chat.brand, APP_URL));
      if (top?.thumb?.startsWith("data:image/jpeg;base64,")) {
        const jpeg = Buffer.from(top.thumb.split(",", 2)[1], "base64");
        await space.send(attachment(jpeg, { name: "top-match.jpg", mimeType: "image/jpeg" }));
      }
      if (top) await space.send("Reply \"report\" for the PDF evidence report, or \"watch\" to check this photo daily.");
    } catch (e) {
      await space.send(`Brand Guard is not reachable right now (${(e as Error).message}). Try again in a minute.`);
    } finally {
      busy.delete(key);
    }
  }

  async function handle(space: Space, message: Message) {
    const chat = chatFor(space, message.platform);
    const key = chatKey(chat.platform, chat.spaceId);
    const media = await mediaOf(message);
    const text = textOf(message);
    const lower = text.toLowerCase();

    if (media) {
      photos.set(key, media);
      if (text) chat.brand = text;  // a caption on a photo names its brand
      saveState();
      if (!chat.brand) {
        await space.send("Got the photo. What's the brand name?");
        return;
      }
      await runScan(space, chat);
      return;
    }

    if (!text) return;
    if (/^(\/?help|hi|hello|hey|start|\?)$/.test(lower)) {
      await space.send(HELP);
    } else if (lower.startsWith("brand ")) {
      chat.brand = text.slice(6).trim();
      saveState();
      if (photos.has(key)) await runScan(space, chat);
      else await space.send(`Brand set to ${chat.brand}. Now send me the photo to check.`);
    } else if (lower.startsWith("domains ")) {
      chat.domains = text.slice(8).trim();
      saveState();
      await space.send(`Got it. I won't flag ${chat.domains}.`);
    } else if (lower === "report") {
      const pdf = chat.lastScanId != null && chat.lastTopIdx != null ? await bg.report(chat.lastScanId, chat.lastTopIdx) : null;
      if (pdf) await space.send(attachment(pdf, { name: "brand-guard-evidence.pdf", mimeType: "application/pdf" }));
      else await space.send("No report available. Run a scan that finds a match first.");
    } else if (lower === "watch") {
      const photo = photos.get(key);
      if (!photo || !chat.brand) {
        await space.send("Send me the photo (and brand name) to watch first.");
        return;
      }
      const watch = await bg.createWatch({ ...photo, brand: chat.brand, officialDomains: chat.domains });
      chat.watchIds.push(watch.id);
      saveState();
      await space.send(`Watching ${chat.brand}: I'll re-check every day and message you here when new copies appear. Send "stop" to end.`);
    } else if (lower === "stop") {
      await Promise.all(chat.watchIds.map(id => bg.deleteWatch(id)));
      const n = chat.watchIds.length;
      chat.watchIds = [];
      saveState();
      await space.send(n ? "Stopped watching." : "Nothing is being watched in this chat.");
    } else if (photos.has(key) && !chat.brand) {
      chat.brand = text;  // the answer to "What's the brand name?"
      saveState();
      await runScan(space, chat);
    } else if (photos.has(key)) {
      await space.send(`Send a new photo to scan, "brand <name>" to change the brand (now ${chat.brand}), or "help".`);
    } else if (!chat.greeted) {
      chat.greeted = true;
      saveState();
      await space.send(HELP);
    } else {
      await space.send("Send me a product photo to check, or \"help\" for the commands.");
    }
  }

  async function spaceFor(chat: Chat): Promise<Space | undefined> {
    const key = chatKey(chat.platform, chat.spaceId);
    if (spaces.has(key)) return spaces.get(key);
    try {
      const space = await lookupSpace(chat.spaceId);
      if (space) spaces.set(key, space);
      return space;
    } catch {
      return undefined;
    }
  }

  /** Forward monitoring alerts for watches created from a chat back into that chat. */
  async function forwardAlerts() {
    const owner = new Map<string, Chat>();
    for (const chat of Object.values(state.chats)) for (const id of chat.watchIds) owner.set(id, chat);
    if (!owner.size) return;
    const forwarded = new Set(state.forwardedAlerts);
    for (const alert of (await bg.alerts()).reverse()) {  // oldest first
      const chat = owner.get(alert.watch_id);
      if (!chat || forwarded.has(alert.id)) continue;
      const space = await spaceFor(chat);
      if (!space) continue;
      await space.send(`Brand Guard alert for ${alert.brand}: ${alert.message}${APP_URL ? `\n${APP_URL}` : ""}`);
      forwarded.add(alert.id);
    }
    state.forwardedAlerts = [...forwarded].slice(-500);
    saveState();
  }

  return { handle, forwardAlerts, state };
}
