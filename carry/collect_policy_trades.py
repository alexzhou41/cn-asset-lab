# -*- coding: utf-8 -*-
"""Collector: pull congressional trade rows from Capitol Trades SSR flight payload.

Saves incremental CSV so the run is resumable.
"""
import json
import os
import re
import sys
import time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(HERE, "output", "policy_trades_raw.csv")
CACHE_DIR = os.path.join(HERE, "output", "ct_pages_txdate")
os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

S = requests.Session()
S.trust_env = False
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                   "Accept-Language": "en-US,en;q=0.9",
                   "Upgrade-Insecure-Requests": "1"})

CUTOFF = "2024-06-30"      # keep trades from this txDate onward
MAX_PAGES = 150
PAGE_SIZE = 100
SLEEP = 8.5
SORT = "-txDate"          # order by trade date, newest first


def flight_payload(html):
    chunks = re.findall(r"self\.__next_f\.push\(\[1,(\"(?:[^\"\\]|\\.)*\")\]\)", html)
    return "".join(json.loads(c) for c in chunks)


def extract_rows(payload):
    """Pull the trades array out of the flight payload (balanced bracket scan)."""
    key = '"data":['
    idx = payload.find(key)
    if idx < 0:
        return []
    start = idx + len(key) - 1
    depth = 0
    in_str = False
    esc = False
    for i in range(start, min(len(payload), start + 4_000_000)):
        ch = payload[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                blob = payload[start:i + 1]
                try:
                    return json.loads(blob)
                except Exception:
                    return []
    return []


def flatten(rows):
    out = []
    for r in rows:
        issuer = r.get("issuer") or {}
        pol = r.get("politician") or {}
        out.append({
            "txId": r.get("_txId"),
            "politicianId": r.get("_politicianId"),
            "name": (pol.get("firstName", "") + " " + pol.get("lastName", "")).strip(),
            "party": pol.get("party"),
            "chamber": r.get("chamber"),
            "state": pol.get("_stateId"),
            "dob": pol.get("dob"),
            "ticker": issuer.get("issuerTicker"),
            "issuerName": issuer.get("issuerName"),
            "sector": issuer.get("sector"),
            "c2iq": issuer.get("c2iq"),
            "txType": r.get("txType"),
            "txTypeExtended": r.get("txTypeExtended"),
            "txDate": r.get("txDate"),
            "pubDate": r.get("pubDate"),
            "price": r.get("price"),
            "value": r.get("value"),
            "reportingGap": r.get("reportingGap"),
            "owner": r.get("owner"),
            "comment": r.get("comment"),
        })
    return out


def fetch_page(page):
    cache = os.path.join(CACHE_DIR, f"p{page:04d}.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    url = (f"https://www.capitoltrades.com/trades?page={page}&pageSize={PAGE_SIZE}"
           f"&sortBy={SORT}")
    for attempt in range(4):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 200:
                rows = extract_rows(flight_payload(r.text))
                if rows:
                    with open(cache, "w", encoding="utf-8") as f:
                        json.dump(rows, f, ensure_ascii=False)
                    return rows
                print(f"  page {page}: 200 but 0 rows (attempt {attempt})", flush=True)
            else:
                print(f"  page {page}: HTTP {r.status_code} (attempt {attempt})", flush=True)
        except Exception as e:
            print(f"  page {page}: ERR {type(e).__name__} (attempt {attempt})", flush=True)
        time.sleep(20 + attempt * 15)
    return []


def main():
    all_rows = []
    for page in range(1, MAX_PAGES + 1):
        rows = fetch_page(page)
        if not rows:
            print(f"page {page}: empty -> stop", flush=True)
            break
        all_rows.extend(rows)
        txdates = [x for x in (rr.get("txDate") for rr in rows) if x]
        oldest = min(txdates) if txdates else "?"
        newest = max(txdates) if txdates else "?"
        print(f"page {page}: {len(rows)} rows  txDate {newest} -> {oldest}  "
              f"total={len(all_rows)}", flush=True)
        df = pd.DataFrame(flatten(all_rows))
        df.to_csv(OUT_CSV, index=False, encoding="utf-8")
        if oldest and oldest < CUTOFF:
            print(f"reached cutoff {CUTOFF} at page {page}", flush=True)
            break
        time.sleep(SLEEP)
    print("DONE rows:", len(all_rows), flush=True)


if __name__ == "__main__":
    main()
