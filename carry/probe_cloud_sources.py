# -*- coding: utf-8 -*-
"""Probe round 3 (cloud): find how Quiver's congress-trading page sources its data.

If the page ships its trades inside the Next.js RSC payload (like Capitol
Trades does), we can swap the scraper to Quiver and run entirely on a hosted
GitHub runner. This script reports the page's plumbing so we know which.
"""
import re
import sys

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

PAGE = "https://www.quiverquant.com/congresstrading"

API_RE = re.compile(r"""["'`]([^"'`\s]{0,120}(?:/api/|api\.quiver|/congress|congresstrading)[^"'`\s]{0,120})["'`]""", re.I)
SRC_RE = re.compile(r"""<script[^>]+src=["']([^"']+)["']""")


def main():
    s = requests.Session()
    s.trust_env = False
    r = s.get(PAGE, headers=HEADERS, timeout=60)
    html = r.text
    print(f"status={r.status_code} bytes={len(html)}", flush=True)

    print(f"has __NEXT_DATA__: {'__NEXT_DATA__' in html}", flush=True)
    print(f"has self.__next_f.push: {html.count('self.__next_f.push')}", flush=True)
    print(f"count 'congresstrading': {html.lower().count('congresstrading')}", flush=True)
    print(f"count 'disclosedDate': {html.count('disclosedDate')}", flush=True)
    print(f"count 'txDate': {html.count('txDate')}", flush=True)
    print(f"count 'representative': {html.count('representative')}", flush=True)

    print("\n--- api-ish strings (unique, first 40) ---", flush=True)
    seen = []
    for m in API_RE.findall(html):
        if m not in seen:
            seen.append(m)
    for u in seen[:40]:
        print(f"  {u}", flush=True)

    print("\n--- script srcs (first 25) ---", flush=True)
    for u in SRC_RE.findall(html)[:25]:
        print(f"  {u}", flush=True)

    print("\n--- snippet around 'disclosedDate'/'txDate' (if any) ---", flush=True)
    for kw in ("disclosedDate", "txDate", "\"politician\"", "amount"):
        i = html.find(kw)
        if i >= 0:
            print(f"[{kw}] ...{html[max(0, i - 300):i + 400]}...", flush=True)
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
