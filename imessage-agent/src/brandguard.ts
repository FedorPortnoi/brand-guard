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
};

export type Done = {
  scanned: number;
  scan_id: string;
  verified: number;
  thumbnail_only: number;
  unverified: number;
  semantic: number;
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
    return fd;
  }

  /** Run a full scan. Takes as long as the scan (often 2-3 minutes); onStatus gets the scan's status lines. */
  async scan(req: ScanRequest, onStatus?: (text: string) => void): Promise<ScanResult> {
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
        if (event === "status" && d.scan_id) out.scanId = d.scan_id;
        if (event === "status" && typeof d.message === "string") onStatus?.(d.message);
        else if (event === "match") out.matches.push(d as Match);
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
               `${m.forensics!.label}${m.verification === "verified" ? "" : " (low-res check)"}`, where);
  });
  if (found.length > top) lines.push("", `+${found.length - top} more.`);
  const own = ownSiteMatches(r);
  if (own) lines.push("", `${own} more on your own sites or resellers (not counted).`);
  if (d?.unverified) lines.push("", `${d.unverified} image${d.unverified === 1 ? "" : "s"} could not be downloaded, so not counted.`);
  if (appUrl && found.length) lines.push("", `Full evidence and takedown drafts: ${appUrl}`);
  return lines.join("\n");
}
