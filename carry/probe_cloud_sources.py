# -*- coding: utf-8 -*-
"""Probe candidate data sources FROM a GitHub-hosted runner (datacenter IP).

Purpose: decide whether the cloud workflow can fetch congressional trades at all.
Prints one line per target: status | bytes | first bytes of body.
"""
import sys

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

TARGETS = [
    ("CT-trades-ssr", "https://www.capitoltrades.com/trades?pageSize=100&sortBy=-txDate"),
    ("CT-trades-plain", "https://www.capitoltrades.com/trades"),
    ("CT-api-v1", "https://api.capitoltrades.com/trades?pageSize=10"),
    ("CT-api-v2", "https://www.capitoltrades.com/api/trades?pageSize=10"),
    ("Quiver-home", "https://www.quiverquant.com/"),
    ("Sina-us-price", "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20_/"
                      "US_MinKService.getDailyK?symbol=SPY"),
    ("HSW-readme", "https://raw.githubusercontent.com/timothycarambat/house-stock-watcher-data/main/README.md"),
    ("HSW-data-dir", "https://api.github.com/repos/timothycarambat/house-stock-watcher-data/contents/data"),
    ("CT-rss", "https://www.capitoltrades.com/rss"),
]


def main():
    s = requests.Session()
    s.trust_env = False
    for name, url in TARGETS:
        try:
            r = s.get(url, headers=HEADERS, timeout=35)
            body = (r.text or "")[:120].replace("\n", " ").replace("\r", " ")
            print(f"{name:18s} | {r.status_code} | {len(r.content):8d} | {body}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"{name:18s} | ERROR | {type(exc).__name__}: {str(exc)[:120]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
