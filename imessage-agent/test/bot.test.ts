import assert from "node:assert/strict";
import { test } from "node:test";
import type { Message, Space } from "spectrum-ts";
import { AskError, type AskRequest, type BrandGuard } from "../src/brandguard.js";
import { createBot, HELP, PRIVATE } from "../src/bot.js";
import { check } from "./fixtures.js";

/** A chat that records what the bot sends. */
export function fakeSpace(id = "chat-1") {
  const sent: string[] = [];
  const space = {
    id,
    send: async (...items: any[]) => { for (const c of items) sent.push(String(c)); return undefined; },
    responding: async (fn: () => unknown) => fn(),
  } as unknown as Space;
  return { space, sent };
}

const from = (id?: string) => (id ? { sender: { __platform: "terminal", id } } : {});
export const text = (t: string, sender?: string) =>
  ({ platform: "terminal", direction: "inbound", content: { type: "text", text: t }, ...from(sender) }) as unknown as Message;
export const media = (caption = "", mimeType = "image/heic") => {
  const att = { type: "attachment", id: "a1", name: "IMG_0001.HEIC", mimeType, read: async () => Buffer.from("img") };
  const content = caption
    ? { type: "group", items: [{ content: att }, { content: { type: "text", text: caption } }] }
    : att;
  return { platform: "terminal", direction: "inbound", content } as unknown as Message;
};

function stubBg(answer: (req: AskRequest) => ReturnType<BrandGuard["ask"]> = async () => check("red", [])) {
  const calls: string[] = [];
  const bg = {
    ask: async (req: AskRequest) => {
      calls.push(`ask "${req.text ?? ""}"${req.image ? ` ${req.image.mimeType}` : ""}`);
      return answer(req);
    },
  };
  return { bg: bg as unknown as BrandGuard, calls };
}

test("a link, a screenshot (with or without a caption) and a name are each checked once", async () => {
  const { bg, calls } = stubBg();
  const { space, sent } = fakeSpace();
  const bot = createBot({ bg });
  await bot.handle(space, text("https://luxe-kicks.store/p"));
  await bot.handle(space, media());
  await bot.handle(space, media("is this real?"));
  await bot.handle(space, text("is Lucky Fours legit?"));
  assert.deepEqual(calls, ['ask "https://luxe-kicks.store/p"', 'ask "" image/heic', 'ask "is this real?" image/heic',
                           'ask "is Lucky Fours legit?"']);
  assert.equal(sent.length, 4);
  assert.ok(sent.every(s => s.startsWith("NOT LEGIT ❌")));
});

test("help, thanks and videos never cost a check", async () => {
  const { bg, calls } = stubBg();
  const { space, sent } = fakeSpace();
  const bot = createBot({ bg });
  await bot.handle(space, text("hi"));
  await bot.handle(space, text("Thanks!"));
  await bot.handle(space, media("", "video/quicktime"));
  assert.deepEqual(calls, []);
  assert.deepEqual(sent, [HELP, "Anytime. Send another link or screenshot whenever.",
                          "I can't read videos. Send a screenshot or the link instead."]);
});

test("the server's own explanation is passed on; an outage says so", async () => {
  const { space, sent } = fakeSpace();
  const said = "I couldn't find a web address or an @account in that screenshot. Send me the link, or the store's name.";
  await createBot({ bg: stubBg(async () => { throw new AskError(said); }).bg }).handle(space, media());
  await createBot({ bg: stubBg(async () => { throw new Error("fetch failed"); }).bg }).handle(space, text("x.com/a"));
  assert.deepEqual(sent, [said, "Brand Guard is not reachable right now (fetch failed). Try again in a minute."]);
});

test("with an allow list, other senders are told once that the agent is private and never cost a check", async () => {
  const { bg, calls } = stubBg();
  const { space, sent } = fakeSpace();
  const bot = createBot({ bg, allowedSenders: ["+1 (555) 010-2345"] });
  const stranger = { ...media(), sender: { __platform: "terminal", id: "+15559999999" } } as unknown as Message;
  await bot.handle(space, stranger);
  await bot.handle(space, text("hi", "+15559999999"));
  assert.deepEqual(sent, [PRIVATE]);
  assert.deepEqual(calls, []);
  await bot.handle(space, text("hi", "+15550102345"));
  assert.equal(sent.at(-1), HELP);
});
