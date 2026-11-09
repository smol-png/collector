"""Build docs/history.json for the history dashboard from items + Claude's matches.

Everything is measured on publish time, so backfilled and live items count the same.
"""
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from matcher import DATA, all_items, load, when

ROOT = Path(__file__).parent
OUT = ROOT / "docs" / "history.json"
DAYS = 7


def ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def run():
    now = dt.datetime.now(dt.timezone.utc)
    feeds = json.loads((ROOT / "feeds.json").read_text())
    names = {f["id"]: f["name"] for f in feeds}
    matches = load(DATA / "matches.json", {})
    events_meta = load(DATA / "events.json", {})
    storylines_meta = load(DATA / "storylines.json", {})
    cutoff = now - dt.timedelta(days=DAYS)
    items = [i for i in all_items() if i["id"] in matches and ts(when(i)) >= cutoff]

    by_event = defaultdict(list)
    for i in items:
        by_event[matches[i["id"]]["event"]].append(i)

    events = []
    for eid, members in by_event.items():
        members.sort(key=when)
        first = members[0]
        t0 = ts(when(first))
        lane, seen_src = [], set()
        for m in members:
            if m["source"] in seen_src:
                continue
            seen_src.add(m["source"])
            lane.append({"source": m["source"], "name": names.get(m["source"], m.get("source_name") or m["source"]),
                         "after_min": round((ts(when(m)) - t0).total_seconds() / 60),
                         "title": m["title"], "link": m["link"], "type": matches[m["id"]]["type"],
                         "summary": (m.get("summary") or "")[:400]})
        mt = [matches[m["id"]] for m in members]
        cats = Counter(x["category"] for x in mt)
        mkts = Counter(x.get("market", "Other") for x in mt)
        usd = [x["usd_m"] for x in mt if x["usd_m"]]
        meta = events_meta.get(eid, {})
        events.append({
            "id": eid,
            "title": meta.get("title") or first["title"],
            "storyline": meta.get("storyline"),
            "category": cats.most_common(1)[0][0],
            "market": mkts.most_common(1)[0][0],
            "outlets": len(lane),
            "spread_2h": sum(1 for o in lane if o["after_min"] <= 120),
            "first": when(first),
            "first_source": first["source"],
            "usd_m": max(usd) if usd else None,
            "tickers": sorted({t for x in mt for t in x["tickers"]}),
            "lane": lane,
            # what we knew at the first sighting, for the filter backtest
            "seed": {"type": mt[0]["type"], "category": mt[0]["category"], "usd_m": mt[0]["usd_m"],
                     "tickers": mt[0]["tickers"], "source": first["source"]},
        })
    events.sort(key=lambda e: (-e["outlets"], -e["spread_2h"], e["first"]))

    storylines = []
    for sid, meta in storylines_meta.items():
        evs = [e for e in events if e["storyline"] == sid]
        if not evs:
            continue
        srcs = {o["source"] for e in evs for o in e["lane"]}
        storylines.append({"id": sid, "name": meta["name"], "events": [e["id"] for e in evs],
                           "headlines": sum(len(by_event[e["id"]]) for e in evs), "outlets": len(srcs),
                           "first": min(e["first"] for e in evs), "last": max(e["first"] for e in evs)})
    storylines.sort(key=lambda s: (-s["headlines"], -s["outlets"]))

    outlets = []
    multi = [e for e in events if e["outlets"] >= 2]
    for f in feeds:
        sid = f["id"]
        mine = [i for i in items if i["source"] == sid]
        if not mine:
            outlets.append({"id": sid, "name": f["name"], "headlines": 0})
            continue
        types = Counter(matches[i["id"]]["type"] for i in mine)
        lags = [o["after_min"] for e in multi for o in e["lane"] if o["source"] == sid and o["after_min"] > 0]
        outlets.append({
            "id": sid, "name": f["name"], "headlines": len(mine),
            "first_on_shared": sum(1 for e in multi if e["first_source"] == sid),
            "in_shared": sum(1 for e in multi if any(o["source"] == sid for o in e["lane"])),
            "median_behind_min": round(median(lags)) if lags else None,
            "exclusive": sum(1 for e in events if e["outlets"] == 1 and e["first_source"] == sid),
            "not_news_share": round(1 - types.get("news", 0) / len(mine), 2),
        })

    span = [ts(when(i)) for i in items]
    out = {
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": {"from": min(span).strftime("%Y-%m-%dT%H:%M:%SZ") if span else None,
                   "to": max(span).strftime("%Y-%m-%dT%H:%M:%SZ") if span else None},
        "totals": {"headlines": len(items), "events": len(events), "shared_events": len(multi),
                   "storylines": len(storylines)},
        "types": dict(Counter(matches[i["id"]]["type"] for i in items)),
        "events": events, "storylines": storylines, "outlets": outlets,
        "headlines": [{"source": i["source"], "source_name": i.get("source_name"), "via": i.get("via"), "title": i["title"], "link": i["link"], "published": when(i), "summary": (i.get("summary") or "")[:400],
                       "type": matches[i["id"]]["type"], "category": matches[i["id"]]["category"],
                       "market": matches[i["id"]].get("market", "Other"),
                       "tickers": matches[i["id"]]["tickers"], "event": matches[i["id"]]["event"]}
                      for i in sorted(items, key=when, reverse=True)],
        "feeds": [{"id": f["id"], "name": f["name"], "url": f["url"], "unverified": bool(f.get("unverified"))} for f in feeds],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False))
    print(f"history: {len(items)} headlines, {len(events)} events, {len(multi)} shared, {len(storylines)} storylines")


if __name__ == "__main__":
    run()
