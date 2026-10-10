import assert from "node:assert/strict";
import { test } from "node:test";
import { evidenceMatches, halfwayNote, parseSse, seenBefore, summarize, type Match, type ScanResult } from "../src/brandguard.js";

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

test("the halfway note counts only other people's copies", () => {
  const own = m(0, 0, { risk: { score: 0, category: "official", host: "acme.com", reasons: [] } });
  assert.equal(halfwayNote(140, 280, [own]), "Halfway: checked 140 of 280 images, no copies yet. About a minute to go.");
  assert.equal(halfwayNote(140, 280, [own, m(1, 60), m(2, 70)]), "Halfway: checked 140 of 280 images, 2 possible copies so far. About a minute to go.");
});

test("own-site candidates skipped by the scan are counted with the own-site rows", () => {
  const r = result([m(0, 60)]);
  r.done!.own_site = 7;
  assert.ok(summarize(r, "Acme").includes("7 more on your own sites or resellers (not counted)."));
});

test("a match seen in an earlier investigation says so; a shared Shopify IP does not count", () => {
  const seen = (x: Partial<import("../src/brandguard.js").Seen>) => ({ host: "shop-a.example", brand: "Acme", started_at: "2026-10-09",
    same_site: false, same_url: false, same_ips: [], this_investigation: false, ...x });
  const onServer = m(1, 70, { server: { shared: null, seen_before: [seen({ same_ips: ["203.0.113.7"] })] } });
  const onShopify = m(2, 60, { server: { shared: "Shopify", seen_before: [seen({ same_ips: ["23.227.38.65"] })] } });
  const again = m(3, 50, { server: { seen_before: [seen({ same_site: true })] } });
  assert.equal(seenBefore(onServer), "Same server as shop-a.example, found before.");
  assert.equal(seenBefore(onShopify), "");
  assert.equal(seenBefore(again), "Found in an earlier investigation.");
  assert.ok(summarize(result([onServer]), "Acme").includes("Same server as shop-a.example, found before."));
});
