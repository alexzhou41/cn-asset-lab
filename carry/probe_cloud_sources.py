# -*- coding: utf-8 -*-
"""Probe round 4 (cloud): can any public proxy fetch Capitol Trades from a
datacenter IP? Also tries a few Quiver data endpoints.

If one works, the whole pipeline can run on a GitHub-hosted runner.
"""
import sys
import time
from urllib.parse import quote

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

CT = "https://www.capitoltrades.com/trades?pageSize=100&sortBy=-txDate"
Q = quote(CT, safe="")

TARGETS = [
    ("jina-rd", f"https://r.jina.ai/{CT}"),
    ("allorigins", f"https://api.allorigins.win/raw?url={Q}"),
    ("corsproxy", f"https://corsproxy.io/?{Q}"),
    ("codetabs", f"https://api.codetabs.com/v1/proxy?quest={Q}"),
    ("whateverorigin", f"https://www.whateverorigin.org/get?url={Q}"),
    ("thingproxy", f"https://thingproxy.freeboard.io/fetch/{CT}"),
    ("QQ-django-1", "https://www.quiverquant.com/api/congresstrading/"),
    ("QQ-django-2", "https://www.quiverquant.com/congresstrading/data/"),
    ("QQ-django-3", "https://www.quiverquant.com/api/trades/"),
    ("QQ-api-root", "https://api.quiverquant.com/"),
]


def main():
    s = requests.Session()
    s.trust_env = False
    for name, url in TARGETS:
        try:
            r = s.get(url, headers=HEADERS, timeout=45)
            body = (r.text or "")[:200].replace("\n", " ").replace("\r", " ")
            print(f"### {name} | {r.status_code} | {len(r.content)} | {body}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"### {name} | ERROR | {type(exc).__name__}: {str(exc)[:140]}", flush=True)
        time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
