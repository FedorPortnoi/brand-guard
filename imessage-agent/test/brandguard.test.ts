import assert from "node:assert/strict";
import { test } from "node:test";
import { reasons, verdictText } from "../src/brandguard.js";
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

test("a green verdict gives the good signs; a screenshot and a name say what was checked", () => {
  const green = check("green", [sig("warn", "Shared hosting."), sig("good", "The domain has existed for about 3 years.", -4)],
                      { how: "name", what: "Lucky Fours: luckyfours.com" });
  assert.deepEqual(reasons(green), ["The domain has existed for about 3 years."]);
  assert.match(verdictText(green), /^LOOKS LEGIT ✅ {2}Lucky Fours: luckyfours\.com\n/);
  assert.match(verdictText(green), /Not a guarantee\.$/);
  const shot = check("yellow", [], { how: "screenshot" });
  assert.match(verdictText(shot), /^NOT SURE ⚠️ {2}luxe-kicks\.store \(from your screenshot\)/);
  assert.match(verdictText(shot), /buyer protection/);
});
