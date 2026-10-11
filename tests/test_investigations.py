"""The investigation log: every scan and listing is kept, with the listing's server IPs, so a later investigation sees
a server or site that came up before. Shared platforms (Shopify, Cloudflare) never count as a link."""
import asyncio
import io
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from PIL import Image

from app import infra, investigations, pipeline, search
from app.main import app

SERVERS = {  # host -> (ips, network, org)
    "shop-a.example": (["203.0.113.7"], "EXAMPLE-VPS", "Example Hosting Ltd"),
    "shop-b.example": (["203.0.113.7", "2001:db8::7"], "EXAMPLE-VPS", "Example Hosting Ltd"),
    "store-c.example": (["23.227.38.65"], "SHOPIFY-NET", "Shopify, Inc."),
    "store-d.example": (["23.227.38.65"], "SHOPIFY-NET", "Shopify, Inc."),
}


def _img() -> bytes:
    b = io.BytesIO()
    Image.effect_noise((256, 256), 60).convert("RGB").save(b, "JPEG", quality=90)
    return b.getvalue()


def _scan(monkeypatch, img, brand, pages, origin="scan"):
    cands = [{"image_url": f"demo://{i}", "page_url": p, "title": "item", "source": "t", "blob": img}
             for i, p in enumerate(pages)]

    async def fake_discover(*a, **k): return cands
    async def none(*a, **k): return []

    async def describe(host, client):
        if infra.platform_host(host):
            return {"ips": [], "network": None, "org": None, "range": None, "shared": None, "platform": True}
        ips, network, org = SERVERS[host]
        return {"ips": ips, "network": network, "org": org, "range": None, "shared": infra.shared_name(network, org),
                "platform": False}
    monkeypatch.setattr(search, "discover", fake_discover)
    monkeypatch.setattr(search, "reverse_candidates", none)
    monkeypatch.setattr(infra, "describe", describe)

    async def go():
        return [e async for e in pipeline.run_scan(img, brand, [], [], origin=origin)]
    ev = asyncio.run(go())
    return {d["risk"]["host"]: d for e, d in ev if e == "match"}, [d for e, d in ev if e == "done"][0]


def test_a_server_seen_in_an_earlier_investigation_links_the_listings(monkeypatch):
    img = _img()
    first, done1 = _scan(monkeypatch, img, "Acme", ["https://shop-a.example/p/1", "https://store-c.example/p"])
    assert first["shop-a.example"]["server"]["ips"] == ["203.0.113.7"] and first["shop-a.example"]["server"]["seen_count"] == 0

    second, done2 = _scan(monkeypatch, img, "Globex", ["https://shop-b.example/x", "https://shop-a.example/p/2",
                                                       "https://store-d.example/q"], origin="watch")
    # a different site on the same dedicated server: linked, with a reason that ends up in the evidence PDF
    b = second["shop-b.example"]
    assert any(s["host"] == "shop-a.example" and s["same_ips"] == ["203.0.113.7"] for s in b["server"]["seen_before"])
    assert any(r.startswith("Same server (IP 203.0.113.7, EXAMPLE-VPS) as 1 other site in the investigation log: shop-a.example")
               for r in b["risk"]["reasons"])
    # the same site again: a repeat offender
    assert any("This site was already found in 1 earlier investigation: Acme on" in r for r in second["shop-a.example"]["risk"]["reasons"])
    # two Shopify shops share an IP without being related: recorded, but no link reason
    d = second["store-d.example"]
    assert d["server"]["shared"] == "Shopify" and d["server"]["seen_before"][0]["host"] == "store-c.example"
    assert not any("Same server" in r for r in d["risk"]["reasons"])

    c = TestClient(app)
    hist = c.get("/api/investigations").json()
    assert [(h["brand"], h["origin"], h["status"], h["listings"]) for h in hist] == [("Globex", "watch", "done", 3), ("Acme", "scan", "done", 2)]
    assert hist[0]["linked"] == 2  # shop-b (same server as shop-a) and shop-a (same site); store-d is only Shopify
    servers = c.get("/api/servers").json()
    assert [(s["ips"], s["shared"], s["sites"], s["investigations"]) for s in servers] == [
        (["203.0.113.7"], None, 2, 2), (["23.227.38.65"], "Shopify", 2, 2)]  # dedicated servers first
    assert servers[0]["domains"] == ["shop-a.example", "shop-b.example"] and servers[0]["brands"] == ["Acme", "Globex"]
    rows = c.get(f"/api/investigations/{done2['scan_id']}").json()["listings"]
    assert {r["host"]: r["ips"] for r in rows}["shop-b.example"] == ["203.0.113.7", "2001:db8::7"]


def test_marketplace_listings_link_only_by_their_own_url(monkeypatch):
    """Every AliExpress listing shares the domain and the platform's IPs, so only the same listing URL is a repeat."""
    img = _img()
    _scan(monkeypatch, img, "Acme", ["https://www.aliexpress.com/item/1.html"])
    second, _ = _scan(monkeypatch, img, "Acme", ["https://www.aliexpress.com/item/2.html", "https://www.aliexpress.com/item/1.html"])
    assert second["aliexpress.com"]["server"]["platform"] is True
    rows = {d["page_url"]: d for d in [second["aliexpress.com"]]}
    hist = TestClient(app).get("/api/investigations").json()
    assert hist[0]["linked"] == 1  # item 1 again; item 2 is new even though it is on the same marketplace
    assert all(not r.startswith("Same server") for d in rows.values() for r in d["risk"]["reasons"])


def test_demo_copies_are_not_logged_and_a_stopped_scan_shows_as_stopped(monkeypatch, tmp_path):
    investigations.start("old1", "Acme", [], [], [], "sha", "scan")
    with investigations._db() as con:
        con.execute("update investigations set started_at = ? where id = 'old1'",
                    ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds"),))
    investigations.add_listing("old1", {"idx": 0, "page_url": "https://x.example/p", "risk": {"host": "x.example", "score": 70,
                                                                                              "category": "unknown"},
                                        "forensics": {"label": "Exact"}, "verification": "verified"}, {})
    assert [(h["id"], h["status"]) for h in investigations.history()] == [("old1", "stopped")]

    img = _img()
    monkeypatch.setattr(pipeline.demo, "doctored_copies", lambda image: [
        {"image_url": "demo://d", "page_url": "https://demo.example/d", "title": "demo", "source": "DEMO SAMPLE",
         "blob": img, "demo": True}])
    hosts, done = _scan(monkeypatch, img, "Acme", [])
    assert hosts == {}  # sanity: no real candidates
    async def go():
        return [e async for e in pipeline.run_scan(img, "Acme", [], [], inject_demo=True)]
    ev = asyncio.run(go())
    assert any(e == "match" for e, _ in ev)
    scan_id = [d for e, d in ev if e == "done"][0]["scan_id"]
    assert investigations.listings(scan_id) == []


def test_network_lookup_parsing_and_shared_platforms():
    doc = {"name": "CLOUDFLARENET", "startAddress": "104.16.0.0", "endAddress": "104.31.255.255",
           "entities": [{"roles": ["registrant"], "vcardArray": ["vcard", [["fn", {}, "text", "Cloudflare, Inc."]]]},
                        {"roles": ["abuse"], "vcardArray": ["vcard", [["fn", {}, "text", "Abuse"]]]}]}
    info = infra._parse(doc)
    assert info == {"network": "CLOUDFLARENET", "org": "Cloudflare, Inc.", "range": "104.16.0.0 - 104.31.255.255"}
    assert infra.shared_name(info["network"], info["org"]) == "Cloudflare"
    assert infra.shared_name("AMAZO-CF", "Amazon.com, Inc.") == "Amazon CloudFront"
    assert infra.shared_name("DIGITALOCEAN-ASN", "DigitalOcean, LLC") is None  # a rented server is not shared
    assert infra.platform_host("www.aliexpress.com") and infra.platform_host("ebay.co.uk")
    assert not infra.platform_host("betsyjohnson.com")


def test_describe_skips_platforms_and_works_offline(monkeypatch):
    monkeypatch.setenv("BG_IP_LOOKUP", "1")
    async def resolve(host): return ["198.51.100.4"]
    async def network(ip, client): return {"network": "SHOPIFY-NET", "org": "Shopify, Inc.", "range": None}
    monkeypatch.setattr(infra, "resolve", resolve)
    monkeypatch.setattr(infra, "network", network)
    got = asyncio.run(infra.describe("valeen.co", None))
    assert got["ips"] == ["198.51.100.4"] and got["shared"] == "Shopify" and not got["platform"]
    assert asyncio.run(infra.describe("www.amazon.com", None))["platform"] is True
    monkeypatch.setenv("BG_IP_LOOKUP", "0")
    assert asyncio.run(infra.describe("valeen.co", None))["ips"] == []


def test_the_scan_endpoint_records_where_a_scan_came_from(monkeypatch, tmp_path):
    import app.main as main
    monkeypatch.setattr(main, "DATA", tmp_path)  # the endpoint also saves each scan for replay: never into data/
    async def none(*a, **k): return []
    monkeypatch.setattr(search, "discover", none)
    monkeypatch.setattr(search, "reverse_candidates", none)
    c = TestClient(app)
    for origin in ("imessage", "anything-else"):
        c.post("/api/scan", files={"image": ("a.jpg", _img(), "image/jpeg")}, data={"brand": "Acme", "origin": origin}).read()
    assert [h["origin"] for h in c.get("/api/investigations").json()] == ["scan", "imessage"]


def test_a_site_behind_several_ips_is_one_server_row():
    for inv in ("i1", "i2"):
        investigations.start(inv, "Acme", [], [], [], "sha", "scan")
        investigations.add_listing(inv, {"idx": 0, "page_url": "https://cdn-site.example/p", "verification": "verified",
                                         "risk": {"host": "cdn-site.example", "score": 40, "category": "unknown"},
                                         "forensics": {"label": "Exact"}},
                                   {"ips": ["104.20.46.45", "172.66.146.250"], "network": "CLOUDFLARENET",
                                    "org": "Cloudflare, Inc.", "shared": "Cloudflare"})
    rows = investigations.repeat_servers()
    assert len(rows) == 1 and sorted(rows[0]["ips"]) == ["104.20.46.45", "172.66.146.250"] and rows[0]["listings"] == 2


def test_newest_first_holds_when_two_investigations_start_in_the_same_second(monkeypatch):
    """Times are stored to the second, so a tie must fall back to insertion order (it flipped on a fast CI runner)."""
    monkeypatch.setattr(investigations, "_now", lambda: "2026-10-10T12:00:00+00:00")
    for inv_id, brand in (("inv-1", "Acme"), ("inv-2", "Globex"), ("inv-3", "Initech")):
        investigations.start(inv_id, brand, [], [], [], "")
        investigations.finish(inv_id, 0)
    assert [h["brand"] for h in investigations.history()] == ["Initech", "Globex", "Acme"]
