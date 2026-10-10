// Client for the Brand Guard HTTP API (the FastAPI app in ../app), plus the text the agent sends back.

export type Match = {
  idx: number;
  title?: string;
  page_url?: string | null;
  image_url: string;
  verification: string;
  forensics: { kind: string; label: string } | null;
  risk: { score: number; category: string; host: string; reasons: string[] };
  thumb?: string | null;
  server?: { shared?: string | null; seen_before?: Seen[] };
};

/** An earlier listing in Brand Guard's investigation log that shares this listing's site, URL or server IP. */
export type Seen = { host: string; brand: string; started_at: string; same_site: boolean; same_url: boolean;
                     same_ips: string[]; this_investigation: boolean };

export type Done = {
  scanned: number;
  scan_id: string;
  verified: number;
  thumbnail_only: number;
  unverified: number;
  semantic: number;
  own_site?: number;  // candidates on the brand's own sites and resellers, skipped before download
  timings?: Record<string, number>;
};

export type ScanResult = { scanId: string; matches: Match[]; done: Done | null; error?: string };

export type Alert = { id: string; watch_id: string; brand: string; type: string; message: string; created_at: string };

export type ScanRequest = { image: Buffer; filename: string; mimeType: string; brand: string; officialDomains?: string };

/** Split a server-sent-events body into (event, data) pairs. Keeps any trailing partial block in `rest`. */
export function parseSse(buffer: string): { events: [string, unknown][]; rest: string } {
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: [string, unknown][] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) continue;
    try {
      events.push([event, JSON.parse(data.join("\n"))]);
    } catch {
      events.push([event, data.join("\n")]);
    }
  }
  return { events, rest };
}

export class BrandGuard {
  constructor(readonly baseUrl: string) {}

  private form(req: ScanRequest): FormData {
    const fd = new FormData();
    fd.append("image", new Blob([new Uint8Array(req.image)], { type: req.mimeType }), req.filename);
    fd.append("brand", req.brand);
    fd.append("official_domains", req.officialDomains ?? "");
    fd.append("origin", "imessage");  // shown in the web app's investigation log
    return fd;
  }

  /** Run a full scan. Takes as long as the scan (often 2-3 minutes); onEvent sees each event as it arrives. */
  async scan(req: ScanRequest, onEvent?: (event: string, data: Record<string, any>) => void): Promise<ScanResult> {
    const res = await fetch(`${this.baseUrl}/api/scan`, { method: "POST", body: this.form(req) });
    if (!res.ok || !res.body) throw new Error(`Brand Guard returned HTTP ${res.status}`);
    const out: ScanResult = { scanId: "", matches: [], done: null };
    const decoder = new TextDecoder();
    let buf = "";
    for await (const chunk of res.body as unknown as AsyncIterable<Uint8Array>) {
      buf += decoder.decode(chunk, { stream: true });
      const { events, rest } = parseSse(buf);
      buf = rest;
      for (const [event, data] of events) {
        const d = data as Record<string, any>;
        onEvent?.(event, d);
        if (event === "status" && d.scan_id) out.scanId = d.scan_id;
        if (event === "match") out.matches.push(d as Match);
        else if (event === "done") { out.done = d as Done; out.scanId = d.scan_id || out.scanId; }
        else if (event === "error") out.error = String(d.message ?? "Scan failed");
      }
    }
    return out;
  }

  /** The PDF evidence report Brand Guard builds for one match. */
  async report(scanId: string, idx: number): Promise<Buffer | null> {
    const res = await fetch(`${this.baseUrl}/api/report/${scanId}/${idx}.pdf`);
    return res.ok ? Buffer.from(await res.arrayBuffer()) : null;
  }

  async createWatch(req: ScanRequest, intervalHours = 24): Promise<{ id: string; next_run_at?: string }> {
    const fd = this.form(req);
    fd.append("interval_hours", String(intervalHours));
    const res = await fetch(`${this.baseUrl}/api/watches`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(`Could not create the watch (HTTP ${res.status})`);
    const body = (await res.json()) as Record<string, any>;
    return (body.watch ?? body) as { id: string; next_run_at?: string };
  }

  async deleteWatch(id: string): Promise<void> {
    await fetch(`${this.baseUrl}/api/watches/${id}`, { method: "DELETE" });
  }

  async alerts(): Promise<Alert[]> {
    const res = await fetch(`${this.baseUrl}/api/alerts`);
    return res.ok ? ((await res.json()) as Alert[]) : [];
  }
}

export const riskWord = (m: Match) =>
  m.verification === "unverified" ? "unverified" : m.risk.score >= 65 ? "HIGH RISK" : m.risk.score >= 40 ? "review" : "low risk";

const OWN = new Set(["official", "authorised_reseller"]);

/** Matches with real visual evidence on someone else's site, worst first. Unverified rows (image could not be
 *  downloaded) are left out, as the web app hides them by default, and so are the brand's own sites and resellers. */
export function evidenceMatches(r: ScanResult): Match[] {
  return r.matches.filter(m => m.forensics && !OWN.has(m.risk.category)).sort((a, b) => b.risk.score - a.risk.score);
}

/** Copies found on the brand's own sites or authorised resellers: expected, so only counted. */
export const ownSiteMatches = (r: ScanResult) => r.matches.filter(m => m.forensics && OWN.has(m.risk.category)).length;

/** Why a match came up before, in a few words: the same site or listing in an earlier investigation, or another site
 *  on the same server (not on shared platforms such as Shopify, where unrelated shops share IPs). */
export function seenBefore(m: Match): string {
  const seen = m.server?.seen_before ?? [];
  if (seen.some(s => (s.same_site || s.same_url) && !s.this_investigation)) return "Found in an earlier investigation.";
  const other = seen.find(s => s.same_ips.length && !s.same_site && !s.same_url);
  return other && !m.server?.shared ? `Same server as ${other.host}, found before.` : "";
}

/** The one message sent while a scan runs, once half the candidates are checked: 2-3 silent minutes feel broken. */
export function halfwayNote(done: number, total: number, sofar: Match[]): string {
  const n = evidenceMatches({ scanId: "", matches: sofar, done: null }).length;
  return `Halfway: checked ${done} of ${total} images, ${n ? `${n} possible cop${n === 1 ? "y" : "ies"} so far` : "no copies yet"}. About a minute to go.`;
}

/** The reply to a finished scan: counts, then the worst few matches with their links. */
export function summarize(r: ScanResult, brand: string, appUrl = "", top = 3): string {
  if (r.error) return `The scan failed: ${r.error}`;
  const d = r.done;
  const found = evidenceMatches(r);
  const high = found.filter(m => m.risk.score >= 65).length;
  const secs = d?.timings?.total ? ` in ${Math.round(d.timings.total)}s` : "";
  const lines = [
    `Brand Guard checked ${d?.scanned ?? 0} images for ${brand}${secs}.`,
    found.length
      ? `${found.length} visual match${found.length === 1 ? "" : "es"}, ${high} high risk.`
      : "No copies of your image found.",
  ];
  found.slice(0, top).forEach((m, i) => {
    const where = m.page_url || m.image_url;
    lines.push("", `${i + 1}. ${riskWord(m)} (${m.risk.score}/100) on ${m.risk.host || "unknown site"}`,
               `${m.forensics!.label}${m.verification === "verified" ? "" : " (low-res check)"}`,
               ...(seenBefore(m) ? [seenBefore(m)] : []), where);
  });
  if (found.length > top) lines.push("", `+${found.length - top} more.`);
  const own = ownSiteMatches(r) + (d?.own_site ?? 0);
  if (own) lines.push("", `${own} more on your own sites or resellers (not counted).`);
  if (d?.unverified) lines.push("", `${d.unverified} image${d.unverified === 1 ? "" : "s"} could not be downloaded, so not counted.`);
  if (appUrl && found.length) lines.push("", `Full evidence and takedown drafts: ${appUrl}`);
  return lines.join("\n");
}
