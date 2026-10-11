import assert from "node:assert/strict";
import { test } from "node:test";
import { crowdText, extrasText, reasons, replyText, verdictText } from "../src/brandguard.js";
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

test("Not legit answers end with what to do if you already paid; other verdicts never do", () => {
  const already_paid = { methods: ["zelle"], steps: ["Zelle: call your bank now and report it as fraud."],
                         report: ["Report it to the FTC: https://reportfraud.ftc.gov"] };
  const red = { ...check("red", [sig("bad", "The domain was registered 4 days ago.", 50)]), already_paid };
  const text = verdictText(red);
  assert.ok(text.includes("Don't pay or share your details.\n\nAlready paid?\n- Zelle: call your bank now and report it as fraud.\n- Report it to the FTC: https://reportfraud.ftc.gov"));
  assert.ok(!verdictText({ ...red, verdict: "green" }).includes("Already paid?"));
  assert.ok(!verdictText({ ...red, verdict: "yellow" }).includes("Already paid?"));
  assert.ok(!verdictText({ ...red, already_paid: {} }).includes("Already paid?"));
});

test("the crowd line shows once someone else has checked the site this week", () => {
  const red = check("red", [sig("bad", "The domain was registered 4 days ago.", 50)]);
  assert.equal(crowdText({ ...red, crowd: { checked: 1, flagged: 1, days: 7 } }), "");  // only this check: nothing to say
  assert.equal(crowdText({ ...red, crowd: {} }), "");
  assert.equal(crowdText({ ...red, crowd: { checked: 5, flagged: 3, days: 7 } }), "👥 Checked 5 times this week; 3 came out Not legit.");
  assert.equal(crowdText({ ...red, crowd: { checked: 2, flagged: 0, days: 7 } }), "👥 Checked 2 times this week; never flagged.");
  const text = verdictText({ ...red, crowd: { checked: 5, flagged: 3, days: 7 } });
  assert.ok(text.includes("- The domain was registered 4 days ago.\n👥 Checked 5 times this week; 3 came out Not legit.\n\nDon't pay"));
});

test("a post the platform hid asks for a screenshot instead of the usual advice, and nothing else", () => {
  const hidden = { ...check("yellow", [sig("warn", "Instagram didn't show us this post without a login.", 12)]),
                   platform: "Instagram", send_screenshot: true, alternatives_note: "Send me the product page." };
  const text = verdictText(hidden);
  assert.ok(text.includes("Instagram didn't let me see this post. Send me a screenshot of it, with the account name and caption showing, and I'll check that."));
  assert.ok(!text.includes("buyer protection") && !text.includes("Send me the product page."));
  assert.ok(verdictText({ ...hidden, send_screenshot: false }).includes("buyer protection"));
});

test("an answer found in a video says so", () => {
  assert.match(verdictText(check("red", [], { how: "video", what: "luxe-kicks.store" })), /^NOT LEGIT ❌ {2}luxe-kicks\.store \(from your video\)/);
});

test("an answer found in a PDF says so", () => {
  assert.match(verdictText(check("red", [], { how: "pdf", what: "mega-sneaker-drop.shop" })), /^NOT LEGIT ❌ {2}mega-sneaker-drop\.shop \(from your PDF\)/);
});

test("a photo with no seller asks for the link instead of the usual advice", () => {
  const photo = { ...check("yellow", [sig("bad", "$39.99 here, about $120.00 at trusted stores. Prices this low are a classic fake-shop sign.")]),
                  ask_for: "I couldn't tell who's selling it from that. Send me the link, or a screenshot that shows the shop's name or address." };
  const text = verdictText(photo);
  assert.ok(text.includes("- $39.99 here, about $120.00 at trusted stores."));
  assert.ok(text.includes("I couldn't tell who's selling it from that.") && !text.includes("buyer protection"));
});
