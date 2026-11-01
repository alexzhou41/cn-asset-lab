# -*- coding: utf-8 -*-
"""Probe candidate congressional-trade data sources FROM a GitHub-hosted runner.

Round 2: Capitol Trades is 429 on every path from datacenter IPs, but Quiver
Quant answered 200 on its homepage. This round tries the specific Quiver
congressional-trading endpoints and prints enough of the body to tell JSON
from HTML.
"""
import sys
import time

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.quiverquant.com/congresstrading",
}

TARGETS = [
    ("QQ-congress-page", "https://www.quiverquant.com/congresstrading"),
    ("QQ-hist-root", "https://www.quiverquant.com/api/historical/congresstrading/"),
    ("QQ-hist-slash", "https://www.quiverquant.com/api/historical/congresstrading"),
    ("QQ-hist-range", "https://www.quiverquant.com/api/historical/congresstrading/"
                      "?start_date=2026-10-01&end_date=2026-11-01"),
    ("QQ-hist-ticker", "https://www.quiverquant.com/api/historical/congresstrading/NVDA"),
    ("QQ-hist-congress", "https://www.quiverquant.com/api/historical/congresstrading/"
                         "?congress=house&ticker=NVDA"),
    ("QQ-hist-nodate", "https://www.quiverquant.com/api/historical/congresstrading?limit=5"),
    ("QQ-api-root", "https://api.quiverquant.com/api/historical/congresstrading"),
    ("QQ-hist-all", "https://www.quiverquant.com/api/historical/congresstrading/all"),
    ("CT-json-ext", "https://www.capitoltrades.com/trades.json"),
]


def main():
    s = requests.Session()
    s.trust_env = False
    for name, url in TARGETS:
        try:
            r = s.get(url, headers=HEADERS, timeout=40)
            body = (r.text or "")[:400].replace("\n", " ").replace("\r", " ")
            print(f"### {name} | {r.status_code} | {len(r.content)} | {r.headers.get('content-type')}",
                  flush=True)
            print(f"    {body}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"### {name} | ERROR | {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        time.sleep(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
