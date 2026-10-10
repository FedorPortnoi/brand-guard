import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import type { Message, Space } from "spectrum-ts";
import { type BrandGuard, type ScanResult } from "../src/brandguard.js";
import { createBot, HELP } from "../src/bot.js";

/** A chat that records what the bot sends: strings as text, attachments as "[file name]". */
export function fakeSpace(id = "chat-1") {
  const sent: string[] = [];
  const space = {
    id,
    send: async (...items: any[]) => {
      for (const c of items) sent.push(typeof c === "string" ? c : `[file ${(await c.build()).name}]`);
      return undefined;
    },
    responding: async (fn: () => unknown) => fn(),
  } as unknown as Space;
  return { space, sent };
}

export const text = (t: string) => ({ platform: "terminal", direction: "inbound", content: { type: "text", text: t } }) as unknown as Message;
export const photo = (caption = "", bytes = Buffer.from("jpeg")) => {
  const att = { type: "attachment", id: "a1", name: "IMG_0001.HEIC", mimeType: "image/heic", read: async () => bytes };
  const content = caption
    ? { type: "group", items: [{ content: att }, { content: { type: "text", text: caption } }] }
    : att;
  return { platform: "terminal", direction: "inbound", content } as unknown as Message;
};

const scanResult: ScanResult = {
  scanId: "s1",
  matches: [{ idx: 3, image_url: "https://x.example/a.jpg", page_url: "https://fake.example/p", verification: "verified",
              forensics: { kind: "exact", label: "Exact / re-compressed copy" },
              risk: { score: 80, category: "likely_counterfeit", host: "fake.example", reasons: [] },
              thumb: "data:image/jpeg;base64," + Buffer.from("thumb").toString("base64") }],
  done: { scanned: 10, scan_id: "s1", verified: 1, thumbnail_only: 0, unverified: 0, semantic: 0 },
};

function stubBg() {
  const calls: string[] = [];
  const bg = {
    scan: async (req: { brand: string; officialDomains?: string; mimeType: string }) => {
      calls.push(`scan ${req.brand} ${req.officialDomains ?? ""} ${req.mimeType}`); return scanResult;
    },
    report: async (scanId: string, idx: number) => { calls.push(`report ${scanId} ${idx}`); return Buffer.from("%PDF-"); },
    createWatch: async () => { calls.push("watch"); return { id: "w1" }; },
    deleteWatch: async (id: string) => { calls.push(`delete ${id}`); },
    alerts: async () => [{ id: "al1", watch_id: "w1", brand: "Acme", type: "new", message: "2 new copies", created_at: "" }],
  };
  return { bg: bg as unknown as BrandGuard, calls };
}

const statePath = () => join(mkdtempSync(join(tmpdir(), "bg-agent-")), "state.json");

test("photo first, brand second, then report, watch, alerts and stop", async () => {
  const { bg, calls } = stubBg();
  const { space, sent } = fakeSpace();
  const bot = createBot({ bg, statePath: statePath(), lookupSpace: async () => space, appUrl: "https://bg.example" });

  await bot.handle(space, text("hi"));
  assert.equal(sent.pop(), HELP);

  await bot.handle(space, photo());
  assert.equal(sent.pop(), "Got the photo. What's the brand name?");

  await bot.handle(space, text("domains acme.com"));
  await bot.handle(space, text("Acme"));
  assert.deepEqual(calls, ["scan Acme acme.com image/heic"]);
  assert.match(sent.join("\n"), /checked 10 images for Acme/);
  assert.match(sent.join("\n"), /HIGH RISK \(80\/100\) on fake\.example/);
  assert.ok(sent.includes("[file top-match.jpg]"));

  await bot.handle(space, text("report"));
  assert.equal(calls.at(-1), "report s1 3");
  assert.equal(sent.at(-1), "[file brand-guard-evidence.pdf]");

  await bot.handle(space, text("watch"));
  await bot.forwardAlerts();
  await bot.forwardAlerts();  // an alert is forwarded once
  assert.equal(sent.filter(s => s.startsWith("Brand Guard alert for Acme: 2 new copies")).length, 1);

  await bot.handle(space, text("stop"));
  assert.equal(calls.at(-1), "delete w1");
  assert.equal(sent.at(-1), "Stopped watching.");
});

test("a captioned photo names the brand, and unknown text gets a short hint after the first help", async () => {
  const { bg, calls } = stubBg();
  const { space, sent } = fakeSpace();
  const bot = createBot({ bg, statePath: statePath(), lookupSpace: async () => space });
  await bot.handle(space, text("what is this"));
  assert.equal(sent.pop(), HELP);
  await bot.handle(space, text("what is this"));
  assert.equal(sent.pop(), 'Send me a product photo to check, or "help" for the commands.');
  await bot.handle(space, photo("Acme"));
  await bot.handle(space, photo("Globex"));
  assert.deepEqual(calls, ["scan Acme  image/heic", "scan Globex  image/heic"]);
});
