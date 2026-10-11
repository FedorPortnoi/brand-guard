// The conversation logic, independent of the messaging platform: agent.ts wires it to Spectrum, and tests drive it
// with stand-in spaces and messages.
import { readFile, stat } from "node:fs/promises";
import { basename, extname } from "node:path";
import type { Message, Space } from "spectrum-ts";
import { AskError, BrandGuard, replyText } from "./brandguard.js";

export const HELP = "Is it legit? Send me a link, a screenshot of what you're looking at, or the store's name.";

export type BotOptions = {
  bg: BrandGuard;
  allowedSenders?: string[];  // phone numbers or emails that may use the agent; empty or missing: anyone
  slowAfterMs?: number;  // an answer not ready by then gets a DOUBLE_CHECKING note first (owner, 10.10: 15 s)
  /** Terminal testing only: a message that is the path of a file on this computer is sent as that file
   *  (dragging files into the terminal chat doesn't work everywhere). Never on iMessage. */
  localFiles?: boolean;
};

const MIME: Record<string, string> = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".heic": "image/heic", ".heif": "image/heif",
  ".webp": "image/webp", ".gif": "image/gif", ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4",
  ".webm": "video/webm", ".pdf": "application/pdf",
};

/** The file a pasted path points to ("D:\shots\post.png", with or without quotes), if it exists and is a kind we read. */
export async function localFile(text: string): Promise<{ data: Buffer; filename: string; mimeType: string } | null> {
  const path = text.trim().replace(/^["']|["']$/g, "");
  const mimeType = MIME[extname(path).toLowerCase()];
  if (!mimeType || !/^([a-zA-Z]:[\\/]|\/|~\/|\\\\)/.test(path)) return null;
  try {
    if (!(await stat(path)).isFile()) return null;
    return { data: await readFile(path), filename: basename(path), mimeType };
  } catch {
    return null;
  }
}

/** Sent once when an answer takes longer than slowAfterMs, so a long check doesn't look like a frozen chat. */
export const DOUBLE_CHECKING = "Double checking…";

export const PRIVATE = "This Brand Guard agent is private. Ask its owner to add your number.";

/** "+1 (555) 010-2345" and "+15550102345" are the same sender; so are emails in any case. */
export const senderKey = (id: string) => id.toLowerCase().replace(/[\s\-().]/g, "");

export function createBot({ bg, allowedSenders = [], slowAfterMs = 15_000, localFiles = false }: BotOptions) {
  const allowed = new Set(allowedSenders.map(senderKey).filter(Boolean));
  const refused = new Set<string>();  // told once that the agent is private; later messages are ignored

  /** The image, video or PDF in a message, if any (an iMessage photo with a caption can arrive as a group). */
  async function mediaOf(message: Message): Promise<{ data: Buffer; filename: string; mimeType: string } | null> {
    const items = message.content.type === "group" ? message.content.items.map(m => m.content) : [message.content];
    for (const c of items) {
      if (c.type === "attachment" && (/^(image|video)\//.test(c.mimeType) || c.mimeType === "application/pdf")) {
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
    let media = await mediaOf(message);
    let text = textOf(message);
    if (!media && localFiles && (media = await localFile(text))) text = "";
    const lower = text.toLowerCase();

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
    let notice: Promise<unknown> | undefined;
    const timer = setTimeout(() => { notice = space.send(DOUBLE_CHECKING).catch(() => {}); }, slowAfterMs);
    let reply: string;
    try {
      reply = replyText(await space.responding(() => bg.ask({ text, image: media ?? undefined })));
    } catch (e) {
      reply = e instanceof AskError ? e.message
        : `Brand Guard is not reachable right now (${(e as Error).message}). Try again in a minute.`;
    } finally {
      clearTimeout(timer);
    }
    await notice;  // the note, if it went out, always arrives before the answer
    await space.send(reply);
  }

  return { handle };
}
