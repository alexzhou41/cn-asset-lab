# -*- coding: utf-8 -*-
"""Analyse collected congressional trades: post-trade returns vs SPY, rankings, aggregates."""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from us_prices import fetch_daily  # noqa: E402

RAW = os.path.join(HERE, "output", "policy_trades_raw.csv")
SNAPSHOT = os.path.join(HERE, "output", "policy_trades_snapshot.csv")
OUT_CSV = os.path.join(HERE, "output", "policy_trades.csv")
OUT_JSON = os.path.join(HERE, "output", "policy_analysis.json")
FIG = os.path.join(HERE, "..", "figures")
os.makedirs(FIG, exist_ok=True)

HORIZONS = [30, 90, 180]


def load():
    df = pd.read_csv(RAW)
    df = df.drop_duplicates(subset=["txId"]).copy()
    df["txDate"] = pd.to_datetime(df["txDate"], errors="coerce")
    df["pubDate"] = pd.to_datetime(df["pubDate"], errors="coerce", utc=True).dt.tz_localize(None)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["reportingGap"] = pd.to_numeric(df["reportingGap"], errors="coerce")
    df = df.dropna(subset=["txDate"]).sort_values("txDate").reset_index(drop=True)
    return df


def _series_arrays(s):
    s = s.reset_index()
    s["date"] = s["date"].dt.normalize()
    s = s.dropna().drop_duplicates(subset=["date"]).sort_values("date")
    return s["date"].to_numpy(dtype="datetime64[ns]"), s["close"].to_numpy(dtype=float)


def _fwd(dates, closes, d, h):
    """Return close[t+h]/close[t]-1 where t is the first trading day on/after d."""
    if dates is None or len(dates) == 0:
        return np.nan
    i = int(np.searchsorted(dates, d, side="left"))
    if i >= len(dates) or i + h >= len(dates):
        return np.nan
    p0, p1 = closes[i], closes[i + h]
    if not p0 or np.isnan(p0) or np.isnan(p1):
        return np.nan
    return p1 / p0 - 1.0


def attach_returns(df):
    """Attach forward returns from trade price and SPY benchmark."""
    df["ticker_clean"] = (df["ticker"].astype(str).str.split(":").str[0].str.strip()
                          .str.replace(r"[^A-Za-z0-9\.\-]", "", regex=True))
    df.loc[df["ticker_clean"].isin(["", "nan", "None"]), "ticker_clean"] = np.nan
    df.loc[df["ticker_clean"].str.len() > 6, "ticker_clean"] = np.nan

    spy = fetch_daily("SPY")
    spy_d, spy_c = _series_arrays(spy)

    tickers = sorted(set(df["ticker_clean"].dropna()))
    print("distinct tickers:", len(tickers), flush=True)

    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        try:
            return t, _series_arrays(fetch_daily(t))
        except Exception:
            return t, None

    series = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, (t, arr) in enumerate(ex.map(one, tickers), 1):
            if arr is not None:
                series[t] = arr
            if i % 100 == 0:
                print(f"  prices {i}/{len(tickers)}", flush=True)
    print("  price series ready:", len(series), flush=True)

    for h in HORIZONS:
        df[f"r{h}"] = np.nan
        df[f"spy{h}"] = np.nan

    dates = df["txDate"].dt.normalize().to_numpy(dtype="datetime64[ns]")
    tick_col = df["ticker_clean"].to_numpy()
    for idx in range(len(df)):
        d = dates[idx]
        t = tick_col[idx]
        if isinstance(t, str) and t in series:
            dd, cc = series[t]
            for h in HORIZONS:
                df.iat[idx, df.columns.get_loc(f"r{h}")] = _fwd(dd, cc, d, h)
        for h in HORIZONS:
            df.iat[idx, df.columns.get_loc(f"spy{h}")] = _fwd(spy_d, spy_c, d, h)
        if idx % 2000 == 0:
            print(f"  returns {idx}/{len(df)}", flush=True)
    return df


def summarise(df):
    df = df.copy()
    df["is_stock"] = df["txType"].isin(["buy", "sell"])
    df["dir_sign"] = np.where(df["txType"] == "buy", 1.0,
                              np.where(df["txType"] == "sell", -1.0, np.nan))
    df["sign"] = df["txType"].map({"buy": "买入", "sell": "卖出",
                                   "exchange": "互换", "receive": "接收", "other": "其他"})
    df = df.fillna({"sign": "其他"})

    out = {}
    out["n_trades"] = int(len(df))
    out["n_politicians"] = int(df["politicianId"].nunique())
    out["n_tickers"] = int(df["ticker_clean"].nunique())
    out["date_min"] = df["txDate"].min().strftime("%Y-%m-%d")
    out["date_max"] = df["txDate"].max().strftime("%Y-%m-%d")
    out["n_buy"] = int((df["txType"] == "buy").sum())
    out["n_sell"] = int((df["txType"] == "sell").sum())
    out["n_exchange"] = int((df["txType"] == "exchange").sum())
    out["total_value"] = float(df["value"].sum())
    out["avg_reporting_gap"] = float(df["reportingGap"].mean())
    out["median_reporting_gap"] = float(df["reportingGap"].median())

    # horizon stats on common stock trades with usable data
    st = df[df["is_stock"]].copy()
    stats = {}
    for h in HORIZONS:
        sub = st.dropna(subset=[f"r{h}", f"spy{h}"])
        b = sub[sub["txType"] == "buy"]
        s = sub[sub["txType"] == "sell"]
        stats[h] = {
            "n_total": int(len(sub)),
            "n_buy": int(len(b)),
            "n_sell": int(len(s)),
            "buy_ret": float(b[f"r{h}"].mean() * 100) if len(b) else None,
            "sell_ret": float(s[f"r{h}"].mean() * 100) if len(s) else None,
            "spy_ret": float(sub[f"spy{h}"].mean() * 100) if len(sub) else None,
            "buy_alpha": float((b[f"r{h}"] - b[f"spy{h}"]).mean() * 100) if len(b) else None,
            "sell_alpha": float((s[f"spy{h}"] - s[f"r{h}"]).mean() * 100) if len(s) else None,
            "buy_alpha_t": float((b[f"r{h}"] - b[f"spy{h}"]).mean() /
                                 ((b[f"r{h}"] - b[f"spy{h}"]).std() / np.sqrt(len(b))))
            if len(b) > 30 else None,
            "sell_alpha_t": float((s[f"spy{h}"] - s[f"r{h}"]).mean() /
                                  ((s[f"spy{h}"] - s[f"r{h}"]).std() / np.sqrt(len(s))))
            if len(s) > 30 else None,
        }
    out["horizon_stats"] = stats

    # monthly net buy value
    mo = df.copy()
    mo["m"] = mo["txDate"].dt.strftime("%Y-%m")
    g = mo.groupby("m")
    monthly = []
    for m, sub in g:
        buy = sub[sub["txType"] == "buy"]["value"].sum()
        sell = sub[sub["txType"] == "sell"]["value"].sum()
        monthly.append({"m": m, "n": int(len(sub)), "buy": float(buy),
                        "sell": float(sell), "net": float(buy - sell)})
    out["monthly"] = monthly

    # politicians
    pol = []
    for pid, sub in df.groupby("politicianId"):
        pol.append({
            "id": pid,
            "name": sub["name"].iloc[0],
            "party": sub["party"].iloc[0],
            "chamber": sub["chamber"].iloc[0],
            "state": sub["state"].iloc[0],
            "n": int(len(sub)),
            "nBuy": int((sub["txType"] == "buy").sum()),
            "nSell": int((sub["txType"] == "sell").sum()),
            "value": float(sub["value"].sum()),
            "net": float(sub[sub["txType"] == "buy"]["value"].sum()
                           - sub[sub["txType"] == "sell"]["value"].sum()),
            "topTicker": sub["ticker_clean"].mode().iloc[0] if sub["ticker_clean"].notna().any() else None,
        })
    pol.sort(key=lambda x: -x["value"])
    out["politicians"] = pol

    # sectors
    sec = []
    for s, sub in df.groupby("sector"):
        sec.append({
            "sector": s,
            "n": int(len(sub)),
            "nBuy": int((sub["txType"] == "buy").sum()),
            "nSell": int((sub["txType"] == "sell").sum()),
            "value": float(sub["value"].sum()),
            "net": float(sub[sub["txType"] == "buy"]["value"].sum()
                          - sub[sub["txType"] == "sell"]["value"].sum()),
        })
    sec.sort(key=lambda x: -abs(x["net"]))
    out["sectors"] = sec

    # tickers
    tk = []
    for t, sub in df.groupby("ticker_clean"):
        if not isinstance(t, str):
            continue
        b = sub[sub["txType"] == "buy"]["value"].sum()
        s = sub[sub["txType"] == "sell"]["value"].sum()
        r30 = sub["r30"].dropna()
        tk.append({
            "ticker": t,
            "name": sub["issuerName"].mode().iloc[0] if sub["issuerName"].notna().any() else t,
            "sector": sub["sector"].mode().iloc[0] if sub["sector"].notna().any() else None,
            "n": int(len(sub)),
            "nBuy": int((sub["txType"] == "buy").sum()),
            "nSell": int((sub["txType"] == "sell").sum()),
            "value": float(sub["value"].sum()),
            "net": float(b - s),
            "r30": float(r30.mean() * 100) if len(r30) else None,
        })
    tk.sort(key=lambda x: -x["value"])
    out["tickers"] = tk
    return out, df


def main():
    live = load()
    print("fresh rows:", len(live), "window:", live["txDate"].min(), "->",
          live["txDate"].max(), flush=True)

    # Accumulate: the snapshot keeps every row ever collected, fresh rows win.
    if os.path.exists(SNAPSHOT):
        snap = pd.read_csv(SNAPSHOT)
        merged = pd.concat([snap, live], ignore_index=True)
    else:
        merged = live
    merged = merged.drop_duplicates(subset=["txId"], keep="last")
    merged["txDate"] = pd.to_datetime(merged["txDate"], errors="coerce")
    merged["pubDate"] = pd.to_datetime(merged["pubDate"], errors="coerce", utc=True).dt.tz_localize(None)
    merged = merged.dropna(subset=["txDate"]).sort_values("txDate").reset_index(drop=True)
    merged.to_csv(SNAPSHOT, index=False, encoding="utf-8")
    print("snapshot rows:", len(merged), flush=True)

    df = attach_returns(merged)
    out, df = summarise(df)

    df["sign"] = df["txType"].map({"buy": "买入", "sell": "卖出", "exchange": "互换",
                                   "receive": "接收"}).fillna("其他")
    keep = ["txId", "politicianId", "name", "party", "chamber", "state", "ticker_clean",
            "issuerName", "sector", "txType", "sign", "txDate", "pubDate", "price", "value",
            "reportingGap", "owner", "comment", "r30", "r90", "spy30", "spy90"]
    df[keep].to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote", OUT_CSV, "and", OUT_JSON, flush=True)
    print(json.dumps({k: out[k] for k in ["n_trades", "n_politicians", "n_tickers",
                                          "date_min", "date_max", "n_buy", "n_sell",
                                          "total_value", "avg_reporting_gap"]},
                     ensure_ascii=False, indent=1), flush=True)
    print(json.dumps(out["horizon_stats"], ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
