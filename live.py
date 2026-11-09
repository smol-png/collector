"""Near-real-time collection on GitHub Actions.

Runs for about 55 minutes inside one workflow job:
  - every minute: check the direct feeds (outlets, official sources, Truth Social)
  - every 5th minute: also query the next batch of GDELT topics and log feed health
  - after new headlines: label them (Claude if ANTHROPIC_API_KEY is set, rules otherwise) and push to the repo
  - every 10 minutes: ask GitHub to rebuild the dashboard
At the end the workflow starts its own next run, so collection never stops.

    python live.py --minutes 55
"""
import argparse
import os
import subprocess
import time

import requests

import collector
import matcher

REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def push_if_changed(message):
    sh("git", "add", "data")
    if sh("git", "diff", "--cached", "--quiet").returncode == 0:
        return False
    sh("git", "commit", "-q", "-m", message)
    for _ in range(3):
        sh("git", "pull", "-q", "--rebase")
        if sh("git", "push", "-q").returncode == 0:
            return True
        time.sleep(5)
    return False


def dispatch(workflow):
    if not (REPO and TOKEN):
        return
    requests.post(f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow}/dispatches",
                  headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"},
                  json={"ref": "main"}, timeout=20)


def items_count():
    return sum(1 for p in (collector.DATA / "items").glob("*.jsonl") for _ in open(p, encoding="utf-8"))


def main(minutes):
    start = time.monotonic()
    i, last_dash, pending_dash = 0, -1e9, False
    while time.monotonic() - start < minutes * 60:
        t0 = time.monotonic()
        full = i % 5 == 0                       # GDELT + health every 5th minute
        before = items_count() if (collector.DATA / "items").exists() else 0
        try:
            collector.run(include_gdelt=full, write_health=full)
            if items_count() > before:
                matcher.run()
                pending_dash = True
        except Exception as ex:                 # one bad pass must not stop the loop
            print("pass failed:", type(ex).__name__, ex)
        stamp = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
        # push new stories at once; otherwise only every 5th minute (state and health)
        if pending_dash or full:
            push_if_changed(f"live {stamp}")
        if pending_dash and time.monotonic() - last_dash >= 600:
            dispatch("dashboard.yml")
            last_dash, pending_dash = time.monotonic(), False
        i += 1
        time.sleep(max(5, 60 - (time.monotonic() - t0)))
    if pending_dash:
        dispatch("dashboard.yml")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=55)
    main(ap.parse_args().minutes)
