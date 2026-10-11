import assert from "node:assert/strict";
import { test } from "node:test";
import { extrasText, reasons, replyText, verdictText } from "../src/brandguard.js";
import { check, sig } from "./fixtures.js";

test("a red verdict says NOT LEGIT with the warnings, first sentence only, and what to do", () => {
  const c = check("red", [
    sig("bad", "The domain was registered 9 days ago.", 34),
    sig("good", "The page uses HTTPS.", -2),
    sig("bad", "Asks buyers to pay or talk elsewhere: zelle, dm to order. That removes buyer protection.", 20),
    sig("warn", "The .store ending needs added scrutiny.", 12),
    sig("warn", "This site or listing appeared in an earlier BrandGuard investigation.", 6),
  ]);
  assert.equal(verdictText(c), [
    "NOT LEGIT ❌  luxe-kicks.store",
    "",
    "- The domain was registered 9 days ago.",
    "- Asks buyers to pay or talk elsewhere: zelle, dm to order.",
    "- The .store ending needs added scrutiny.",
    "",
    "Don't pay or share your details.",
  ].join("\n"));
});

test("three verdicts: Legit gives the good signs, Couldn't check says why; a screenshot and a name say what was checked", () => {
  const green = check("green", [sig("warn", "Shared hosting."), sig("good", "The domain has existed for about 3 years.", -4)],
                      { how: "name", what: "Lucky Fours: luckyfours.com" });
  assert.deepEqual(reasons(green), ["The domain has existed for about 3 years."]);
  assert.match(verdictText(green), /^LEGIT ✅ {2}Lucky Fours: luckyfours\.com\n/);
  assert.match(verdictText(green), /Not a guarantee\.$/);
  const shot = check("yellow", [], { how: "screenshot" });
  assert.match(verdictText(shot), /^COULDN'T CHECK ⚠️ {2}luxe-kicks\.store \(from your screenshot\)/);
  assert.match(verdictText(shot), /couldn't read the site or find anything about it elsewhere/);
  assert.match(verdictText(shot), /buyer protection/);
});

test("every answer ends with the real brand and other places to buy; the brand's own site is not repeated", () => {
  const fake = {
    ...check("red", [sig("bad", "The domain was registered 9 days ago.", 34)]),
    real_brand: { name: "Adidas", url: "https://www.adidas.com/us", this_site: false },
    alternatives: [
      { url: "https://www.footlocker.com/p/1", host: "footlocker.com", title: "Samba OG", price: 100, currency: "USD", verdict: "green" as const },
      { url: "https://www.zalando.de/p/2", host: "zalando.de", title: "Samba OG", price: 110, currency: "EUR", verdict: "yellow" as const },
      { url: "https://www.zappos.com/p/3", host: "zappos.com", title: "Samba OG", price: null, currency: null, verdict: "green" as const },
      { url: "https://x.example/p/4", host: "x.example", title: "Samba OG", price: 1, currency: "USD", verdict: "green" as const },
    ],
  };
  assert.ok(verdictText(fake).endsWith([
    "Don't pay or share your details.",
    "",
    "The real Adidas: https://www.adidas.com/us",
    "Other places to buy it:",
    "- footlocker.com, $100.00: https://www.footlocker.com/p/1",
    "- zalando.de, 110.00 EUR: https://www.zalando.de/p/2",
    "- zappos.com: https://www.zappos.com/p/3",
  ].join("\n")));

  const own = { ...check("green", []), real_brand: { name: "adidas", url: "https://adidas.com", this_site: true }, alternatives: [] };
  assert.deepEqual(extrasText(own), []);
  const unknown = { ...check("yellow", []), alternatives: [], alternatives_note: "I couldn't tell which product this is." };
  assert.deepEqual(extrasText(unknown), ["I couldn't tell which product this is."]);
});

test("a store known only by name gets trusted stores for what it sells", () => {
  const c = {
    ...check("yellow", []),
    alternatives: [{ url: "https://watchwarehouse.com/men/", host: "watchwarehouse.com", title: "Watches", price: null, currency: null, verdict: "green" as const }],
    alternatives_for: { what: "Affordable Luxury Watches", kind: "category" as const },
  };
  assert.deepEqual(extrasText(c), ["Trusted stores for affordable luxury watches:", "- watchwarehouse.com: https://watchwarehouse.com/men/"]);
});

test("one reply answers every thing the message named, and says why one couldn't be checked", () => {
  const c = {
    ...check("green", [sig("good", "The domain has existed for about 3 years.", -4)], { how: "name", what: "Lucky Fours: luckyfours.com" }),
    others: [
      check("green", [sig("good", "The domain has existed for about 26 years.", -4)], { what: "kith.com" }),
      { error: "I couldn't find a website for \"Zzqxv\". Send me the link instead.", found: { how: "name" as const, what: "Zzqxv" } },
    ],
  };
  const parts = replyText(c).split("\n\n———\n\n");
  assert.equal(parts.length, 3);
  assert.match(parts[0], /^LEGIT ✅ {2}Lucky Fours: luckyfours\.com/);
  assert.match(parts[1], /^LEGIT ✅ {2}kith\.com/);
  assert.equal(parts[2], "Zzqxv: I couldn't find a website for \"Zzqxv\". Send me the link instead.");
  assert.equal(replyText(check("red", [])), verdictText(check("red", [])));
});


test("each reason shows its proof on the line below; same photo elsewhere is shown unless Legit", async () => {
  const proofed = {
    ...check("red", [
      { label: "Very new domain", detail: "The domain was registered 4 days ago.", tone: "bad", impact: 50,
        proof: { text: "Registered 2026-10-06", url: "https://lookup.icann.org/en/lookup?name=luxe-kicks.store" } },
      { label: "Payment off the platform", detail: "Asks buyers to pay or talk elsewhere: zelle. That removes buyer protection.",
        tone: "bad", impact: 20, proof: { text: "\"DM to order, Zelle only\" (from your screenshot)", url: null } },
    ]),
    same_photo: [{ url: "https://www.adidas.com/us/samba", host: "www.adidas.com", title: "Samba OG" }],
  };
  const text = verdictText(proofed);
  assert.ok(text.includes("- The domain was registered 4 days ago.\n  ↳ Registered 2026-10-06: https://lookup.icann.org/en/lookup?name=luxe-kicks.store\n"));
  assert.ok(text.includes("- Asks buyers to pay or talk elsewhere: zelle.\n  ↳ \"DM to order, Zelle only\" (from your screenshot)\n"));
  assert.ok(text.includes("Same photo also on:\n- www.adidas.com: https://www.adidas.com/us/samba"));
  assert.ok(!verdictText({ ...proofed, verdict: "green" }).includes("Same photo"));
});
