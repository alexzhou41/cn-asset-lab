# -*- coding: utf-8 -*-
"""One refresh cycle for the US policy-trading data.

Fetches only the freshest few pages of congressional trade disclosures, merges
them into the accumulated snapshot, recomputes post-trade returns, rewrites the
mini app payload and rebuilds the static site.

Kept deliberately small: the point is to stay current, not to re-crawl history.
"""
import json
import os
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "output", "policy_trades_raw.csv")
PAGES = 5          # 5 x 100 = 500 freshest disclosures per run
MAX_PAGES = 40     # hard cap when back-filling a gap after downtime
SLEEP = 6.0

S = requests.Session()
S.trust_env = False
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
})


def flight_payload(html):
    import re
    chunks = re.findall(r"self\.__next_f\.push\(\[1,(\"(?:[^\"\\]|\\.)*\")\]\)", html)
    return "".join(json.loads(c) for c in chunks)


def extract_rows(payload):
    key = '"data":['
    idx = payload.find(key)
    if idx < 0:
        return []
    start = idx + len(key) - 1
    depth, in_str, esc = 0, False, False
    for i in range(start, len(payload)):
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
                try:
                    return json.loads(payload[start:i + 1])
                except Exception:
                    return []
    return []


def flatten(rows):
    out = []
    for r in rows:
        issuer = r.get("issuer") or {}
        pol = r.get("politician") or {}
        out.append({
            "txId": r.get("_txId"), "politicianId": r.get("_politicianId"),
            "name": (pol.get("firstName", "") + " " + pol.get("lastName", "")).strip(),
            "party": pol.get("party"), "chamber": r.get("chamber"),
            "state": pol.get("_stateId"), "dob": pol.get("dob"),
            "ticker": issuer.get("issuerTicker"), "issuerName": issuer.get("issuerName"),
            "sector": issuer.get("sector"), "c2iq": issuer.get("c2iq"),
            "txType": r.get("txType"), "txTypeExtended": r.get("txTypeExtended"),
            "txDate": r.get("txDate"), "pubDate": r.get("pubDate"),
            "price": r.get("price"), "value": r.get("value"),
            "reportingGap": r.get("reportingGap"), "owner": r.get("owner"),
            "comment": r.get("comment"),
        })
    return out


def snapshot_newest_txdate():
    """Newest trade date already stored in the accumulated snapshot (None if empty)."""
    import pandas as pd
    path = os.path.join(HERE, "output", "policy_trades_snapshot.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, usecols=["txDate"])
        d = pd.to_datetime(df["txDate"], errors="coerce").max()
        return None if pd.isna(d) else d
    except Exception:
        return None


def fetch():
    """Pull the newest disclosures, then keep paging until the snapshot gap is closed."""
    import pandas as pd
    known = snapshot_newest_txdate()
    rows = []
    page = 0
    while page < MAX_PAGES:
        page += 1
        url = (f"https://www.capitoltrades.com/trades?page={page}&pageSize=100"
               f"&sortBy=-txDate")
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 429:
                # Vercel Security Checkpoint 对数据中心出口 IP 很敏感：退避后重试。
                for wait in (45, 90, 180):
                    print(f"  page {page}: HTTP 429, retry in {wait}s", flush=True)
                    time.sleep(wait)
                    r = S.get(url, timeout=60)
                    if r.status_code != 429:
                        break
            if r.status_code != 200:
                print(f"  page {page}: HTTP {r.status_code}", flush=True)
                break
            got = extract_rows(flight_payload(r.text))
            rows.extend(got)
            print(f"  page {page}: {len(got)} rows", flush=True)
            if not got:
                break
        except Exception as e:
            print(f"  page {page}: ERR {type(e).__name__}", flush=True)
            break
        # 断点续抓：若本轮抓到的最老一笔仍比快照里的最新一笔更新，说明中间漏了一段，
        # 继续往后翻页直到接上快照（电脑关机几天后靠这段补齐）。
        if known is not None and len(rows) >= PAGES * 100:
            oldest = min(x.get("txDate") for x in rows if x.get("txDate"))
            if oldest and pd.Timestamp(oldest) > known:
                print(f"  gap detected (fresh oldest {oldest} > snapshot "
                      f"{known.date()}); fetching more pages", flush=True)
                time.sleep(SLEEP)
                continue
            break
        if page >= PAGES and known is None:
            break
        time.sleep(SLEEP)
    if not rows:
        print("no rows fetched; keeping previous snapshot", flush=True)
        return False
    df = pd.DataFrame(flatten(rows))
    df.to_csv(RAW, index=False, encoding="utf-8")
    print(f"wrote {RAW} ({len(df)} fresh rows)", flush=True)
    return True


def run(script):
    print(f"--- {script} ---", flush=True)
    r = subprocess.run([sys.executable, "-X", "utf8", "-u", os.path.join(HERE, script)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (r.stdout or "").strip().splitlines()[-12:]
    for line in tail:
        print("   ", line, flush=True)
    if r.returncode != 0:
        print("   STDERR:", (r.stderr or "")[-1200:], flush=True)
        return False
    return True


def main():
    ok = fetch()
    if not ok:
        print("refresh skipped (network)", flush=True)
        return
    for s in ["policy_analysis.py", "cn_policy_export.py", "cn_site_build.py"]:
        if not run(s):
            print(f"step failed: {s}", flush=True)
            return
    print("REFRESH_OK", flush=True)


if __name__ == "__main__":
    main()
