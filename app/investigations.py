"""The investigation log: every scan that ran and every listing it found, with the server IPs behind each listing.

Kept in SQLite (data/investigations.db) across restarts, unlike the in-memory sessions and the 20-scan replay history,
so a later investigation can see that a listing's server, or the site itself, came up before.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import store
from .sellers import registrable_domain

DB = store.DATA / "investigations.db"
_lock = threading.Lock()
STALE = timedelta(minutes=30)  # a scan still "running" after this was stopped (closed tab, restart)

SCHEMA = """
create table if not exists investigations (
  id text primary key, started_at text not null, finished_at text, brand text, keywords text, official_domains text,
  resellers text, image_sha256 text, origin text, scanned integer, listings integer, high integer, status text);
create table if not exists listings (
  id integer primary key, investigation_id text not null, idx integer, found_at text, page_url text, host text,
  domain text, title text, finding text, verification text, category text, score integer,
  network text, org text, shared text, platform integer);
create table if not exists listing_ips (listing_id integer not null, ip text not null);
create index if not exists listing_ips_ip on listing_ips(ip);
create index if not exists listings_domain on listings(domain);
create index if not exists listings_inv on listings(investigation_id);
"""


@contextmanager
def _db():
    with _lock:
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        try:
            con.executescript(SCHEMA)
            yield con
            con.commit()
        finally:
            con.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start(inv_id: str, brand: str, keywords: list[str], official_domains: list[str], resellers: list[str],
          image_sha256: str, origin: str = "scan") -> None:
    with _db() as con:
        con.execute("insert or replace into investigations (id, started_at, brand, keywords, official_domains, resellers,"
                    " image_sha256, origin, status) values (?, ?, ?, ?, ?, ?, ?, ?, 'running')",
                    (inv_id, _now(), brand, json.dumps(keywords), json.dumps(official_domains), json.dumps(resellers),
                     image_sha256, origin))


def finish(inv_id: str, scanned: int, status: str = "done") -> None:
    with _db() as con:
        n, high = con.execute("select count(*), coalesce(sum(score >= 65 and verification != 'unverified'), 0)"
                              " from listings where investigation_id = ?", (inv_id,)).fetchone()
        con.execute("update investigations set finished_at = ?, scanned = ?, listings = ?, high = ?, status = ? where id = ?",
                    (_now(), scanned, n, high, status, inv_id))


def site_key(host: str, platform: bool) -> str:
    """The site a listing belongs to. On a marketplace or social site every listing shares the domain, so there the
    listing's own URL is the unit instead (empty key)."""
    return "" if platform or not host else registrable_domain(host)


def earlier(inv_id: str, host: str, page_url: str, ips: list[str], platform: bool = False) -> list[dict]:
    """Listings already in the log on the same site, at the same URL, or on the same IP, except this site's own rows
    in this investigation. Newest first, one entry per listing."""
    domain = site_key(host, platform)
    if not domain and not ips and not page_url:
        return []
    marks = ",".join("?" * len(ips)) or "null"
    with _db() as con:
        rows = con.execute(f"""
            select l.id, l.investigation_id, l.found_at, l.page_url, l.host, l.domain, l.score, l.verification, l.shared,
                   l.network, i.brand, i.started_at,
                   (select group_concat(ip) from listing_ips x where x.listing_id = l.id and x.ip in ({marks})) as same_ips
            from listings l join investigations i on i.id = l.investigation_id
            where ((l.domain = ? and ? != '') or l.page_url = ?
                   or l.id in (select listing_id from listing_ips where ip in ({marks})))
              and not (l.investigation_id = ? and (l.domain = ? and ? != '' or l.page_url = ?))
            order by l.found_at desc, l.id desc limit 50""",
            (*ips, domain, domain, page_url or "", *ips, inv_id, domain, domain, page_url or "")).fetchall()
    return [{"investigation_id": r["investigation_id"], "brand": r["brand"], "started_at": r["started_at"],
             "found_at": r["found_at"], "page_url": r["page_url"], "host": r["host"], "score": r["score"],
             "verification": r["verification"], "same_site": bool(domain) and r["domain"] == domain,
             "same_url": bool(page_url) and r["page_url"] == page_url,
             "same_ips": (r["same_ips"] or "").split(",") if r["same_ips"] else [],
             "shared": r["shared"], "this_investigation": r["investigation_id"] == inv_id} for r in rows]


def crowd(host: str, page_url: str, platform: bool = False, days: int = 7) -> dict:
    """How many link checks looked at this site (or, on a marketplace or social site, this listing) in the last `days`
    days, and how many came out Not legit: {"checked", "flagged", "days"}."""
    key = site_key(host, platform)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with _db() as con:
        row = con.execute("""
            select count(distinct investigation_id) as checked,
                   count(distinct case when category = 'likely_counterfeit' then investigation_id end) as flagged
            from listings
            where verification = 'link-check' and found_at >= ? and (case when ? != '' then domain = ? else page_url = ? end)""",
            (since, key, key, page_url or "")).fetchone()
    return {"checked": row["checked"], "flagged": row["flagged"], "days": days}


def link_reasons(server: dict, seen: list[dict]) -> list[str]:
    """Risk reasons for a listing whose server or site is already in the log. An IP shared with other sites counts
    only off shared platforms (two Shopify shops share IPs without being related)."""
    out = []
    other = [s for s in seen if s["same_ips"] and not s["same_site"] and not s["same_url"]]
    hosts = sorted({s["host"] for s in other})
    if hosts and not server.get("shared"):
        ip = other[0]["same_ips"][0]
        where = f"{ip}, {server['network']}" if server.get("network") else ip
        out.append(f"Same server (IP {where}) as "
                   f"{len(hosts)} other site{'' if len(hosts) == 1 else 's'} in the investigation log: "
                   f"{', '.join(hosts[:4])}{' and more' if len(hosts) > 4 else ''}. Sites on one server are often run "
                   f"by one operator")
    repeats = {}
    for s in seen:
        if (s["same_site"] or s["same_url"]) and not s["this_investigation"]:
            repeats.setdefault(s["investigation_id"], s)
    if repeats:
        what = "This listing" if server.get("platform") else "This site"
        out.append(f"{what} was already found in {len(repeats)} earlier investigation{'' if len(repeats) == 1 else 's'}: "
                   + ", ".join(f"{s['brand']} on {s['started_at'][:10]}" for s in list(repeats.values())[:3]))
    return out


def add_listing(inv_id: str, m: dict, server: dict) -> None:
    r, f = m["risk"], m.get("forensics")
    host = r.get("host") or ""
    with _db() as con:
        cur = con.execute(
            "insert into listings (investigation_id, idx, found_at, page_url, host, domain, title, finding, verification,"
            " category, score, network, org, shared, platform) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (inv_id, m.get("idx"), m.get("captured_at") or _now(), m.get("page_url") or m.get("image_url"), host,
             site_key(host, server.get("platform")), m.get("title", ""), f["label"] if f else "Unverified",
             m.get("verification"), r.get("category"), r.get("score"), server.get("network"), server.get("org"),
             server.get("shared"), int(bool(server.get("platform")))))
        con.executemany("insert into listing_ips (listing_id, ip) values (?, ?)",
                        [(cur.lastrowid, ip) for ip in server.get("ips", [])])


def _status(row) -> str:
    if row["status"] != "running":
        return row["status"]
    started = datetime.fromisoformat(row["started_at"])
    return "stopped" if datetime.now(timezone.utc) - started > STALE else "running"


def history(limit: int = 100) -> list[dict]:
    """Past investigations, newest first, with how many of their listings link to another investigation."""
    with _db() as con:
        rows = con.execute("""
            select i.*, (select count(*) from listings l where l.investigation_id = i.id and exists (
                select 1 from listings o where o.investigation_id != i.id and (
                    (o.domain = l.domain and l.domain != '') or o.page_url = l.page_url
                    or (coalesce(l.shared, '') = '' and o.id in (
                        select a.listing_id from listing_ips a join listing_ips b on a.ip = b.ip
                        where b.listing_id = l.id))))) as linked
            from investigations i order by i.started_at desc, i.rowid desc limit ?""", (limit,)).fetchall()
    return [{"id": r["id"], "started_at": r["started_at"], "finished_at": r["finished_at"], "brand": r["brand"],
             "origin": r["origin"], "keywords": json.loads(r["keywords"] or "[]"), "scanned": r["scanned"],
             "listings": r["listings"], "high": r["high"], "linked": r["linked"], "status": _status(r)} for r in rows]


def listings(inv_id: str) -> list[dict]:
    with _db() as con:
        rows = con.execute("select l.*, (select group_concat(ip) from listing_ips x where x.listing_id = l.id) as ips"
                           " from listings l where investigation_id = ? order by score desc", (inv_id,)).fetchall()
    return [{**{k: r[k] for k in ("found_at", "page_url", "host", "domain", "title", "finding", "verification",
                                  "category", "score", "network", "org", "shared")},
             "platform": bool(r["platform"]), "ips": (r["ips"] or "").split(",") if r["ips"] else []} for r in rows]


def repeat_servers(min_listings: int = 2) -> list[dict]:
    """IPs that hosted at least min_listings listings, dedicated servers first: the more sites and investigations on
    one server that is not a shared platform, the stronger the link."""
    with _db() as con:
        rows = con.execute("""
            select x.ip, count(distinct l.id) as n, count(distinct l.domain) as sites,
                   count(distinct l.investigation_id) as investigations, max(l.found_at) as last_seen,
                   max(l.network) as network, max(l.org) as org, max(coalesce(l.shared, '')) as shared,
                   group_concat(distinct l.domain) as domains, group_concat(distinct i.brand) as brands
            from listing_ips x join listings l on l.id = x.listing_id join investigations i on i.id = l.investigation_id
            group by x.ip having n >= ?
            order by shared != '', sites desc, n desc, last_seen desc limit 200""", (min_listings,)).fetchall()
    # a site behind a CDN resolves to several IPs that all tell the same story: one row per set of sites and network
    out: dict[tuple, dict] = {}
    for r in rows:
        domains = sorted((r["domains"] or "").split(","))
        key = (tuple(domains), r["network"])
        if key in out:
            out[key]["ips"].append(r["ip"])
            continue
        out[key] = {"ips": [r["ip"]], "listings": r["n"], "sites": r["sites"], "investigations": r["investigations"],
                    "last_seen": r["last_seen"], "network": r["network"], "org": r["org"], "shared": r["shared"] or None,
                    "domains": domains, "brands": sorted((r["brands"] or "").split(","))}
    return list(out.values())
