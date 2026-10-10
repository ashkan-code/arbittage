#!/usr/bin/env python3
"""Capture raw Bitunix SPOT public API responses. Standard library only.

Saves RAW response bodies to samples/, plus one samples/capture_report.json
with status, headers and timing for every attempt. It does NOT parse or
interpret anything, so we learn the real field names from the real data.

Public endpoints only, GET only, no API keys, no orders.
Run:  python capture_samples.py
"""
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
TIMEOUT = 15          # seconds per request
PAUSE = 0.35          # seconds between calls, to stay polite on rate limits
DEPTH_RUNS = 20

# Base URLs and paths are best guesses; every attempt is recorded either way.
BASES = ["https://openapi.bitunix.com"]   # api. gives 403, spot. does not resolve
PAIR_PATHS = [
    "/api/spot/v1/market/trading_pair/list",
    "/api/spot/v1/market/trading_pairs",
    "/api/spot/v1/market/trading_pair",
    "/api/spot/v1/common/trading_pair/list",
    "/api/spot/v1/market/symbols",
]
DEPTH_PATHS = ["/api/spot/v1/market/depth"]
DEPTH_QUERIES = [          # {sym} is filled in; precision/limit are guesses
    "symbol={sym}&precision=0",
    "symbol={sym}",
    "symbol={sym}&limit=20",
    "symbol={sym}&precision=0&limit=20",
]
PRICE_PATHS = ["/api/spot/v1/market/last_price"]
PRICE_QUERIES = ["symbol={sym}"]
TICKER_PATHS = [                      # looking for ONE call that returns all pairs
    "/api/spot/v1/market/ticker",
    "/api/spot/v1/market/tickers",
    "/api/spot/v1/market/ticker/list",
    "/api/spot/v1/market/book_ticker",
    "/api/spot/v1/market/24h_ticker",
]
ALT_CANDIDATES = ["DOGEUSDT", "ADAUSDT", "XRPUSDT", "TRXUSDT"]

attempts = []   # everything we try, in order
raw_files = []  # names of raw body files written


def safe(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


def get(url):
    """One GET. Returns dict with status, headers, body bytes, elapsed ms, error."""
    req = urllib.request.Request(url, headers={"User-Agent": "capture-samples/1.0",
                                               "Accept": "application/json"})
    t0 = time.perf_counter()
    res = {"url": url, "status": None, "headers": {}, "error": None,
           "request_unix_ms": int(time.time() * 1000)}
    body = b""
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            res["status"] = r.status
            res["headers"] = dict(r.headers.items())
            body = r.read()
    except urllib.error.HTTPError as e:      # 4xx/5xx still have a useful body
        res["status"] = e.code
        res["headers"] = dict(e.headers.items()) if e.headers else {}
        try:
            body = e.read()
        except Exception:
            pass
        res["error"] = f"HTTPError {e.code} {e.reason}"
    except Exception as e:                   # DNS, timeout, TLS, proxy...
        res["error"] = f"{type(e).__name__}: {e}"
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    res["body_bytes"] = len(body)
    res["body"] = body
    return res


def record(label, res, save=True):
    """Log the attempt and write the raw body to its own file."""
    entry = {k: v for k, v in res.items() if k != "body"}
    entry["label"] = label
    entry["raw_file"] = None
    if save and res["body"]:
        name = f"{len(attempts):02d}_{safe(label)}.raw"
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(res["body"])
        entry["raw_file"] = name
        raw_files.append(name)
    attempts.append(entry)
    ok = res["status"] == 200
    print(f"[{'OK ' if ok else 'ERR'}] {res['status']} {res['elapsed_ms']:>7} ms  {res['url']}"
          + (f"  <- {res['error']}" if res["error"] else ""))
    time.sleep(PAUSE)
    return entry


def first_ok(label, urls):
    """Try URLs in order, keep trying after failures, record all. Return first 200."""
    winner = None
    for u in urls:
        res = get(u)
        entry = record(label, res)
        if res["status"] == 200 and winner is None:
            winner = (u, res, entry)
    return winner


def keep_copy(winner, canonical):
    """Copy the first successful raw body to a stable, easy-to-find name."""
    if winner and winner[2]["raw_file"]:
        src = os.path.join(OUT, winner[2]["raw_file"])
        with open(src, "rb") as f, open(os.path.join(OUT, canonical), "wb") as g:
            g.write(f.read())
        raw_files.append(canonical)


def find_alt_symbol(pair_body):
    """Pick a small altcoin pair from the raw text with a regex only.
    No field names are assumed: we just look for quoted strings ending in USDT."""
    text = pair_body.decode("utf-8", "replace")
    syms = sorted(set(re.findall(r'"([A-Z0-9]{2,15}USDT)"', text)))
    for c in ALT_CANDIDATES:
        if c in syms:
            return c
    # Otherwise the last one alphabetically, which is usually obscure
    return syms[-1] if syms else None


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


def stats(vals):
    s = sorted(vals)
    if not s:
        return None
    return {"n": len(s), "median_ms": round(statistics.median(s), 1),
            "p90_ms": pct(s, 0.9), "min_ms": s[0], "max_ms": s[-1]}


def connection_test(url):
    """First call on a new connection (DNS+TCP+TLS handshake) vs 20 calls that
    reuse the same open connection. Stdlib http.client keeps the socket open."""
    import http.client
    import urllib.parse
    u = urllib.parse.urlsplit(url)
    path = u.path + ("?" + u.query if u.query else "")
    out = {"url": url, "first_call_ms": None, "reused_ms": [], "fresh_each_time_ms": [], "errors": []}
    print("\nKeep-alive test")
    try:
        c = http.client.HTTPSConnection(u.netloc, timeout=TIMEOUT)
        for i in range(DEPTH_RUNS + 1):
            t0 = time.perf_counter()
            c.request("GET", path, headers={"User-Agent": "capture-samples/1.0",
                                            "Connection": "keep-alive"})
            r = c.getresponse()
            r.read()
            ms = round((time.perf_counter() - t0) * 1000, 1)
            if r.status != 200:
                out["errors"].append(f"call {i}: HTTP {r.status}")
            elif i == 0:
                out["first_call_ms"] = ms
            else:
                out["reused_ms"].append(ms)
            time.sleep(PAUSE)
        c.close()
    except Exception as e:
        out["errors"].append(f"{type(e).__name__}: {e}")
    for _ in range(5):                       # a few brand-new connections for comparison
        res = get(url)
        if res["status"] == 200:
            out["fresh_each_time_ms"].append(res["elapsed_ms"])
        time.sleep(PAUSE)
    out["reused_stats"] = stats(out["reused_ms"])
    out["fresh_stats"] = stats(out["fresh_each_time_ms"])
    print(f"  first call (handshake): {out['first_call_ms']} ms")
    print(f"  next {DEPTH_RUNS} reused:        {out['reused_stats']}")
    print(f"  5 fresh connections:    {out['fresh_stats']}")
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    started = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"Capturing to {OUT}\n")

    # 1) Trading pair list: try every base x path
    urls = [b + p for p in PAIR_PATHS for b in BASES]
    pairs = first_ok("pairs", urls)
    keep_copy(pairs, "trading_pair_list.json")
    base = pairs[0].split("/api/")[0] if pairs else BASES[0]
    alt = find_alt_symbol(pairs[1]["body"]) if pairs else None
    if pairs:
        print(f"\nWorking base URL: {base}   altcoin picked: {alt}\n")
    else:
        print("\nNo pair-list URL worked. Will still try depth/price on the default base.\n")
        alt = ALT_CANDIDATES[0]

    # 2) Depth for BTCUSDT and the altcoin, trying query variants and bases
    depth_winner = {}
    for sym in ["BTCUSDT", alt]:
        urls = []
        for b in ([base] + [x for x in BASES if x != base]):
            for p in DEPTH_PATHS:
                for q in DEPTH_QUERIES:
                    urls.append(f"{b}{p}?{q.format(sym=sym)}")
        w = first_ok(f"depth_{sym}", urls)
        depth_winner[sym] = w
        keep_copy(w, f"depth_{sym}.json")

    # 3) Last price, BTCUSDT and the altcoin
    for sym in ["BTCUSDT", alt]:
        urls = [f"{b}{p}?{q.format(sym=sym)}"
                for b in ([base] + [x for x in BASES if x != base])
                for p in PRICE_PATHS for q in PRICE_QUERIES]
        w = first_ok(f"last_price_{sym}", urls)
        keep_copy(w, f"last_price_{sym}.json")

    # 3b) Bulk ticker / best bid-ask for all pairs: try with and without a symbol
    for p in TICKER_PATHS:
        for q in ["", "?symbol=BTCUSDT"]:
            res = get(base + p + q)
            e = record("ticker", res)
            if res["status"] == 200:
                keep_copy((None, res, e), "ticker_" + safe(p.split("/market/")[1] + q) + ".json")

    # 4) Latency: 20 sequential depth calls on the URL that worked
    latency = {"url": None, "runs": DEPTH_RUNS, "samples_ms": [], "failures": 0}
    w = depth_winner.get("BTCUSDT")
    if w:
        latency["url"] = w[0]
        print(f"\nLatency test, {DEPTH_RUNS} calls: {w[0]}")
        for _ in range(DEPTH_RUNS):
            res = get(w[0])
            if res["status"] == 200:
                latency["samples_ms"].append(res["elapsed_ms"])
            else:
                latency["failures"] += 1
            time.sleep(PAUSE)
        s = sorted(latency["samples_ms"])
        if s:
            latency["avg_ms"] = round(statistics.mean(s), 1)
            latency["median_ms"] = round(statistics.median(s), 1)
            latency["p90_ms"] = s[min(len(s) - 1, int(len(s) * 0.9))]
            latency["min_ms"], latency["max_ms"] = s[0], s[-1]
            print(f"  avg {latency['avg_ms']} ms, median {latency['median_ms']} ms, "
                  f"p90 {latency['p90_ms']} ms, failures {latency['failures']}")
    else:
        print("\nSkipping latency test: no depth URL returned 200.")

    # 4b) Fresh connection each time vs one reused (keep-alive) connection
    keepalive = None
    if w:
        keepalive = connection_test(w[0])

    # 5) One report with everything. Raw bodies are also inlined (truncated)
    #    so you can paste or send just this single file.
    inline = {}
    for name in sorted(set(raw_files)):
        try:
            with open(os.path.join(OUT, name), "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        inline[name] = {"total_chars": len(text), "head": text[:6000]}
        if name == "trading_pair_list.json":
            inline[name]["head"] = text[:20000]   # more pairs, so field names are clear
    report = {
        "started": started,
        "working_base_url": base if pairs else None,
        "altcoin_symbol": alt,
        "attempts": attempts,
        "latency_depth_btcusdt": latency,
        "keepalive_test": keepalive,
        "raw_bodies_inline": inline,
    }
    path = os.path.join(OUT, "capture_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    ok = sum(1 for a in attempts if a["status"] == 200)
    print(f"\nDone. {ok}/{len(attempts)} attempts returned HTTP 200.")
    print(f"Report: {path}")
    print("Send me samples/capture_report.json (and trading_pair_list.json if it exists).")


if __name__ == "__main__":
    main()
