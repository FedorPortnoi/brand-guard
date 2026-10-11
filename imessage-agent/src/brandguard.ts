// Client for the Brand Guard HTTP API (the FastAPI app in ../app), plus the text the agent sends back.

export type Signal = { label: string; detail: string; tone: "good" | "bad" | "warn" | "neutral"; impact: number };

/** The link check's answer (POST /api/listing/ask), plus how the address was found. */
export type Check = {
  verdict: "green" | "yellow" | "red";
  score: number;
  signals: Signal[];
  url: string;
  domain: string;
  found: { how: "link" | "screenshot" | "account" | "name"; what: string; read?: string[] };
};

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

const HEADLINE = { red: "NOT LEGIT ❌", yellow: "NOT SURE ⚠️", green: "LOOKS LEGIT ✅" };
const ADVICE = {
  red: "Don't pay or share your details.",
  yellow: "I couldn't confirm it's safe. Only pay with buyer protection (a card or PayPal Goods & Services).",
  green: "No warning signs found. Not a guarantee.",
};

const firstSentence = (s: string) => (s.match(/^.*?[.!?](?=\s|$)/)?.[0] ?? s).trim();

/** The reasons worth texting: warnings for a red or yellow verdict, good signs for a green one. */
export function reasons(c: Check, top = 3): string[] {
  const wanted = c.verdict === "green" ? ["good"] : ["bad", "warn"];
  return c.signals.filter(s => wanted.includes(s.tone)).slice(0, top).map(s => firstSentence(s.detail));
}

/** The reply: verdict, what was checked, why, and what to do. */
export function verdictText(c: Check): string {
  const what = c.found.how === "screenshot" ? `${c.found.what} (from your screenshot)` : c.found.what || c.domain;
  return [`${HEADLINE[c.verdict]}  ${what}`, "", ...reasons(c).map(r => `- ${r}`), "", ADVICE[c.verdict]].join("\n");
}
