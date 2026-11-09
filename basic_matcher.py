"""No-AI fallback for matcher.py, used automatically when ANTHROPIC_API_KEY is not set.

Produces the same files as the Claude matcher (data/matches.json, events.json, storylines.json),
so the dashboard works unchanged. It is rule-based and less accurate:
  - same story  = headlines within 36 hours sharing enough distinctive words or figures
  - type        = roundup / live / explainer / analysis / sponsored / news, from title patterns
  - category    = first matching keyword group
  - market      = Prediction markets, Crypto, AI, Stocks, Macro or Other, from tickers and keywords
  - tickers     = coin and stock names found in the headline
  - usd_m       = largest dollar figure in the headline, in millions
Storylines are not created (that needs Claude). Already-labelled headlines are never relabelled,
so switching to Claude later only affects new headlines.
"""
import datetime as dt
import re

CATEGORIES = [  # first match wins, order matters
    ("Hacks & security", r"\b(hack|hacked|hackers|exploit|stolen|heist|breach|drained|attacker|phishing|security)\b"),
    ("Courts & lawsuits", r"\b(sues|sued|lawsuit|court|judge|pleads|guilty|indicted|extradit|injunction|trial)\b"),
    ("Regulation & enforcement", r"\b(sec|cftc|fincen|ofac|sanction|regulat|license|licence|ban|bans|tax|treasury|bill|act)\b"),
    ("ETFs & fund flows", r"\b(etf|etfs|inflows?|outflows?)\b"),
    ("Macro & rates", r"\b(fed|federal reserve|fomc|powell|cpi|inflation|jobs|payrolls|unemployment|rate hike|rate cut|yields?|recession|gdp|bank of england|ecb)\b"),
    ("Deals & funding", r"\b(acquire|acquires|acquisition|buys|raises|funding|deal|merger|stake|invest)\b"),
    ("Companies & earnings", r"\b(earnings|revenue|profit|q[1-4]|quarter|ceo|cfo|layoffs?|steps down|ipo)\b"),
    ("Launches & upgrades", r"\b(launch|launches|upgrade|mainnet|testnet|fork|debut|rolls out|unveils)\b"),
    ("Politics", r"\b(trump|white house|congress|senate|democrats?|republicans?|election|midterms?|president)\b"),
    ("Markets & prices", r"\b(price|rally|surges?|plunges?|tumbles?|slides?|drops?|falls?|record high|liquidat|selloff|sell-off)\b"),
    ("AI", r"\b(ai|openai|anthropic|chatgpt|claude|superintelligence|nvidia|data cent)"),
]
CRYPTO_T = {"bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "ether": "ETH", "eth": "ETH", "solana": "SOL", "xrp": "XRP",
            "bnb": "BNB", "dogecoin": "DOGE", "cardano": "ADA", "tron": "TRX", "zcash": "ZEC", "hyperliquid": "HYPE",
            "aave": "AAVE", "tether": "USDT", "usdt": "USDT", "usdc": "USDC", "chainlink": "LINK", "litecoin": "LTC"}
STOCK_T = {"nvidia": "NVDA", "tesla": "TSLA", "apple": "AAPL", "microsoft": "MSFT", "alphabet": "GOOGL", "google": "GOOGL",
           "meta": "META", "amazon": "AMZN", "coinbase": "COIN", "strategy": "MSTR", "microstrategy": "MSTR", "robinhood": "HOOD"}
CRYPTO_WORDS = r"\b(crypto|bitcoin|btc|stablecoin|binance|ether|ethereum|token|blockchain|defi|onchain|on-chain|wallet|exchange|miners?|mining|coinbase|kraken|okx|bybit|tether|mexc|bitget|gopax|gnosis|signalplus|ledger)\b"
STOP = set("""a an the and or of to in on for with by at from as is are was were be been it its this that these those after
before over under into amid says said say new now just more than up down out off why how what who will would could
may might can not no yes per vs via about report reports update news""".split())


def when(it):
    return it.get("published") or it["first_seen"]


def kind_of(title):
    t = title.lower()
    if re.search(r"(here.s what happened|morning minute|daybook|daily recap|weekly recap|week in review)", t):
        return "roundup"
    if t.startswith("live") or "live updates" in t or "live blog" in t:
        return "live"
    if re.match(r"^(what is|what are|how to|explainer|why )", t):
        return "explainer"
    if re.search(r"(sponsored|press release|partner content)", t):
        return "sponsored"
    if re.search(r"\b(analysis|opinion|column)\b", t):
        return "analysis"
    return "news"


def usd_of(title):
    best = None
    for num, unit in re.findall(r"\$\s?([\d.,]+)\s?(trillion|tn|billion|bn|b|million|mn|m)?\b", title.lower()):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        v = v * 1e6 if unit in ("trillion", "tn") else v * 1e3 if unit in ("billion", "bn", "b") else v if unit in ("million", "mn", "m") else v / 1e6
        best = v if best is None or v > best else best
    return round(best, 2) if best else None


def label(title):
    t = title.lower()
    tickers = sorted({v for k, v in {**CRYPTO_T, **STOCK_T}.items() if re.search(r"\b" + re.escape(k) + r"\b", t)})
    category = next((c for c, rx in CATEGORIES if re.search(rx, t)), "Other")
    if re.search(r"kalshi|polymarket|prediction market", t):
        market = "Prediction markets"
    elif any(x in CRYPTO_T.values() for x in tickers) or re.search(CRYPTO_WORDS, t):
        market = "Crypto"
    elif category == "AI" or re.search(r"\b(ai|openai|anthropic)\b", t):
        market = "AI"
    elif any(x in STOCK_T.values() for x in tickers) or re.search(r"\b(stocks?|shares|s&p|nasdaq|dow|ipo|wall street)\b", t):
        market = "Stocks"
    elif category == "Macro & rates":
        market = "Macro"
    else:
        market = "Other"
    return {"type": kind_of(title), "category": category, "tickers": tickers[:6], "usd_m": usd_of(title), "market": market}


def words(title):
    out = set()
    for w in re.findall(r"[a-z0-9$.]+", title.lower()):
        w = w.strip(".")
        w = {"bitcoin": "btc", "ethereum": "eth", "ether": "eth", "billion": "bn", "hackers": "hack", "hacked": "hack"}.get(w, w)
        if w and w not in STOP and (len(w) > 2 or any(c.isdigit() for c in w)):
            out.add(w)
    return out


def same_story(a, b):
    shared = a & b
    if not shared:
        return False
    figures = {w for w in shared if any(c.isdigit() for c in w)}
    return len(shared) >= 4 or (len(shared) >= 3 and len(shared) / len(a | b) >= 0.3) or (figures and len(shared) >= 3)


def run(todo, matches, events, storylines):
    """Label `todo` (new headlines, oldest first) in place, matching against recent events."""
    recent = {}  # event id -> (word set of its first headline, time)
    for k, v in events.items():
        recent[k] = (set(v.get("words", [])), v["last"])
    for it in todo:
        w, t = words(it["title"]), when(it)
        tt = dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
        best = None
        for k, (ew, last) in recent.items():
            if not ew:
                continue
            lt = dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
            if abs((tt - lt).total_seconds()) <= 36 * 3600 and same_story(w, ew):
                best = k
                break
        if best is None:
            best = f"e{len(events) + 1}"
            events[best] = {"title": it["title"], "storyline": None, "first": t, "last": t, "words": sorted(w)}
            recent[best] = (w, t)
        else:
            e = events[best]
            e["first"], e["last"] = min(e["first"], t), max(e["last"], t)
            recent[best] = (recent[best][0], e["last"])
        matches[it["id"]] = {**label(it["title"]), "event": best, "by": "rules"}
    return len(todo)
