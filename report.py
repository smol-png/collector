"""Turn the collected items into docs/data.json for the dashboard.

Nothing here filters stories out. It only measures: volume per source,
feed health, how fast each outlet publishes, which stories several
outlets covered (and who was first), and which coins/topics come up.
"""
import datetime as dt
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "docs" / "data.json"
WINDOW_DAYS = 7
CLUSTER_HOURS = 72

COINS = {
    "BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "ether", "eth"], "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"], "BNB": ["bnb"], "DOGE": ["dogecoin", "doge"],
    "ADA": ["cardano", "ada"], "TRX": ["tron", "trx"], "TON": ["toncoin", "ton"],
    "AVAX": ["avalanche", "avax"], "LINK": ["chainlink"], "DOT": ["polkadot"],
    "LTC": ["litecoin", "ltc"], "HYPE": ["hyperliquid", "hype"], "SUI": ["sui"],
    "AAVE": ["aave"], "UNI": ["uniswap"], "ZEC": ["zcash", "zec"],
    "USDT": ["tether", "usdt"], "USDC": ["usdc"], "WLFI": ["world liberty", "wlfi"],
}
TOPICS = {
    "Hacks and exploits": ["hack", "exploit", "stolen", "heist", "breach", "drained", "attacker"],
    "ETFs and funds": ["etf", "fund", "inflow", "outflow", "treasury", "blackrock"],
    "Regulation and courts": ["sec", "cftc", "regulat", "court", "lawsuit", "bill", "senate", "congress", "tax", "ban", "license"],
    "Listings and launches": ["launch", "listing", "lists", "debut", "airdrop", "upgrade", "mainnet", "ipo"],
    "Prices and liquidations": ["price", "rally", "surge", "plunge", "tumble", "liquidat", "record high", "slide", "drops", "falls"],
    "Macro and the Fed": ["fed", "rate", "inflation", "jobs", "cpi", "dollar", "treasury yield", "recession"],
    "Companies and deals": ["acquire", "acquisition", "raise", "funding", "partners", "earnings", "revenue", "layoff"],
    "AI": [" ai ", "artificial intelligence", "openai", "anthropic", "nvidia", "data center"],
}
STOP = set("""a an the and or of to in on for with by at from as is are was were be been it its this that
these those after before over under into amid says said say new now just more than up down out off
why how what who will would could may might can not no yes than per vs via about""".split())
SYNONYM = {"bitcoin": "btc", "ethereum": "eth", "ether": "eth", "solana": "sol", "billion": "bn",
           "million": "mn", "hackers": "hack", "hacked": "hack", "heist": "hack", "theft": "hack"}


def parse_ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if s else None


def read_jsonl(folder, days, today):
    rows = []
    for i in range(days):
        p = folder / f"{(today - dt.timedelta(days=i)).strftime('%Y-%m-%d')}.jsonl"
        if p.exists():
            rows += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


def tokens(title):
    words = re.findall(r"[a-z0-9$]+", title.lower().replace("’", "'"))
    out = set()
    for w in words:
        w = SYNONYM.get(w.strip("$"), w.strip("$"))
        if w and w not in STOP and (len(w) > 2 or w.isdigit()):
            out.add(w)
    return out


def story_time(it):
    return parse_ts(it.get("published")) or parse_ts(it["first_seen"])


def clusters(items, names):
    """Group headlines from different outlets that describe the same story."""
    items = sorted(items, key=story_time)
    toks = [tokens(i["title"]) for i in items]
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if (story_time(items[b]) - story_time(items[a])).total_seconds() > 18 * 3600:
                break
            if items[a]["source"] == items[b]["source"]:
                continue
            shared = toks[a] & toks[b]
            union = toks[a] | toks[b]
            if len(shared) >= 3 and len(shared) / max(len(union), 1) >= 0.25:
                parent[find(b)] = find(a)

    groups = defaultdict(list)
    for i in range(len(items)):
        groups[find(i)].append(items[i])
    result = []
    for g in groups.values():
        if len({i["source"] for i in g}) < 2:
            continue
        g.sort(key=story_time)
        first_t = story_time(g[0])
        firsts, lane = set(), []
        for it in g:
            if it["source"] in firsts:
                continue
            firsts.add(it["source"])
            lane.append({"source": it["source"], "name": names.get(it["source"], it["source"]),
                         "after_min": round((story_time(it) - first_t).total_seconds() / 60),
                         "title": it["title"], "link": it["link"]})
        result.append({"title": g[0]["title"], "first": iso(first_t), "outlets": lane})
    result.sort(key=lambda c: (len(c["outlets"]), c["first"]), reverse=True)
    return result


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def count_mentions(items, table):
    c = Counter()
    for it in items:
        text = " " + re.sub(r"[^a-z0-9$ ]", " ", (it["title"] + " " + it.get("summary", "")).lower()) + " "
        for label, words in table.items():
            if any(((" " + w.strip() + " ") in text or (" " + w.strip() + "s ") in text)
                   if len(w.strip()) <= 4 else w in text for w in words):
                c[label] += 1
    return [{"label": k, "count": v} for k, v in c.most_common()]


def run():
    now = dt.datetime.now(dt.timezone.utc)
    feeds = json.loads((ROOT / "feeds.json").read_text())
    names = {f["id"]: f["name"] for f in feeds}
    items = read_jsonl(DATA / "items", WINDOW_DAYS + 1, now)
    health = read_jsonl(DATA / "health", 2, now)
    state = json.loads((DATA / "state.json").read_text()) if (DATA / "state.json").exists() else {}

    day_ago = now - dt.timedelta(hours=24)
    recent = [i for i in items if parse_ts(i["first_seen"]) >= day_ago]

    sources = []
    for f in feeds:
        sid = f["id"]
        mine = [i for i in items if i["source"] == sid]
        mine_24 = [i for i in recent if i["source"] == sid]
        checks = [h for h in health if h["source"] == sid and parse_ts(h["ts"]) >= day_ago]
        ok = [h for h in checks if h["status"] in ("ok", "unchanged")]
        last = max(checks, key=lambda h: h["ts"]) if checks else None
        lags = [(parse_ts(i["first_seen"]) - parse_ts(i["published"])).total_seconds() / 60
                for i in mine if i.get("published") and not i.get("backfill")]
        lags = [x for x in lags if 0 <= x < 24 * 60]
        newest = state.get(sid, {}).get("newest_item")
        sources.append({
            "id": sid, "name": f["name"],
            "items_24h": len(mine_24), "items_7d": len(mine),
            "checks_24h": len(checks), "ok_24h": len(ok),
            "last_status": last["status"] if last else None,
            "last_error": last["error"] if last else None,
            "newest_item": newest,
            "median_delay_min": round(median(lags)) if lags else None,
        })

    hours = Counter(parse_ts(i["first_seen"]).strftime("%Y-%m-%dT%H:00:00Z") for i in items
                    if parse_ts(i["first_seen"]) >= now - dt.timedelta(hours=48) and not i.get("backfill"))
    volume = []
    for h in range(47, -1, -1):
        k = (now - dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00Z")
        volume.append({"hour": k, "count": hours.get(k, 0)})

    cl_items = [i for i in items if story_time(i) >= now - dt.timedelta(hours=CLUSTER_HOURS)]
    latest = sorted(items, key=lambda i: (story_time(i), i["first_seen"]), reverse=True)[:200]

    out = {
        "generated": iso(now),
        "collecting_since": min((i["first_seen"] for i in items), default=None),
        "totals": {"items_24h": len(recent), "items_7d": len(items)},
        "sources": sources,
        "volume_48h": volume,
        "clusters": clusters(cl_items, names)[:40],
        "coins_24h": count_mentions(recent, COINS),
        "topics_24h": count_mentions(recent, TOPICS),
        "latest": [{k: i.get(k) for k in ("source", "title", "link", "published", "first_seen", "tags")} for i in latest],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False))
    print(f"report: {len(items)} items, {len(out['clusters'])} multi-outlet stories")


if __name__ == "__main__":
    run()
