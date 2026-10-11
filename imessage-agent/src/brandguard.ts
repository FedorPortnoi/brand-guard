// Client for the Brand Guard HTTP API (the FastAPI app in ../app), plus the text the agent sends back.

/** What backs a reason: a date, a link anyone can open, or a quote. */
export type Proof = { text: string; url: string | null };
export type Signal = { label: string; detail: string; tone: "good" | "bad" | "warn" | "neutral"; impact: number; proof?: Proof };

/** The link check's answer (POST /api/listing/ask), plus how the address was found. */
export type Check = {
  /** green = Legit, red = Not legit, yellow = Couldn't check (nothing to judge by). */
  verdict: "green" | "yellow" | "red";
  score: number;
  signals: Signal[];
  url: string;
  domain: string;
  found: { how: "link" | "screenshot" | "account" | "name"; what: string; read?: string[] };
  /** The brand the page claims to sell and its own site; this_site: the checked site is that brand's own. */
  real_brand?: { name: string; url: string; this_site: boolean } | null;
  /** Other places to buy the same item, trusted first (only ones rated Legit). */
  alternatives?: Alternative[];
  /** Why there are no alternatives, when there are none. */
  alternatives_note?: string;
  /** The other things named in the same message ("is Lucky Fours legit? and kith.com"), each checked or why not. */
  others?: (Check | { error: string; found: Check["found"] })[];
  /** What the alternatives are for: the same product, or (when only the store is known) what the store sells. */
  alternatives_for?: { what: string; kind: "product" | "category" } | Record<string, never>;
  /** Other sites showing the same product photo (a sign the photo was taken from somewhere else). */
  same_photo?: { url: string; host: string; title: string }[];
  /** Not legit only: what to do if the shopper already paid, by the payment methods seen, and where to report it. */
  already_paid?: { methods: string[]; steps: string[]; report: string[] } | Record<string, never>;
  /** Link checks of this site (or listing) in the last `days` days, this one included, and how many were Not legit. */
  crowd?: { checked: number; flagged: number; days: number } | Record<string, never>;
};

/** "Checked 5 times in the last 7 days; 3 came out Not legit." Only once someone else has checked it too. */
export function crowdText(c: Check): string {
  const cr = c.crowd;
  if (!cr || !("checked" in cr) || cr.checked < 2) return "";
  const when = cr.days === 7 ? "this week" : `in the last ${cr.days} days`;
  return `👥 Checked ${cr.checked} times ${when}; ` +
    (cr.flagged ? `${cr.flagged} came out Not legit.` : "never flagged.");
}

export type Alternative = { url: string; host: string; title: string; price: number | null; currency: string | null; verdict: Check["verdict"] };

export type AskRequest = { text?: string; image?: { data: Buffer; filename: string; mimeType: string } };

/** Something the shopper should hear as is ("I couldn't find a web address in that screenshot..."). */
export class AskError extends Error {}

export class BrandGuard {
  constructor(readonly baseUrl: string) {}

  /** Is this legit? From a link, a screenshot or a store's name. */
  async ask(req: AskRequest): Promise<Check> {
    const fd = new FormData();
    fd.append("text", req.text ?? "");
    if (req.image) fd.append("image", new Blob([new Uint8Array(req.image.data)], { type: req.image.mimeType }), req.image.filename);
    const res = await fetch(`${this.baseUrl}/api/listing/ask`, { method: "POST", body: fd });
    if (res.status === 422) throw new AskError(String(((await res.json()) as { detail?: unknown }).detail ?? "I couldn't check that."));
    if (!res.ok) throw new Error(`Brand Guard returned HTTP ${res.status}`);
    return (await res.json()) as Check;
  }
}

const HEADLINE = { red: "NOT LEGIT ❌", yellow: "COULDN'T CHECK ⚠️", green: "LEGIT ✅" };
const ADVICE = {
  red: "Don't pay or share your details.",
  yellow: "I couldn't read the site or find anything about it elsewhere. If you buy, pay only with buyer protection (a card or PayPal Goods & Services).",
  green: "Nothing points to a scam. Not a guarantee.",
};

const firstSentence = (s: string) => (s.match(/^.*?[.!?](?=\s|$)/)?.[0] ?? s).trim();

/** The signals worth texting: warnings for Not legit or Couldn't check, good signs for Legit. */
function picked(c: Check, top = 3): Signal[] {
  const wanted = c.verdict === "green" ? ["good"] : ["bad", "warn"];
  return c.signals.filter(s => wanted.includes(s.tone)).slice(0, top);
}

/** The reasons worth texting, one sentence each. */
export function reasons(c: Check, top = 3): string[] {
  return picked(c, top).map(s => firstSentence(s.detail));
}

/** A reason with its proof on the line below: "- reason" / "  ↳ Registered 2026-10-01: https://…". */
export function reasonLines(c: Check, top = 3): string[] {
  return picked(c, top).map(s => `- ${firstSentence(s.detail)}` +
    (s.proof ? `
  ↳ ${s.proof.text}${s.proof.url ? `: ${s.proof.url}` : ""}` : ""));
}

const price = (a: Alternative) =>
  a.price == null ? "" : !a.currency || a.currency === "USD" ? `, $${a.price.toFixed(2)}` : `, ${a.price.toFixed(2)} ${a.currency}`;

/** The real brand (when this isn't its site) and other places to buy, for every verdict. */
export function extrasText(c: Check): string[] {
  const lines: string[] = [];
  if (c.real_brand && !c.real_brand.this_site) lines.push(`The real ${c.real_brand.name}: ${c.real_brand.url}`);
  const alts = (c.alternatives ?? []).slice(0, 3);
  const forWhat = c.alternatives_for && "kind" in c.alternatives_for && c.alternatives_for.kind === "category"
    ? `Trusted stores for ${c.alternatives_for.what.toLowerCase()}:` : "Other places to buy it:";
  if (alts.length) lines.push(forWhat, ...alts.map(a => `- ${a.host}${price(a)}: ${a.url}`));
  else if (c.alternatives_note) lines.push(c.alternatives_note);
  const photos = c.verdict === "green" ? [] : (c.same_photo ?? []).slice(0, 2);
  if (photos.length) lines.push("Same photo also on:", ...photos.map(p => `- ${p.host}: ${p.url}`));
  return lines;
}

/** The whole reply: one answer per thing the message named, separated by a line. */
export function replyText(c: Check): string {
  const others = (c.others ?? []).map(o => "error" in o ? `${o.found.what}: ${o.error}` : verdictText(o));
  return [verdictText(c), ...others].join("\n\n———\n\n");
}

/** The reply: verdict, what was checked, why, what to do, then the real brand and other places to buy. */
export function verdictText(c: Check): string {
  const what = c.found.how === "screenshot" ? `${c.found.what} (from your screenshot)` : c.found.what || c.domain;
  const extras = extrasText(c);
  const ap = c.already_paid;
  const paid = c.verdict === "red" && ap && "steps" in ap && ap.steps.length
    ? ["", "Already paid?", ...ap.steps.map(s => `- ${s}`), ...ap.report.map(r => `- ${r}`)] : [];
  const crowd = crowdText(c);
  return [`${HEADLINE[c.verdict]}  ${what}`, "", ...reasonLines(c), ...(crowd ? [crowd] : []), "", ADVICE[c.verdict], ...paid,
    ...(extras.length ? ["", ...extras] : [])].join("\n");
}
