"""Use Claude to understand each new headline.

For every headline not yet processed, Claude returns:
  type        news / analysis / opinion / explainer / roundup / live / sponsored / other
  category    one of CATEGORIES
  tickers     coins or stocks named
  usd_m       biggest dollar amount in the headline, in millions (or null)
  event       which specific happening it reports (same event = same story, however it's worded)
  storyline   the ongoing saga the event belongs to (e.g. "Binance $6.3B hack"), or none

Results are saved in data/ (matches.json, events.json, storylines.json) so each
headline is only ever sent once. Uses ANTHROPIC_API_KEY when set; without it, basic_matcher.py
labels headlines with simple rules so Collector still works.

    python matcher.py              # process everything new
    python matcher.py --dry-run    # show what would be sent, no API calls
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
MODEL = os.environ.get("MATCH_MODEL", "claude-sonnet-5-5")
BATCH = 25
CONTEXT_HOURS = 96          # how far back existing events are offered for matching
CATEGORIES = ["Hacks & security", "Regulation & enforcement", "Courts & lawsuits", "ETFs & fund flows",
              "Markets & prices", "Macro & rates", "Companies & earnings", "Deals & funding",
              "Launches & upgrades", "AI", "Politics", "Other"]
MARKETS = ["Crypto", "Stocks", "AI", "Macro", "Prediction markets", "Other"]
TYPES = ["news", "analysis", "opinion", "explainer", "roundup", "live", "sponsored", "other"]

SYSTEM = f"""You sort crypto and markets news headlines from several outlets.

Definitions:
- EVENT: one specific thing that happened, e.g. "US Treasury's FinCEN cuts MEXC off from the US financial system".
  Headlines from different outlets about the same happening are the SAME event even when worded very differently,
  focusing on different details, or adding commentary. A genuinely new development (a new number, an official
  response, a new action by a different party) is a NEW event.
- STORYLINE: an ongoing saga that several events belong to over days or weeks, e.g. "Binance $6.3B hack".
  Only create a storyline when an event clearly belongs to a bigger ongoing saga; otherwise use null.

For each new headline decide:
- type: one of {TYPES}. "roundup" = daily digests and newsletters; "live" = live blogs; "explainer" = "what is X";
  "analysis" = interpretive pieces built on data; "opinion" = columns and editorials.
- category: the topic, one of {CATEGORIES}.
- market: the market it matters to most, one of {MARKETS}. Listed companies (miners, treasury firms, Coinbase) are Stocks;
  tokens, exchanges and onchain news are Crypto; AI labs and AI policy are AI; jobs, rates and central banks are Macro.
- tickers: coin or stock symbols clearly named or implied by a named asset/company (BTC for bitcoin, COIN for Coinbase).
- usd_m: the largest US-dollar amount in the headline or summary, in millions (6.3 billion -> 6300), else null.
- event: an existing event id from the list if it is the same happening; otherwise "new:" followed by a short neutral
  title for the event. Headlines in this batch that report the same new happening must use the identical "new:" title.
- storyline: an existing storyline id, or "new:" plus a short name, or null.

Reply with JSON only, no prose: {{"items":[{{"id":"...","type":"...","category":"...","market":"...","tickers":[],"usd_m":null,"event":"...","storyline":null}}]}}"""


def load(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1))


def all_items():
    rows = []
    for p in sorted((DATA / "items").glob("*.jsonl")):
        rows += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


def when(it):
    return it.get("published") or it["first_seen"]


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def call_claude(client, payload):
    for attempt in range(4):
        try:
            msg = client.messages.create(
                model=MODEL, max_tokens=6000, system=SYSTEM,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
            return parse_json("".join(b.text for b in msg.content if b.type == "text"))
        except Exception as ex:  # rate limits, overload, malformed JSON: back off and retry
            wait = 5 * 2 ** attempt
            print(f"  retry in {wait}s: {type(ex).__name__}: {str(ex)[:120]}")
            time.sleep(wait)
    raise RuntimeError("Claude did not return usable JSON after 4 attempts")


def run(dry_run=False):
    matches = load(DATA / "matches.json", {})
    events = load(DATA / "events.json", {})
    storylines = load(DATA / "storylines.json", {})
    todo = sorted((i for i in all_items() if i["id"] not in matches), key=when)
    if not todo:
        print("matcher: nothing new")
        return
    client = None
    if not dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        # No key yet: use the basic rule-based matcher so the timeline still fills.
        import basic_matcher
        n = basic_matcher.run(todo, matches, events, storylines)
        save(DATA / "matches.json", matches)
        save(DATA / "events.json", events)
        save(DATA / "storylines.json", storylines)
        print(f"matcher: {n} new headlines labelled by basic rules (add ANTHROPIC_API_KEY to switch to Claude)")
        return
    print(f"matcher: {len(todo)} new headlines, model {MODEL}")
    if not dry_run:
        import anthropic
        client = anthropic.Anthropic()

    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        horizon = (dt.datetime.fromisoformat(when(batch[0]).replace("Z", "+00:00"))
                   - dt.timedelta(hours=CONTEXT_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        open_events = [{"id": k, "title": v["title"], "storyline": v.get("storyline")}
                       for k, v in events.items() if v["last"] >= horizon]
        payload = {
            "existing_storylines": [{"id": k, "name": v["name"]} for k, v in storylines.items()],
            "existing_events": open_events,
            "new_headlines": [{"id": i["id"], "source": i["source"], "published": when(i),
                               "title": i["title"], "summary": i.get("summary", "")[:280]} for i in batch],
        }
        if dry_run:
            print(json.dumps(payload, ensure_ascii=False)[:1500], "...")
            return
        result = call_claude(client, payload)
        new_ev_ids, new_sl_ids = {}, {}
        by_id = {i["id"]: i for i in batch}
        for r in result.get("items", []):
            it = by_id.get(r.get("id"))
            if not it:
                continue
            sl = r.get("storyline")
            if isinstance(sl, str) and sl.startswith("new:"):
                name = sl[4:].strip()
                sl = new_sl_ids.get(name) or f"s{len(storylines) + 1}"
                new_sl_ids[name] = sl
                storylines.setdefault(sl, {"name": name})
            elif sl not in storylines:
                sl = None
            ev = r.get("event") or "new:" + it["title"]
            if ev.startswith("new:") or ev not in events:
                title = ev[4:].strip() if ev.startswith("new:") else it["title"]
                ev = new_ev_ids.get(title) or f"e{len(events) + 1}"
                new_ev_ids[title] = ev
                events.setdefault(ev, {"title": title, "storyline": sl, "first": when(it), "last": when(it)})
            e = events[ev]
            e["last"] = max(e["last"], when(it))
            e["first"] = min(e["first"], when(it))
            if sl and not e.get("storyline"):
                e["storyline"] = sl
            matches[it["id"]] = {
                "type": r.get("type") if r.get("type") in TYPES else "other",
                "category": r.get("category") if r.get("category") in CATEGORIES else "Other",
                "market": r.get("market") if r.get("market") in MARKETS else "Other",
                "tickers": [str(t).upper() for t in (r.get("tickers") or [])][:8],
                "usd_m": r.get("usd_m") if isinstance(r.get("usd_m"), (int, float)) else None,
                "event": ev,
            }
        save(DATA / "matches.json", matches)
        save(DATA / "events.json", events)
        save(DATA / "storylines.json", storylines)
        print(f"  batch {start // BATCH + 1}: {len(batch)} headlines, {len(events)} events so far")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    run(ap.parse_args().dry_run)
