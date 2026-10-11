// Client for the Brand Guard HTTP API (the FastAPI app in ../app), plus the text the agent sends back.

export type Signal = { label: string; detail: string; tone: "good" | "bad" | "warn" | "neutral"; impact: number };

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
};

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

/** The reasons worth texting: warnings for Not legit or Couldn't check, good signs for Legit. */
export function reasons(c: Check, top = 3): string[] {
  const wanted = c.verdict === "green" ? ["good"] : ["bad", "warn"];
  return c.signals.filter(s => wanted.includes(s.tone)).slice(0, top).map(s => firstSentence(s.detail));
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
  return [`${HEADLINE[c.verdict]}  ${what}`, "", ...reasons(c).map(r => `- ${r}`), "", ADVICE[c.verdict],
    ...(extras.length ? ["", ...extras] : [])].join("\n");
}
