import assert from "node:assert/strict";
import { test } from "node:test";
import type { Message, Space } from "spectrum-ts";
import { AskError, type AskRequest, type BrandGuard } from "../src/brandguard.js";
import { createBot, DOUBLE_CHECKING, HELP, PRIVATE } from "../src/bot.js";
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

test("help and thanks never cost a check", async () => {
  const { bg, calls } = stubBg();
  const { space, sent } = fakeSpace();
  const bot = createBot({ bg });
  await bot.handle(space, text("hi"));
  await bot.handle(space, text("Thanks!"));
  assert.deepEqual(calls, []);
  assert.deepEqual(sent, [HELP, "Anytime. Send another link or screenshot whenever."]);
});

test("a video (a screen recording, a saved reel) is checked like a screenshot", async () => {
  const { bg, calls } = stubBg();
  const { space, sent } = fakeSpace();
  await createBot({ bg }).handle(space, media("is this legit?", "video/quicktime"));
  assert.deepEqual(calls, ['ask "is this legit?" video/quicktime']);
  assert.equal(sent.length, 1);
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

const wait = (ms: number) => new Promise(r => setTimeout(r, ms));

test("an answer slower than the limit gets one \"Double checking…\" first; a quick one doesn't", async () => {
  const slow = stubBg(async () => { await wait(80); return check("green", []); });
  const a = fakeSpace();
  await createBot({ bg: slow.bg, slowAfterMs: 20 }).handle(a.space, text("is Lucky Fours legit"));
  assert.equal(a.sent.length, 2);
  assert.equal(a.sent[0], DOUBLE_CHECKING);
  assert.match(a.sent[1], /^LEGIT ✅/);

  const quick = stubBg(async () => check("green", []));
  const b = fakeSpace();
  await createBot({ bg: quick.bg, slowAfterMs: 20 }).handle(b.space, text("kith.com"));
  await wait(60);  // the timer was cleared: nothing arrives after the answer
  assert.equal(b.sent.length, 1);
  assert.match(b.sent[0], /^LEGIT ✅/);

  const failing = stubBg(async () => { await wait(80); throw new AskError("I couldn't find a website for \"zzz\"."); });
  const c = fakeSpace();
  await createBot({ bg: failing.bg, slowAfterMs: 20 }).handle(c.space, text("is zzz legit"));
  assert.deepEqual(c.sent, [DOUBLE_CHECKING, "I couldn't find a website for \"zzz\"."]);
});

test("the double-check note waits 15 seconds by default", async () => {
  const { bg } = stubBg(async () => { await wait(80); return check("green", []); });
  const { space, sent } = fakeSpace();
  await createBot({ bg }).handle(space, text("kith.com"));
  assert.equal(sent.length, 1);
});

test("terminal testing: a pasted file path is sent as that file; on iMessage it stays text", async () => {
  const { mkdtempSync, writeFileSync } = await import("node:fs");
  const { join } = await import("node:path");
  const { tmpdir } = await import("node:os");
  const dir = mkdtempSync(join(tmpdir(), "bg-"));
  const file = join(dir, "test-recording.mp4");
  writeFileSync(file, Buffer.from("video bytes"));

  const local = stubBg();
  await createBot({ bg: local.bg, localFiles: true }).handle(fakeSpace().space, text(`"${file}"`));
  assert.deepEqual(local.calls, ['ask "" video/mp4']);

  const cloud = stubBg();
  await createBot({ bg: cloud.bg }).handle(fakeSpace().space, text(`"${file}"`));
  assert.deepEqual(cloud.calls, [`ask ""${file}""`]);

  const missing = stubBg();
  await createBot({ bg: missing.bg, localFiles: true }).handle(fakeSpace().space, text(join(dir, "nope.mp4")));
  assert.deepEqual(missing.calls, [`ask "${join(dir, "nope.mp4")}"`]);  // no such file: left to the API to explain
});

test("a PDF (a receipt, an order confirmation) is sent to be checked", async () => {
  const { bg, calls } = stubBg();
  const att = { type: "attachment", id: "a1", name: "receipt.pdf", mimeType: "application/pdf", read: async () => Buffer.from("%PDF-") };
  await createBot({ bg }).handle(fakeSpace().space, { platform: "terminal", direction: "inbound", content: att } as unknown as Message);
  assert.deepEqual(calls, ['ask "" application/pdf']);
});

test("a link and its caption sent as two messages are one question; a second file is a second question", async () => {
  const { bg, calls } = stubBg();
  const { space, sent } = fakeSpace();
  const bot = createBot({ bg, settleMs: 30 });
  await Promise.all([bot.handle(space, text("how about this?")), bot.handle(space, text("https://www.instagram.com/p/Abc123/"))]);
  assert.deepEqual(calls, ['ask "how about this? https://www.instagram.com/p/Abc123/"']);
  assert.equal(sent.length, 1);
  await Promise.all([bot.handle(space, media()), bot.handle(space, text("is this real?")), bot.handle(space, media("", "application/pdf"))]);
  assert.deepEqual(calls.slice(1), ['ask "is this real?" image/heic', 'ask "" application/pdf']);
  await Promise.all([bot.handle(space, text("hi")), bot.handle(space, text("kith.com"))]);  // a greeting before a link doesn't swallow it
  assert.deepEqual(calls.slice(3), ['ask "hi kith.com"']);
});
