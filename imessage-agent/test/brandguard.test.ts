import assert from "node:assert/strict";
import { test } from "node:test";
import { evidenceMatches, parseSse, summarize, type Match, type ScanResult } from "../src/brandguard.js";

test("parseSse splits events, parses JSON and keeps a partial block for the next chunk", () => {
  const { events, rest } = parseSse('event: status\ndata: {"message":"Searching"}\n\nevent: match\r\ndata: {"idx":0}\r\n\nevent: do');
  assert.deepEqual(events, [["status", { message: "Searching" }], ["match", { idx: 0 }]]);
  assert.equal(rest, "event: do");
});

const m = (idx: number, score: number, extra: Partial<Match> = {}): Match => ({
  idx, image_url: `https://img.example/${idx}.jpg`, page_url: `https://shop${idx}.example/p`, verification: "verified",
  forensics: { kind: "exact", label: "Exact / re-compressed copy" }, risk: { score, category: "unknown", host: `shop${idx}.example`, reasons: [] },
  ...extra,
});

const result = (matches: Match[]): ScanResult => ({
  scanId: "abc", matches,
  done: { scanned: 312, scan_id: "abc", verified: 2, thumbnail_only: 0, unverified: 4, semantic: 0, timings: { total: 151.4 } },
});

test("summary lists the worst evidence matches first and leaves out unverified rows", () => {
  const r = result([m(0, 30), m(1, 80), m(2, 55, { verification: "unverified", forensics: null })]);
  assert.deepEqual(evidenceMatches(r).map(x => x.idx), [1, 0]);
  const text = summarize(r, "Acme", "https://bg.example");
  assert.match(text, /checked 312 images for Acme in 151s/);
  assert.match(text, /2 visual matches, 1 high risk/);
  assert.ok(text.indexOf("shop1.example") < text.indexOf("shop0.example"));
  assert.ok(!text.includes("shop2.example"));
  assert.match(text, /4 images could not be downloaded/);
  assert.match(text, /https:\/\/bg\.example/);
});

test("the brand's own sites and resellers are counted but never listed as matches", () => {
  const own = (idx: number, category: string) => m(idx, 0, { risk: { score: 0, category, host: "acme.com", reasons: [] } });
  const r = result([own(0, "official"), own(1, "authorised_reseller"), m(2, 60)]);
  assert.deepEqual(evidenceMatches(r).map(x => x.idx), [2]);
  const text = summarize(r, "Acme");
  assert.match(text, /1 visual match, 0 high risk/);
  assert.ok(!text.includes("acme.com"));
  assert.match(text, /2 more on your own sites or resellers \(not counted\)/);
});

test("summary with no matches and with an error", () => {
  assert.match(summarize(result([]), "Acme"), /No copies of your image found/);
  assert.equal(summarize({ scanId: "", matches: [], done: null, error: "Could not read that image." }, "Acme"),
               "The scan failed: Could not read that image.");
});
