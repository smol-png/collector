"""Collect older headlines (default: last 7 days) by paging back through feeds.

    python backfill.py            # 7 days
    python backfill.py --days 10

Only feeds with a "pages" pattern in feeds.json are paged (WordPress-style
?paged=2, 3, ...). Paging stops when a page is empty, repeats page 1, adds
nothing new, or reaches items older than the cutoff. Safe to run more than once.
Items are marked "backfill" so speed stats (which need live polling) skip them;
the history dashboard uses each item's publish time, so they count fully there.
"""
import argparse
import datetime as dt
import time

import feedparser
import requests

from collector import (DATA, ROOT, UA, append_jsonl, entry_time, items_from_entries,
                       iso, load_json, now_utc, save_json)

MAX_PAGES = 30


def get_entries(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    return feedparser.parse(r.content).entries or [], None


def run(days):
    feeds = load_json(ROOT / "feeds.json", [])
    seen = load_json(DATA / "seen.json", {})
    t = now_utc()
    cutoff = iso(t - dt.timedelta(days=days))
    all_new = []
    for feed in feeds:
        urls = [feed["url"]] + ([feed["pages"].format(n=n) for n in range(2, MAX_PAGES + 1)] if feed.get("pages") else [])
        first_links, added, oldest = None, 0, None
        for n, url in enumerate(urls, start=1):
            try:
                entries, err = get_entries(url)
            except requests.RequestException as ex:
                print(f"{feed['id']:16} page {n}: {type(ex).__name__}")
                break
            if err or not entries:
                print(f"{feed['id']:16} page {n}: {err or 'empty'}, stopping")
                break
            links = [e.get("link") for e in entries]
            if n == 1:
                first_links = links
            elif links == first_links:
                print(f"{feed['id']:16} page {n} repeats page 1, paging not supported")
                break
            found, _ = items_from_entries(feed, entries, seen, t)
            found = [f for f in found if not f["published"] or f["published"] >= cutoff]
            for f in found:
                f["backfill"] = True
            all_new += found
            added += len(found)
            dates = [e for e in (f.get("published") for f in found) if e]
            oldest = min(dates + ([oldest] if oldest else []), default=oldest)
            page_dates = [x for x in (entry_time(e) for e in entries) if x]
            if page_dates and min(page_dates) < cutoff:
                break
            time.sleep(1.5)  # be polite
        print(f"{feed['id']:16} +{added} items, oldest {oldest or '-'}")
    append_jsonl(DATA / "items" / f"{t.strftime('%Y-%m-%d')}.jsonl", all_new)
    save_json(DATA / "seen.json", seen)
    print(f"backfill done: {len(all_new)} items")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    run(ap.parse_args().days)
