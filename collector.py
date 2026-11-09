"""Poll every feed once, save new items and a health record.

Run on a schedule (GitHub Actions every 5 minutes). Writes:
  data/items/YYYY-MM-DD.jsonl   one line per new headline (by day first seen, UTC)
  data/health/YYYY-MM-DD.jsonl  one line per feed check
  data/state.json               ETag / Last-Modified per feed
  data/seen.json                keys already stored (pruned after 14 days)
"""
import calendar
import datetime as dt
import hashlib
import html
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests

ROOT = Path(__file__).parent
DATA = ROOT / "data"
UA = "crypto-wire-pilot/0.1 (+personal news research; polls every 5 min)"
SEEN_DAYS = 14
GDELT_PER_RUN = 5      # topics per 5-minute run: all 25 refresh about every 25 minutes
GDELT_PAUSE = 7        # seconds between GDELT requests


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True))


def append_jsonl(path, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def clean_text(s, limit=None):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit].rstrip() if limit and len(s) > limit else s


def canonical(url):
    """Drop tracking query strings and fragments so one story = one link."""
    if not url:
        return ""
    p = urlsplit(url.strip())
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower() or "https", p.netloc.lower(), path, "", ""))


def entry_time(e):
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return iso(dt.datetime.fromtimestamp(calendar.timegm(t), dt.timezone.utc))
    return None


def fetch(feed, state):
    headers = {"User-Agent": UA, "Cache-Control": "no-cache"}   # always ask for a fresh copy
    st = state.get(feed["id"], {})
    if st.get("etag"):
        headers["If-None-Match"] = st["etag"]
    if st.get("modified"):
        headers["If-Modified-Since"] = st["modified"]
    t0 = time.monotonic()
    r = requests.get(feed["url"], headers=headers, timeout=20)
    ms = int((time.monotonic() - t0) * 1000)
    return r, ms


def norm_title(t):
    """Same story, same title: lower-case words only, used to spot duplicates across feeds."""
    return " ".join(re.findall(r"[a-z0-9$]+", (t or "").lower()))[:160]


def publisher_slug(name):
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def load_trusted():
    cfg = load_json(ROOT / "trusted.json", {"trusted_only": False, "domains": {}})
    return cfg.get("trusted_only", False), cfg.get("domains", {})


def outlet_for(domain, trusted):
    """'www.ft.com' or 'markets.ft.com' -> 'Financial Times' when ft.com is on the trusted list."""
    d = (domain or "").lower().removeprefix("www.")
    parts = d.split(".")
    for i in range(len(parts) - 1):
        if ".".join(parts[i:]) in trusted:
            return trusted[".".join(parts[i:])]
    return None


def tidy_gdelt_title(t):
    """GDELT tokenises headlines: '$3 . 4 billion , FT' -> '$3.4 billion, FT'."""
    t = re.sub(r"(\d) \. (\d)", r"\1.\2", t or "")
    t = re.sub(r"\s+([,.:;?!)%])", r"\1", t)
    t = re.sub(r"([(\[$])\s+", r"\1", t)
    t = re.sub(r"\s+'\s*s\b", "'s", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def is_ap_syndication(url):
    """Local papers re-publish Associated Press stories under paths like /ap/... or /ap_news/..."""
    return bool(re.search(r"/(ap|ap_news|ap-news)(/|_)", (url or "").lower()))


def gdelt_entries(payload, trusted_only, trusted):
    """GDELT DOC 2.0 ArtList JSON -> entries shaped like feedparser's, so the rest of the pipeline is shared."""
    out = []
    for a in (payload or {}).get("articles", []):
        name = outlet_for(a.get("domain"), trusted) or ("AP" if is_ap_syndication(a.get("url")) else None)
        if trusted_only and not name:
            continue
        try:
            when = dt.datetime.strptime(a.get("seendate", ""), "%Y%m%dT%H%M%SZ").timetuple()
        except ValueError:
            when = None
        out.append({"link": a.get("url"), "id": a.get("url"), "title": tidy_gdelt_title(a.get("title")),
                    "published_parsed": when, "source": {"title": name or a.get("domain")}, "tags": []})
    return out


def items_from_entries(feed, entries, seen, t):
    """Turn parsed feed entries into stored items, skipping ones already seen.

    Google News topic feeds ("kind": "gnews") are searches, not outlets: each item is credited to
    the real publisher named in the feed (Financial Times, Bloomberg...), the " - Publisher" suffix
    is removed from the headline, and a story already collected from any feed or topic is skipped.
    """
    found, newest = [], None
    gnews = feed.get("kind") in ("gnews", "gdelt")   # aggregators: credit the real outlet
    for e in entries:
        link = canonical(e.get("link"))
        title = clean_text(e.get("title"))
        source, source_name = feed["id"], feed["name"]
        if gnews:
            src = e.get("source") or {}
            source_name = clean_text(src.get("title")) or source_name
            if source_name and title.endswith(" - " + source_name):
                title = title[: -len(" - " + source_name)].rstrip()
            source = "pub-" + publisher_slug(source_name)
        key = feed["id"] + "|" + (link or e.get("id") or title)
        tkey = "t|" + norm_title(title)
        published = entry_time(e)
        if published and (newest is None or published > newest):
            newest = published
        if key in seen or (gnews and tkey in seen):
            continue
        seen[key] = iso(t)
        seen.setdefault(tkey, iso(t))
        item = {
            "id": hashlib.sha1(key.encode()).hexdigest()[:16],
            "source": source,
            "title": title,
            "link": link or e.get("link"),
            "published": published,
            "first_seen": iso(t),
            "summary": "" if gnews else clean_text(e.get("summary"), 400),
            "tags": sorted({clean_text(x.get("term")) for x in e.get("tags", []) if x.get("term")})[:12],
        }
        if gnews:
            item.update({"source_name": source_name, "via": feed["id"], "topic": feed.get("group")})
        found.append(item)
    return found, newest


def run():
    feeds = load_json(ROOT / "feeds.json", [])
    state = load_json(DATA / "state.json", {})
    seen = load_json(DATA / "seen.json", {})
    t = now_utc()
    day = t.strftime("%Y-%m-%d")
    new_items, health = [], []

    trusted_only, trusted = load_trusted()
    # GDELT refuses rapid bursts (HTTP 429), so each run asks only a rotating handful of topics.
    gd = [f for f in feeds if f.get("kind") == "gdelt" and not f.get("disabled")]
    cur = state.get("_gdelt", {}).get("cursor", 0) % max(len(gd), 1)
    gd_now = {f["id"] for f in (gd[cur:] + gd[:cur])[:GDELT_PER_RUN]}
    state["_gdelt"] = {"cursor": (cur + GDELT_PER_RUN) % max(len(gd), 1)}
    gd_blocked = False
    for feed in feeds:
        if feed.get("disabled"):
            continue
        if feed.get("kind") == "gdelt":
            if feed["id"] not in gd_now or gd_blocked:
                continue
            time.sleep(GDELT_PAUSE)
        rec = {"ts": iso(t), "source": feed["id"], "status": "error",
               "http": None, "ms": None, "entries": 0, "new": 0, "error": None}
        try:
            r, ms = fetch(feed, state)
            rec["http"], rec["ms"] = r.status_code, ms
            st = state.setdefault(feed["id"], {})
            if r.status_code == 304:
                rec["status"] = "unchanged"
            elif r.status_code == 200:
                if feed.get("kind") == "gdelt":
                    try:
                        entries = gdelt_entries(r.json(), trusted_only, trusted)
                    except ValueError:   # GDELT answers bad queries with a plain-text message
                        raise requests.RequestException("GDELT: " + r.text[:160])
                else:
                    entries = feedparser.parse(r.content).entries or []
                rec["entries"] = len(entries)
                if not entries and feed.get("kind") == "gdelt":
                    rec["status"] = "ok"   # a quiet topic is normal, not a failure
                elif not entries:
                    rec["error"] = "feed returned no items"
                else:
                    rec["status"] = "ok"
                    st["etag"] = r.headers.get("ETag")
                    st["modified"] = r.headers.get("Last-Modified")
                    found, newest = items_from_entries(feed, entries, seen, t)
                    new_items += found
                    rec["new"] = len(found)
                    st["newest_item"] = newest
            else:
                rec["error"] = f"HTTP {r.status_code}"
                if r.status_code == 429 and feed.get("kind") == "gdelt":
                    gd_blocked = True   # back off: skip the rest of this run's GDELT topics
        except requests.RequestException as ex:
            rec["error"] = type(ex).__name__ + ": " + str(ex)[:200]
        if rec["status"] in ("ok", "unchanged"):
            state.setdefault(feed["id"], {})["last_ok"] = iso(t)
        health.append(rec)

    # First run seeds the backlog: mark it so delay stats ignore it.
    if not (DATA / "items").exists():
        for it in new_items:
            it["backfill"] = True

    cutoff = iso(t - dt.timedelta(days=SEEN_DAYS))
    seen = {k: v for k, v in seen.items() if v >= cutoff}

    append_jsonl(DATA / "items" / f"{day}.jsonl", new_items)
    append_jsonl(DATA / "health" / f"{day}.jsonl", health)
    save_json(DATA / "state.json", state)
    save_json(DATA / "seen.json", seen)
    for h in health:
        print(f"{h['source']:16} {h['status']:9} new={h['new']:3} {h['error'] or ''}")


if __name__ == "__main__":
    run()
