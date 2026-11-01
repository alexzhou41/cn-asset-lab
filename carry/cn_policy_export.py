# -*- coding: utf-8 -*-
"""Merge the congressional-trading analysis into the miniapp client data.json."""
import json
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYSIS = os.path.join(HERE, "output", "policy_analysis.json")
TRADES = os.path.join(HERE, "output", "policy_trades.csv")
DATA_JSON = os.path.join(HERE, "..", "miniapps", "china-all-weather",
                         "miniapp", "client", "data.json")

# Capitol Trades stores the midpoint of the disclosed bracket; rebuild the bracket.
RANGES = [(1001, 15000), (15001, 50000), (50001, 100000), (100001, 250000),
          (250001, 500000), (500001, 1000000), (1000001, 5000000),
          (5000001, 10000000), (10000001, 50000000)]


def bracket(mid):
    if mid is None or pd.isna(mid):
        return None, None
    m = float(mid)
    for lo, hi in RANGES:
        if abs(m - (lo + hi) / 2.0) < 1.0:
            return lo, hi
    if m >= 50000000:
        return 50000001, None
    return None, None


def r2(x, nd=2):
    if x is None or pd.isna(x):
        return None
    return round(float(x), nd)


def main():
    with open(ANALYSIS, encoding="utf-8") as f:
        a = json.load(f)
    df = pd.read_csv(TRADES)

    df["txDate"] = pd.to_datetime(df["txDate"], errors="coerce")
    df["pubDate"] = pd.to_datetime(df["pubDate"], errors="coerce")
    df = df.dropna(subset=["txDate"]).sort_values("txDate", ascending=False)

    rows = []
    for _, t in df.iterrows():
        lo, hi = bracket(t.get("value"))
        rows.append({
            "id": int(t["txId"]) if pd.notna(t.get("txId")) else None,
            "d": t["txDate"].strftime("%Y-%m-%d"),
            "pd": (t["pubDate"].strftime("%Y-%m-%d") if pd.notna(t.get("pubDate")) else None),
            "n": t.get("name"),
            "pa": t.get("party"),
            "ch": t.get("chamber"),
            "t": (t.get("ticker_clean") if isinstance(t.get("ticker_clean"), str) else None),
            "co": (t.get("issuerName") if isinstance(t.get("issuerName"), str) else None),
            "s": (t.get("sector") if isinstance(t.get("sector"), str) else None),
            "ty": t.get("txType"),
            "v": r2(t.get("value"), 0),
            "lo": lo, "hi": hi,
            "pr": r2(t.get("price"), 2),
            "g": (int(t["reportingGap"]) if pd.notna(t.get("reportingGap")) else None),
            "r30": r2(t.get("r30") * 100, 1) if pd.notna(t.get("r30")) else None,
            "r90": r2(t.get("r90") * 100, 1) if pd.notna(t.get("r90")) else None,
        })

    MAX_ROWS = 2500
    if len(rows) > MAX_ROWS:
        rows = rows[:MAX_ROWS]

    buy = sum(r["v"] for r in rows if r["ty"] == "buy" and r["v"])
    sell = sum(r["v"] for r in rows if r["ty"] == "sell" and r["v"])
    net = float(buy - sell)
    n_house = sum(1 for r in rows if r["ch"] == "house")
    n_senate = sum(1 for r in rows if r["ch"] == "senate")
    n_people = len({r["n"] for r in rows if r["n"]})
    n_tick = len({r["t"] for r in rows if r["t"]})
    gaps = sorted(r["g"] for r in rows if r["g"] is not None)
    med_gap = (gaps[len(gaps) // 2] if len(gaps) % 2
               else (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]) / 2) if gaps else None
    ds = sorted(r["d"] for r in rows if r["d"])
    window = f"{ds[0]} → {ds[-1]}" if ds else ""

    stats = {}
    for h in ("30", "90", "180"):
        s = a["horizon_stats"].get(h, {})
        stats[h] = {
            "nBuy": s.get("n_buy"), "nSell": s.get("n_sell"), "nTotal": s.get("n_total"),
            "buyRet": r2(s.get("buy_ret")), "sellRet": r2(s.get("sell_ret")),
            "spyRet": r2(s.get("spy_ret")),
            "buyAlpha": r2(s.get("buy_alpha")), "sellAlpha": r2(s.get("sell_alpha")),
            "buyT": r2(s.get("buy_alpha_t")), "sellT": r2(s.get("sell_alpha_t")),
        }

    # Aggregates are rebuilt from the shipped rows so the table, the rankings
    # and the KPI row can never disagree.
    people, sectors, monthly = {}, {}, {}
    for r in rows:
        v = r["v"] or 0
        key = (r["n"], r["pa"], r["ch"])
        p = people.setdefault(key, {"name": r["n"], "party": r["pa"], "chamber": r["ch"],
                                    "n": 0, "nBuy": 0, "nSell": 0, "value": 0.0, "net": 0.0,
                                    "tick": {}})
        p["n"] += 1
        p["value"] += v
        if r["ty"] == "buy":
            p["nBuy"] += 1
            p["net"] += v
        elif r["ty"] == "sell":
            p["nSell"] += 1
            p["net"] -= v
        if r["t"]:
            p["tick"][r["t"]] = p["tick"].get(r["t"], 0) + 1
        s = sectors.setdefault(r["s"], {"sector": r["s"], "n": 0, "nBuy": 0, "nSell": 0,
                                         "value": 0.0, "net": 0.0})
        s["n"] += 1
        s["value"] += v
        if r["ty"] == "buy":
            s["nBuy"] += 1
            s["net"] += v
        elif r["ty"] == "sell":
            s["nSell"] += 1
            s["net"] -= v
        m = monthly.setdefault(r["d"][:7], {"m": r["d"][:7], "n": 0, "buy": 0.0,
                                            "sell": 0.0, "net": 0.0})
        m["n"] += 1
        if r["ty"] == "buy":
            m["buy"] += v
            m["net"] += v
        elif r["ty"] == "sell":
            m["sell"] += v
            m["net"] -= v

    pol_list = []
    for (name, party, chamber), p in people.items():
        top = max(p["tick"].items(), key=lambda kv: kv[1])[0] if p["tick"] else None
        pol_list.append({"id": name, "name": name, "party": party, "chamber": chamber,
                         "state": None, "n": p["n"], "nBuy": p["nBuy"], "nSell": p["nSell"],
                         "value": p["value"], "net": p["net"], "topTicker": top})
    pol_list.sort(key=lambda x: -x["value"])
    sec_list = sorted(sectors.values(), key=lambda x: -abs(x["net"]))
    mon_list = sorted(monthly.values(), key=lambda x: x["m"])

    meta = {
        "source": "Capitol Trades",
        "sourceUrl": "https://www.capitoltrades.com/trades",
        "fetched": "2026-11-01",
        "window": window,
        "nTrades": len(rows),
        "nPoliticians": n_people,
        "nHouse": n_house,
        "nSenate": n_senate,
        "nTickers": n_tick,
        "nBuy": sum(1 for r in rows if r["ty"] == "buy"),
        "nSell": sum(1 for r in rows if r["ty"] == "sell"),
        "netValue": net,
        "totalValue": buy + sell,
        "avgGap": (sum(gaps) / len(gaps)) if gaps else 0,
        "medGap": med_gap or 0,
        "bench": "SPY",
        "valueNote": "金额是国会申报的区间（例如「$1,001 – $15,000」），不是精确成交额；"
                     "合计值按区间中值估算。",
        "intro": "数据源为 Capitol Trades（底层是美国国会 PTR 财务披露）。"
                 "你给的 Quiver Quant 在本机网络连不上（连接超时），所以换成同口径的公开聚合源："
                 "两者都是把同一批国会财务披露整理成可查询的交易表。左侧可以按议员、标的、"
                 "方向、党派、板块筛选，下面两张图回答「他们买完卖完之后，股票到底怎么走了」。",
    }

    notes = [
        "<b>这是申报，不是内幕交易。</b>国会议员按 STOCK Act 要在 45 天内公开交易区间，"
        "所以这张表是事后公开的记录，不是任何提前获知内幕的证据。",
        "<b>金额是区间不是精确值。</b>国会只申报区间（例如 $1,001–$15,000），"
        "这里显示的区间就是从申报值还原出来的，合计按中值估算。因此「议员交易额 Top 15」"
        "衡量的是申报规模的下界，不是真实交易额。",
        "<b>很多交易不是议员本人的。</b>常见申报人是配偶、信托、子女，"
        "所以这张表更准确的读法是「这个议员办公室披露了哪些家庭资产变动」。",
        "<b>滞后是重要的。</b>成交日到公开日平均滞后 " + f'{a["avg_reporting_gap"]:.0f}' +
        " 天（中位 " + f'{a["median_reporting_gap"]:.0f}' + " 天）。"
        "如果市场在公开当天就反应，那么「成交后收益」高估了他们选股的能力；"
        "真正的信息含量要看公开日之后的价格。",
        "<b>事后收益是选择后的。</b>只有成交价与后续行情的股票才进入收益统计，"
        "债券、地方债、基金持仓等非股票资产被排除在收益统计之外，但保留在查询表里。",
        "<b>不同持有期的样本不同。</b>30 日用近月交易，180 日只能用到 6 个月以前的交易，"
        "所以每一行的样本数都标在表里，不要跨行直接比较样本量。",
        "<b>行情口径。</b>个股日线取自新浪财经美股接口（未做股息复权），基准为 SPY，"
        "超额收益＝个股涨跌幅减去同期 SPY 涨跌幅。",
        "<b>这一页只是信息查询，不构成投资建议。</b>国会议员的持仓规模相对于个人资产组合"
        "通常极小，其披露噪声远大于信号。",
    ]

    policy = {
        "meta": meta,
        "trades": rows,
        "politicians": pol_list[:80],
        "sectors": sec_list,
        "tickers": a["tickers"][:80],
        "monthly": mon_list,
        "stats": stats,
        "notes": notes,
    }

    with open(DATA_JSON, encoding="utf-8") as f:
        data = json.load(f)
    data["policy"] = policy
    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(DATA_JSON)
    print(f"wrote {DATA_JSON} ({size/1024:.0f} KB) trades={len(rows)} "
          f"politicians={len(policy['politicians'])} tickers={len(policy['tickers'])}")
    print("meta:", json.dumps(meta, ensure_ascii=False)[:400])
    print("stats:", json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
