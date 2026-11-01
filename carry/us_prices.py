# -*- coding: utf-8 -*-
"""Fetch US daily closes from Sina for congressional-trade tickers, with cache."""
import io
import json
import os
import re
import time

import pandas as pd
import requests

S = requests.Session()
S.trust_env = False
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                   "Referer": "https://finance.sina.com.cn/"})

# 缓存目录可用环境变量 US_PRICE_CACHE 覆盖。
# 自托管 runner 的 checkout 每次都会被 actions/checkout 清空，所以那里必须指向
# 一个跨运行持久的目录；否则每次运行都要重抓 700+ 个 ticker 的日线。
CACHE = os.environ.get("US_PRICE_CACHE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "us_stocks")
os.makedirs(CACHE, exist_ok=True)

KLINE = ("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20_/"
         "US_MinKService.getDailyK?symbol={sym}&_={ts}")


def clean(sym):
    return sym.replace(".", "-").replace("/", "-").replace("^", "_")


def fetch_daily(sym, force=False):
    """Return pandas Series indexed by datetime with close price."""
    path = os.path.join(CACHE, clean(sym) + ".csv")
    if os.path.exists(path) and not force:
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")["close"].astype(float)
    url = KLINE.format(sym=sym, ts=int(time.time() * 1000))
    r = S.get(url, timeout=30)
    txt = r.text
    m = re.search(r"var _\((\[.*\])\)", txt, re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
    except Exception:
        return None
    if not arr:
        return None
    df = pd.DataFrame(arr)[["d", "c"]].rename(columns={"d": "date", "c": "close"})
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna().sort_values("date").reset_index(drop=True)
    df.to_csv(path, index=False)
    return df.set_index("date")["close"].astype(float)


if __name__ == "__main__":
    tests = ["AAPL", "MSFT", "NVDA", "SPY", "TSLA", "T", "LRCX", "MPC", "PLD"]
    for t in tests:
        s = fetch_daily(t)
        if s is None:
            print(f"{t}: FAILED")
        else:
            print(f"{t}: n={len(s)} first={s.index[0].date()} last={s.index[-1].date()} "
                  f"lastclose={s.iloc[-1]:.2f}")
        time.sleep(1.2)
