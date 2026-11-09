"""Run the whole pilot on your own computer with one command.

    python run_local.py

Checks the feeds every 5 minutes, rebuilds the dashboard after each check,
and serves it at http://localhost:8000. Stop it with Ctrl+C.
Everything is saved in data/, so you can later upload that folder to GitHub
and the cloud version carries on from where this left off.
"""
import functools
import http.server
import threading
import time
import traceback
import webbrowser

import os

import collector
import history
import matcher
import report

PORT = 8000
EVERY_MINUTES = 5


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep the terminal readable
        pass


def serve():
    handler = functools.partial(QuietHandler, directory=str(report.ROOT / "docs"))
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler).serve_forever()


def main():
    threading.Thread(target=serve, daemon=True).start()
    url = f"http://localhost:{PORT}"
    print(f"Dashboard: {url}  (refresh the page to see new data)\n")
    first = True
    while True:
        print(time.strftime("%H:%M"), "checking feeds")
        try:
            collector.run()
            report.run()
            if os.environ.get("ANTHROPIC_API_KEY"):
                matcher.run()
                history.run()
        except Exception:
            traceback.print_exc()
        if first:
            webbrowser.open(url)
            first = False
        print(f"next check in {EVERY_MINUTES} minutes (Ctrl+C to stop)\n")
        time.sleep(EVERY_MINUTES * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped, data kept in data/")
