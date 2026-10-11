// The conversation logic, independent of the messaging platform: agent.ts wires it to Spectrum, and tests drive it
// with stand-in spaces and messages.
import type { Message, Space } from "spectrum-ts";
import { AskError, BrandGuard, replyText } from "./brandguard.js";

export const HELP = "Is it legit? Send me a link, a screenshot of what you're looking at, or the store's name.";

export type BotOptions = {
  bg: BrandGuard;
  allowedSenders?: string[];  // phone numbers or emails that may use the agent; empty or missing: anyone
};

export const PRIVATE = "This Brand Guard agent is private. Ask its owner to add your number.";

/** "+1 (555) 010-2345" and "+15550102345" are the same sender; so are emails in any case. */
export const senderKey = (id: string) => id.toLowerCase().replace(/[\s\-().]/g, "");

export function createBot({ bg, allowedSenders = [] }: BotOptions) {
  const allowed = new Set(allowedSenders.map(senderKey).filter(Boolean));
  const refused = new Set<string>();  // told once that the agent is private; later messages are ignored

  /** The image or video in a message, if any (an iMessage photo with a caption can arrive as a group). */
  async function mediaOf(message: Message): Promise<{ data: Buffer; filename: string; mimeType: string } | null> {
    const items = message.content.type === "group" ? message.content.items.map(m => m.content) : [message.content];
    for (const c of items) {
      if (c.type === "attachment" && /^(image|video)\//.test(c.mimeType)) {
        return { data: await c.read(), filename: c.name || "screenshot", mimeType: c.mimeType };
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

  async function handle(space: Space, message: Message) {
    if (allowed.size && !allowed.has(senderKey(message.sender?.id ?? ""))) {
      const who = senderKey(message.sender?.id ?? space.id);
      if (!refused.has(who)) {
        refused.add(who);
        await space.send(PRIVATE);
      }
      return;
    }
    const media = await mediaOf(message);
    const text = textOf(message);
    const lower = text.toLowerCase();

    if (media?.mimeType.startsWith("video/")) {
      await space.send("I can't read videos. Send a screenshot or the link instead.");
      return;
    }
    if (!media) {
      if (!text) return;
      if (/^(\/?help|hi|hello|hey|start|\?)$/.test(lower)) {
        await space.send(HELP);
        return;
      }
      if (/^(thanks|thank you|thx|ty|ok|okay|cool|great|nice|got it)\b[.!]*$/.test(lower)) {
        await space.send("Anytime. Send another link or screenshot whenever.");
        return;
      }
    }
    try {
      const check = await space.responding(() => bg.ask({ text, image: media ?? undefined }));
      await space.send(replyText(check));
    } catch (e) {
      if (e instanceof AskError) await space.send(e.message);
      else await space.send(`Brand Guard is not reachable right now (${(e as Error).message}). Try again in a minute.`);
    }
  }

  return { handle };
}
