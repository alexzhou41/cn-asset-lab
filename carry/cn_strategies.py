# -*- coding: utf-8 -*-
"""China A-share strategy lab: asset-class metrics + correlation + strategy backtests.

Everything is China-A-share implementable, monthly, dividend-adjusted, and net of
ETF trading costs.

Method
------
1. ASSET PANEL   one monthly total-return series per instrument.
                 Equity legs = INDEX PRICE + EXPLICIT DIVIDEND YIELD
                 (Sina ETF closes are not dividend-consistent across funds).
                 Bond / commodity legs = ETF price series.
                 NO CASH LEG (money-market ETFs price at 99.99-100.05 and pay
                 out through daily share accretion, so their price series is
                 unusable; per the brief, cash is excluded entirely).
2. ASSET CLASS   per-asset metrics over 1Y/3Y/5Y/10Y/YTD/full history
                 (each asset on its own history) - Return / Risk / Drawdown.
3. CORRELATION   pairwise-complete, minimum 24 overlapping months.
4. STRATEGIES    classic allocation recipes + two Markowitz constructions:
                   - Equal Risk Contribution (ERC, mu-free)
                   - Max-Sharpe MVO on BL posterior mu
                   - All-Weather MVO: correlation screen -> sleeve structure
                     (equity / bond / commodity) -> max-Sharpe subject to
                     sleeve risk-contribution bands
5. COSTS        30 bps round trip per traded notional at each rebalance
                (user-specified); sensitivity table covers 0 / 10 / 30 / 50 bps.
"""
import os
import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
OUT = os.path.join(HERE, 'output')
FIG = os.path.join(HERE, 'figures')
os.makedirs(OUT, exist_ok=True)
os.makedirs(FIG, exist_ok=True)

RF = 0.0165
COST_BPS = 0.0030          # round trip, as specified by the user
PERIODS = [('1Y', 12), ('3Y', 36), ('5Y', 60), ('10Y', 120)]
MIN_OVERLAP = 24

# ---------------------------------------------------------------- universe
# kind: 'index' -> index price + dividend yield; 'etf' -> ETF close price
UNIVERSE = [
    # key,          label,              cn,          ticker,     kind,  div,  class
    ('CSI300',   'CSI 300 Index',      '沪深300',    'sh000300', 'index', 0.025, 'Equity'),
    ('DIVIDEND', 'SSE Dividend Index', '上证红利',    'sh000015', 'index', 0.045, 'Equity'),
    ('CHINEXT',  'ChiNext Index',      '创业板指',    'sz399006', 'index', 0.005, 'Equity'),
    ('CSI500',   'CSI 500 Index',      '中证500',    'sh000905', 'index', 0.015, 'Equity'),
    ('STAR50',   'STAR 50 Index',      '科创50',      'sh000688', 'index', 0.003, 'Equity'),
    ('TREASURY', 'Treasury Bond ETF',  '国债ETF',     'sh511010', 'etf',   0.000, 'Bond'),
    ('SHORTDUR', 'Short-Duration ETF', '短融ETF',     'sh511360', 'etf',   0.000, 'Bond'),
    ('CONVERT',  'Convertible Bond ETF', '可转债ETF', 'sh511380', 'etf',   0.000, 'Bond'),
    ('GOLD',     'Gold ETF',           '黄金ETF',     'sh518880', 'etf',   0.000, 'Commodity'),
    ('OIL',      'Crude Oil QDII',     '原油QDII',    'sh501018', 'etf',   0.000, 'Commodity'),
    ('METALS',   'Non-ferrous Metals ETF', '有色ETF', 'sz159980', 'etf',   0.000, 'Commodity'),
    ('SOYBEAN',  'Soybean Meal ETF',   '豆粕ETF',     'sz159985', 'etf',   0.000, 'Commodity'),
]
KEY = {u[0]: u for u in UNIVERSE}
LABEL = {u[0]: u[1] for u in UNIVERSE}
CN = {u[0]: u[2] for u in UNIVERSE}
CLASS = {u[0]: u[6] for u in UNIVERSE}

CORE = ['CSI300', 'DIVIDEND', 'CHINEXT', 'TREASURY', 'GOLD']
EXT = [u[0] for u in UNIVERSE]


# ---------------------------------------------------------------- data
def load_daily():
    from engine import sina_index_daily

    def etf(sym):
        df = pd.read_csv(os.path.join(DATA, f'etf_{sym}.csv'), parse_dates=['date'])
        return df.set_index('date').sort_index()['close']

    out = {}
    for k, lab, cn, tk, kind, div, cls in UNIVERSE:
        if kind == 'index':
            out[k] = sina_index_daily(tk)['close']
        else:
            out[k] = etf(tk)
    df = pd.DataFrame(out).dropna(how='all')
    return df.resample('D').last().ffill()


def monthly_panel(df):
    """Monthly total returns, union of dates, NaN where an asset has no data."""
    m = df.resample('ME').last()
    r = m.pct_change().iloc[1:]
    for k, lab, cn, tk, kind, div, cls in UNIVERSE:
        if kind == 'index':
            r[k] = r[k] + div / 12
    return r


# ---------------------------------------------------------------- metrics
def metrics(r, rf=RF, ann=12):
    r = r.dropna()
    if len(r) < 3:
        return None
    cum = (1 + r).cumprod()
    n = len(r)
    cagr = (1 + r).prod() ** (ann / n) - 1
    vol = r.std(ddof=1) * np.sqrt(ann)
    mdd = (cum / cum.cummax() - 1).min()
    yr = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1)
    return {
        'ann_return_%': cagr * 100,
        'ann_vol_%': vol * 100,
        'sharpe': (cagr - rf) / vol if vol > 0 else np.nan,
        'max_dd_%': mdd * 100,
        'cum_return_%': ((1 + r).prod() - 1) * 100,
        'calmar': cagr / abs(mdd) if mdd < 0 else np.nan,
        'best_year_%': yr.max() * 100,
        'worst_year_%': yr.min() * 100,
        'months': n,
        'years': n / ann,
    }


def drawdown_episodes(r):
    r = r.dropna()
    cum = (1 + r).cumprod()
    dd = cum / cum.cummax() - 1
    eps, in_dd, trough, peak_i = [], False, None, None
    for i, v in enumerate(dd.values):
        if v < 0 and not in_dd:
            in_dd, peak_i = True, i - 1 if i > 0 else 0
            trough = (v, i)
        elif v < 0 and in_dd:
            if v < trough[0]:
                trough = (v, i)
        elif v >= 0 and in_dd:
            eps.append({'peak': str(r.index[peak_i].date()),
                        'trough': str(r.index[trough[1]].date()),
                        'recover': str(r.index[i].date()),
                        'depth_%': trough[0] * 100,
                        'length_m': i - peak_i})
            in_dd = False
    if in_dd:
        eps.append({'peak': str(r.index[peak_i].date()),
                    'trough': str(r.index[trough[1]].date()),
                    'recover': None,
                    'depth_%': trough[0] * 100,
                    'length_m': len(r) - 1 - peak_i})
    return eps


# ---------------------------------------------------------------- backtest
def backtest(rm, assets, w_target, cost_bps=COST_BPS * 1e4, rebal='quarterly',
             band=0.05):
    w = np.array(w_target, dtype=float)
    rows, w_hist = [], []
    for i, (d, row) in enumerate(rm.iterrows()):
        if i == 0:
            rows.append({'date': d, 'ret': 0.0, 'cost': 0.0})
            w_hist.append(w.copy())
            continue
        r = row[assets].values
        w = w * (1 + r)
        tot = w.sum()
        w = w / tot
        cost = 0.0
        do = (rebal == 'quarterly' and d.month in (3, 6, 9, 12)) or \
             (rebal == 'band' and np.abs(w - w_target).max() > band)
        if do:
            cost = float(np.abs(w_target - w).sum() * cost_bps / 1e4)
            w = np.array(w_target, dtype=float)
        rows.append({'date': d, 'ret': tot - 1 - cost, 'cost': cost})
        w_hist.append(w.copy())
    return (pd.DataFrame(rows).set_index('date'),
            pd.DataFrame(w_hist, index=rm.index, columns=assets))


# ---------------------------------------------------------------- estimators
def lw(rm, assets):
    X = rm[assets].values
    l = LedoitWolf().fit(X)
    return (rm[assets].mean().values * 12, l.covariance_ * 12,
            float(l.shrinkage_), np.cov(X.T, ddof=1) * 12)


def black_litterman(mu_s, cov, rf, w_mkt, tau=0.05):
    mu_mkt = float(w_mkt @ mu_s)
    var = float(w_mkt @ cov @ w_mkt)
    delta = max((mu_mkt - rf) / max(var, 1e-12), 0.5)
    pi = delta * cov @ w_mkt
    P = np.eye(len(mu_s))
    Om = np.diag(tau * np.diag(cov))
    A = np.linalg.inv(tau * cov) + P.T @ np.linalg.inv(Om) @ P
    b = np.linalg.inv(tau * cov) @ pi + P.T @ np.linalg.inv(Om) @ mu_s
    return np.linalg.solve(A, b), pi, delta


def risk_contrib(w, cov):
    rc = w * (cov @ w)
    s = rc.sum()
    return rc / s if s > 0 else np.zeros_like(w)


def solve_erc(cov):
    n = len(cov)

    def obj(logw):
        w = np.exp(logw - logw.max())
        w = w / w.sum()
        rc = risk_contrib(w, cov)
        return float(((rc - 1 / n) ** 2).sum())

    best, bestv = None, np.inf
    for seed in range(4):
        x0 = np.zeros(n) if seed == 0 else np.random.default_rng(seed).normal(0, .5, n)
        res = minimize(obj, x0, method='Nelder-Mead',
                       options={'maxiter': 8000, 'xatol': 1e-12, 'fatol': 1e-16})
        if res.fun < bestv:
            best, bestv = res.x, res.fun
    w = np.exp(best - best.max())
    return w / w.sum()


def solve_max_sharpe(mu, cov, rf, groups=None, band=None, cap=0.60,
                     feas_tol=1e-6):
    """Max-Sharpe with optional group (sleeve) risk-contribution band.

    groups : list of index lists; band : (lo, hi) on each group's share of
             total portfolio risk contribution.
    Returns (w, status) where status is 'solved' or 'fallback'.
    """
    n = len(mu)

    def neg(w):
        w = np.clip(w, 0, 1)
        s = w.sum()
        if s <= 0:
            return 1e9
        w = w / s
        var = max(w @ cov @ w, 1e-18)
        return -(w @ mu - rf) / np.sqrt(var)

    cons = [{'type': 'eq', 'fun': lambda w: np.clip(w, 0, 1).sum() - 1}]
    if groups and band:
        lo, hi = band
        for g in groups:
            cons.append({'type': 'ineq',
                         'fun': (lambda w, g=g: hi -
                                 risk_contrib(np.clip(w, 0, 1), cov)[g].sum())})
            cons.append({'type': 'ineq',
                         'fun': (lambda w, g=g: risk_contrib(
                             np.clip(w, 0, 1), cov)[g].sum() - lo)})

    def feasible(w):
        if abs(w.sum() - 1) > 1e-4:
            return False
        if w.min() < -feas_tol or w.max() > cap + 1e-4:
            return False
        if groups and band:
            rc = risk_contrib(w, cov)
            lo, hi = band
            for g in groups:
                if rc[g].sum() > hi + 1e-3 or rc[g].sum() < lo - 1e-3:
                    return False
        return True

    best, bestv = None, np.inf
    for seed in range(8):
        rng = np.random.default_rng(seed)
        w0 = np.ones(n) / n if seed == 0 else rng.dirichlet(np.ones(n))
        try:
            res = minimize(neg, w0, method='SLSQP', bounds=[(0, cap)] * n,
                           constraints=cons,
                           options={'maxiter': 9000, 'ftol': 1e-14})
        except Exception:
            continue
        w = res.x
        if w.min() < -1e-8:
            continue
        w = np.clip(w, 0, None)
        w = w / w.sum()
        if not feasible(w):
            continue
        v = neg(w)
        if v < bestv:
            best, bestv = w, v
    if best is None:
        return None, 'infeasible'
    return best, 'solved'


def penalty_max_sharpe(mu, cov, rf, groups=None, band=None, cap=0.60):
    """Soft-constraint fallback: maximise Sharpe minus constraint penalties."""
    n = len(mu)

    def f(w):
        w = np.clip(w, 0, 1)
        s = w.sum()
        if s <= 0:
            return 1e9
        w = w / s
        var = max(w @ cov @ w, 1e-18)
        r = (w @ mu - rf) / np.sqrt(var)
        pen = 0.0
        if groups and band:
            lo, hi = band
            rc = risk_contrib(w, cov)
            for g in groups:
                v = rc[g].sum()
                if v > hi:
                    pen += 1e4 * (v - hi) ** 2
                if v < lo:
                    pen += 1e4 * (lo - v) ** 2
        cap_pen = 1e4 * np.sum(np.maximum(0, w - cap) ** 2)
        return -r + pen + cap_pen

    best, bestv = None, np.inf
    for seed in range(6):
        rng = np.random.default_rng(seed)
        w0 = np.ones(n) / n if seed == 0 else rng.dirichlet(np.ones(n))
        res = minimize(f, w0, method='SLSQP',
                       bounds=[(0, 1)] * n,
                       constraints=[{'type': 'eq', 'fun': lambda w: w.sum() - 1}],
                       options={'maxiter': 9000, 'ftol': 1e-14})
        w = np.clip(res.x, 0, None)
        w = w / w.sum()
        if f(w) < bestv:
            best, bestv = w, f(w)
    return best


# ---------------------------------------------------------------- strategies
def build_strategies():
    """Each strategy = dict(name, group, desc, weights{asset: weight}, family)."""
    S = []

    def w(**kw):
        return {k: v for k, v in kw.items() if v > 0}

    # ---- classic recipes (cash sleeve dropped, redistributed pro-rata) ----
    # All-Weather: equity 40 / bond 30 / commodity 15 / cash 15 -> risky 85
    aw = {'CSI300': 0.40 * 0.6, 'DIVIDEND': 0.40 * 0.4,
          'TREASURY': 0.30, 'GOLD': 0.15}
    aw = {k: v / 0.85 for k, v in aw.items()}
    S.append(dict(key='AW', name='All-Weather China (Dalio structure)',
                  family='Classic', weights=aw,
                  desc='Equity 40 / Bond 30 / Commodity 15, cash 15% sleeve '
                       'dropped and redistributed pro-rata (no cash leg). '
                       'Equity sleeve: 60% CSI 300 + 40% SSE Dividend.'))
    # Permanent Portfolio: 25 stocks / 25 long bonds / 25 gold / 25 cash
    pp = {'CSI300': 25 / 2 / 75, 'DIVIDEND': 25 / 2 / 75,
          'TREASURY': 25 / 75, 'GOLD': 25 / 75}
    S.append(dict(key='PP', name='Permanent Portfolio (Browne)',
                  family='Classic', weights=pp,
                  desc='25 stocks / 25 long bonds / 25 gold / 25 cash; the cash '
                       'quarter is redistributed over the other three.'))
    # Golden Butterfly: 25 stocks / 25 long bonds / 25 gold / 10 broad
    # commodities / 15 cash. In China the broad-commodity leg is proxied by a
    # crude-oil QDII (the only broad commodity ETF with a pre-2020 history),
    # and the cash sleeve is redistributed.
    gb = {'CSI300': 25 / 2 / 85, 'DIVIDEND': 25 / 2 / 85,
          'TREASURY': 25 / 85, 'GOLD': 25 / 85, 'OIL': 10 / 85}
    S.append(dict(key='GB', name='Golden Butterfly (Vidoes)',
                  family='Classic', weights=gb, merged=True, merged_into='PP',
                  desc='25 stocks / 25 long bonds / 25 gold / 10 broad '
                       'commodities / 15 cash. The broad-commodity leg is a '
                       'crude-oil QDII (China has no broad commodity index for '
                       'retail), so its history starts in 2016; cash is '
                       'redistributed over the other legs.'))
    # 60/40
    S.append(dict(key='6040', name='60 / 40',
                  family='Classic',
                  weights={'DIVIDEND': 0.60, 'TREASURY': 0.40},
                  desc='60% Chinese equity (SSE Dividend) / 40% Treasury ETF.'))
    S.append(dict(key='EQ', name='Equal Weight (5 core assets)',
                  family='Classic',
                  weights={k: 1 / len(CORE) for k in CORE},
                  desc='Equal weight over CSI 300, SSE Dividend, ChiNext, '
                       'Treasury ETF, Gold ETF.'))
    S.append(dict(key='DIVTILT', name='Dividend Tilt (China value tilt)',
                  family='Classic', merged=True, merged_into='AW',
                  weights={'DIVIDEND': 0.40, 'CSI300': 0.20, 'TREASURY': 0.25,
                           'GOLD': 0.15},
                  desc='40% SSE Dividend + 20% CSI 300 + 25% Treasury + 15% Gold.'))
    # every recipe must be a complete allocation
    for st in S:
        tot = sum(st['weights'].values())
        assert abs(tot - 1) < 1e-9, (st['key'], tot)
    return S


def main():
    df = load_daily()
    rm_all = monthly_panel(df)
    print('=' * 92)
    print('资产面板：每个标的用自己完整的历史（不是共同样本）')
    print('=' * 92)
    hist = []
    for k in EXT:
        s = rm_all[k].dropna()
        hist.append({'asset': k, 'label': LABEL[k], 'cn': CN[k],
                     'class': CLASS[k], 'start': str(s.index[0].date()),
                     'end': str(s.index[-1].date()), 'months': len(s),
                     'years': len(s) / 12})
        print(f'  {LABEL[k]:26s} {CN[k]:8s} {s.index[0].date()} -> '
              f'{s.index[-1].date()}  {len(s):4d} 个月 ({len(s)/12:4.1f} 年)')
    pd.DataFrame(hist).to_csv(os.path.join(OUT, 'cn_asset_history.csv'), index=False)

    # ------------------------------------------------------------------
    # 1. asset class metrics
    # ------------------------------------------------------------------
    print('\n' + '=' * 92)
    print('【第一步】资产类别表现（Return / Risk / Drawdown / Years）')
    print('=' * 92)
    rows = []
    for k in EXT:
        s = rm_all[k].dropna()
        rec = {'asset': k, 'label': LABEL[k], 'cn': CN[k], 'class': CLASS[k]}
        for pname, m in PERIODS:
            sub = s.iloc[-m:]
            if len(sub) < m * 0.8:
                rec[f'{pname}_n'] = len(sub)
                continue
            mm = metrics(sub)
            rec[f'{pname}_n'] = len(sub)
            rec[f'{pname}_ret'] = mm['ann_return_%']
            rec[f'{pname}_vol'] = mm['ann_vol_%']
            rec[f'{pname}_mdd'] = mm['max_dd_%']
            rec[f'{pname}_sharpe'] = mm['sharpe']
        # YTD
        ytd = s[s.index.year == s.index[-1].year]
        if len(ytd) > 1:
            rec['YTD_ret'] = ((1 + ytd).prod() - 1) * 100
            rec['YTD_n'] = len(ytd)
        mm = metrics(s)
        rec.update({'full_ret': mm['ann_return_%'], 'full_vol': mm['ann_vol_%'],
                    'full_mdd': mm['max_dd_%'], 'full_sharpe': mm['sharpe'],
                    'full_cum': mm['cum_return_%'],
                    'best_year': mm['best_year_%'],
                    'worst_year': mm['worst_year_%'],
                    'years': mm['years'], 'months': mm['months']})
        rows.append(rec)
    AC = pd.DataFrame(rows)
    AC.to_csv(os.path.join(OUT, 'cn_asset_class_returns.csv'), index=False)
    show = AC[['asset', 'cn', 'class', 'full_ret', 'full_vol', 'full_mdd',
               'full_sharpe', 'best_year', 'worst_year', 'years']]
    print(show.rename(columns={'full_ret': '年化收益%', 'full_vol': '年化波动%',
                               'full_mdd': '最大回撤%', 'full_sharpe': '夏普',
                               'best_year': '最好年%', 'worst_year': '最差年%',
                               'years': '历史年数'}).round(2).to_string(index=False))

    # ------------------------------------------------------------------
    # 2. correlation
    # ------------------------------------------------------------------
    print('\n' + '=' * 92)
    print('【第二步】相关性矩阵（pairwise，最少 24 个月重叠）')
    print('=' * 92)
    corr = pd.DataFrame(index=EXT, columns=EXT, dtype=float)
    for a in EXT:
        for b in EXT:
            if a == b:
                corr.loc[a, b] = 1.0
                continue
            j = rm_all[[a, b]].dropna()
            corr.loc[a, b] = j[a].corr(j[b]) if len(j) >= MIN_OVERLAP else np.nan
    corr.to_csv(os.path.join(OUT, 'cn_corr_pairwise.csv'))
    key_pairs = [('CSI300', 'TREASURY'), ('DIVIDEND', 'TREASURY'),
                 ('CHINEXT', 'TREASURY'), ('GOLD', 'TREASURY'),
                 ('CSI300', 'GOLD'), ('DIVIDEND', 'GOLD'), ('CHINEXT', 'GOLD'),
                 ('CSI300', 'DIVIDEND'), ('CHINEXT', 'STAR50'),
                 ('CONVERT', 'TREASURY'), ('METALS', 'GOLD'), ('OIL', 'GOLD')]
    for a, b in key_pairs:
        print(f'   {LABEL[a]:24s} vs {LABEL[b]:24s} {corr.loc[a, b]:+.3f}')

    # ------------------------------------------------------------------
    # 3. Markowitz constructions
    # ------------------------------------------------------------------
    print('\n' + '=' * 92)
    print('【第三步】Markowitz 构造（ERC / Max-Sharpe / All-Weather MVO）')
    print('=' * 92)

    # 3a. correlation screen over the extended universe
    sub = rm_all[EXT].dropna()
    print(f'  共同样本 {sub.index[0].date()} -> {sub.index[-1].date()} '
          f'({len(sub)} 个月)')
    csub = sub.corr()
    order = csub.abs().mean().sort_values().index
    selected, dropped = [], []
    for a in order:
        dup = [s for s in selected if abs(csub.loc[a, s]) > 0.75]
        if dup:
            dropped.append((a, dup[0], csub.loc[a, dup[0]]))
        else:
            selected.append(a)
    print('  相关性筛选（|corr| > 0.75 视为同一因子）：')
    for a, s, c in dropped:
        print(f'    剔除 {LABEL[a]:24s} 与 {LABEL[s]:24s} {c:+.3f}')
    print(f'  低相关候选：{[LABEL[x] for x in selected]}')

    mu_e, cov_e, shr_e, _ = lw(sub, EXT)
    idx_e = {a: i for i, a in enumerate(EXT)}

    def greedy(pool, n):
        pick, rest = [], list(pool)
        for _ in range(n):
            best, bestv = None, None
            for a in rest:
                ix = [idx_e[x] for x in pick + [a]]
                c = cov_e[np.ix_(ix, ix)]
                w = np.ones(len(ix)) / len(ix)
                v = float(w @ c @ w)
                if bestv is None or v < bestv:
                    best, bestv = a, v
            pick.append(best)
            rest.remove(best)
        return pick

    lc_basket = greedy(selected, 5)
    print(f'  贪心低相关篮子（每次挑让组合波动下降最多的）：'
          f'{[LABEL[x] for x in lc_basket]}')

    # 3b. core-universe Markowitz (long sample)
    core_rm = rm_all[CORE].dropna()
    mu_c, cov_c, shr_c, cov_c_s = lw(core_rm, CORE)
    mu_c_bl, pi_c, delta_c = black_litterman(mu_c, cov_c, RF,
                                              np.ones(len(CORE)) / len(CORE))
    print(f'\n  核心池样本 {core_rm.index[0].date()} -> {core_rm.index[-1].date()} '
          f'({len(core_rm)} 个月)  LW 收缩 {shr_c:.3f}')
    for i, k in enumerate(CORE):
        print(f'    {LABEL[k]:24s} μ样本 {mu_c[i]*100:6.2f}%  μBL {mu_c_bl[i]*100:6.2f}%'
              f'  σ {np.sqrt(cov_c[i,i])*100:5.2f}%  [{CLASS[k]}]')

    w_erc = solve_erc(cov_c)
    print(f'  ERC        : ' + '  '.join(f'{LABEL[k]} {w_erc[i]*100:.1f}%'
                                        for i, k in enumerate(CORE)))
    w_mvo, st = solve_max_sharpe(mu_c_bl, cov_c, RF, cap=0.60)
    if w_mvo is None:
        w_mvo = penalty_max_sharpe(mu_c_bl, cov_c, RF)
        st = 'penalty'
    print(f'  MaxSharpe  : ' + '  '.join(f'{LABEL[k]} {w_mvo[i]*100:.1f}%'
                                        for i, k in enumerate(CORE)) + f'  [{st}]')
    # All-Weather MVO on the core: sleeve risk bands
    groups = [[CORE.index(k) for k in CORE if CLASS[k] == c]
              for c in ['Equity', 'Bond', 'Commodity']]
    groups = [g for g in groups if g]
    w_awm, st_awm = solve_max_sharpe(mu_c_bl, cov_c, RF, groups=groups,
                                      band=(0.10, 0.50), cap=0.60)
    if w_awm is None:
        w_awm = penalty_max_sharpe(mu_c_bl, cov_c, RF, groups=groups,
                                    band=(0.10, 0.50))
        st_awm = 'penalty'
    print(f'  AllW-MVO   : ' + '  '.join(f'{LABEL[k]} {w_awm[i]*100:.1f}%'
                                        for i, k in enumerate(CORE)) +
          f'  [{st_awm}]  板块风险贡献: ' + '  '.join(
              f'{c} {risk_contrib(w_awm, cov_c)[[CORE.index(k) for k in CORE if CLASS[k]==c]].sum()*100:.0f}%'
              for c in ['Equity', 'Bond', 'Commodity']))

    strategies = build_strategies()
    strategies.append(dict(key='ERC', name='Risk Parity (ERC, mu-free)',
                           family='Markowitz',
                           weights={k: float(w_erc[i]) for i, k in enumerate(CORE)},
                           desc='Equal risk contribution over the five core '
                                'assets; no expected-return assumption.'))
    strategies.append(dict(key='MVO', name='Max-Sharpe MVO (mu/sigma)',
                           family='Markowitz',
                           weights={k: float(w_mvo[i]) for i, k in enumerate(CORE)},
                           desc='Markowitz max-Sharpe on Black-Litterman '
                                'posterior mu and Ledoit-Wolf Sigma.'))
    strategies.append(dict(key='AWMVO',
                           name='All-Weather MVO (screen + sleeve bands)',
                           family='Markowitz',
                           weights={k: float(w_awm[i]) for i, k in enumerate(CORE)},
                           desc='Max-Sharpe inside an All-Weather structure: '
                                'equity / bond / commodity sleeves each 10%-50% '
                                'of total risk contribution.'))
    strategies.append(dict(key='LC',
                           name='Low-Correlation Screen (5 assets)',
                           family='Markowitz',
                           weights={k: 1 / len(lc_basket) for k in lc_basket},
                           desc='Greedy minimum-variance pick after removing '
                                '|corr|>0.75 duplicates: '
                                + ', '.join(LABEL[x] for x in lc_basket) +
                                '. Shorter sample (2020-12 onwards).'))
    strategies.append(dict(key='LCMVO',
                           name='Low-Correlation + Max-Sharpe',
                           family='Markowitz', merged=True, merged_into='LC',
                           weights=None,
                           desc='Max-Sharpe on the correlation-screened basket.'))
    # compute LC max sharpe
    lcs = rm_all[lc_basket].dropna()
    mu_l, cov_l, _, _ = lw(lcs, lc_basket)
    mu_l_bl, _, _ = black_litterman(mu_l, cov_l, RF,
                                    np.ones(len(lc_basket)) / len(lc_basket))
    w_lcm, _ = solve_max_sharpe(mu_l_bl, cov_l, RF, cap=0.60)
    if w_lcm is None:
        w_lcm = penalty_max_sharpe(mu_l_bl, cov_l, RF)
    for st_ in strategies:
        if st_['key'] == 'LCMVO':
            st_['weights'] = {k: float(w_lcm[i]) for i, k in enumerate(lc_basket)}
    for st_ in strategies:
        tot = sum(st_['weights'].values())
        assert abs(tot - 1) < 1e-6, (st_['key'], tot)

    # ------------------------------------------------------------------
    # 4. backtests
    # ------------------------------------------------------------------
    print('\n' + '=' * 92)
    print('【第四步】回测（季度再平衡，往返 30 bps）')
    print('=' * 92)
    perf_rows, curves, wrows = [], {}, []
    for st in strategies:
        assets = list(st['weights'].keys())
        rm_s = rm_all[assets].dropna()
        wv = np.array([st['weights'][a] for a in assets])
        bt, wh = backtest(rm_s, assets, wv)
        m = metrics(bt['ret'])
        m['strategy'] = st['name']
        m['key'] = st['key']
        m['family'] = st['family']
        m['sample_start'] = str(rm_s.index[0].date())
        m['sample_end'] = str(rm_s.index[-1].date())
        m['sample_months'] = len(rm_s)
        m['assets'] = ', '.join(LABEL[a] for a in assets)
        perf_rows.append(m)
        curves[st['key']] = (1 + bt['ret']).cumprod()
        for a, x in zip(assets, wv):
            wrows.append({'strategy': st['name'], 'key': st['key'],
                          'asset': a, 'label': LABEL[a], 'cn': CN[a],
                          'class': CLASS[a], 'weight_%': x * 100})
    P = pd.DataFrame(perf_rows)
    cols = ['key', 'strategy', 'sample_start', 'sample_months', 'ann_return_%',
            'ann_vol_%', 'sharpe', 'max_dd_%', 'cum_return_%', 'best_year_%',
            'worst_year_%']
    print(P[cols].round(2).to_string(index=False))
    P.to_csv(os.path.join(OUT, 'cn_strategy_perf.csv'), index=False)
    pd.DataFrame(wrows).to_csv(os.path.join(OUT, 'cn_strategy_weights.csv'),
                               index=False)
    CURVE_DF = pd.DataFrame(curves)
    CURVE_DF.to_csv(os.path.join(OUT, 'cn_strategy_curves.csv'))

    # common-window comparison (apples to apples)
    common_start = max(sub.index[0], core_rm.index[0])
    cw = rm_all.loc[common_start:]
    print(f'\n  共同窗口对比（{common_start.date()} 起，所有策略同一段样本）')
    rows = []
    for st in strategies:
        assets = list(st['weights'].keys())
        rm_s = cw[assets].dropna()
        if len(rm_s) < 36:
            continue
        wv = np.array([st['weights'][a] for a in assets])
        bt, _ = backtest(rm_s, assets, wv)
        m = metrics(bt['ret'])
        m['strategy'] = st['name']
        m['key'] = st['key']
        rows.append(m)
    CW = pd.DataFrame(rows)
    print(CW[['key', 'strategy', 'ann_return_%', 'ann_vol_%', 'sharpe',
              'max_dd_%', 'cum_return_%']].round(2).to_string(index=False))
    CW.to_csv(os.path.join(OUT, 'cn_strategy_perf_common.csv'), index=False)

    # cost sensitivity
    print('\n  成本敏感性（All-Weather China 与 Max-Sharpe MVO）')
    cost_rows = []
    for st in strategies:
        if st['key'] not in ('AW', 'MVO', 'AWMVO'):
            continue
        assets = list(st['weights'].keys())
        rm_s = rm_all[assets].dropna()
        wv = np.array([st['weights'][a] for a in assets])
        for cb in [0, 10, 30, 50]:
            bt, _ = backtest(rm_s, assets, wv, cost_bps=cb)
            m = metrics(bt['ret'])
            m.update({'key': st['key'], 'cost_bps': cb,
                      'ann_return_%': m['ann_return_%'],
                      'sharpe': m['sharpe']})
            cost_rows.append(m)
    CS = pd.DataFrame(cost_rows)[['key', 'cost_bps', 'ann_return_%', 'sharpe',
                                  'max_dd_%']]
    print(CS.round(3).to_string(index=False))
    CS.to_csv(os.path.join(OUT, 'cn_cost_sensitivity.csv'), index=False)

    # drawdowns and rolling returns
    dd_rows, roll_rows = [], []
    for st in strategies:
        c = curves[st['key']]
        for e in drawdown_episodes(c.pct_change().fillna(0)):
            e['strategy'] = st['name']
            e['key'] = st['key']
            dd_rows.append(e)
        r36 = c.iloc[-36:].copy()
        roll = (c / c.shift(36)) ** (12 / 36) - 1
        roll = roll.dropna()
        if len(roll):
            roll_rows.append({'key': st['key'], 'strategy': st['name'],
                              'roll3y_min_%': roll.min() * 100,
                              'roll3y_max_%': roll.max() * 100,
                              'roll3y_mean_%': roll.mean() * 100,
                              'roll3y_end_%': roll.iloc[-1] * 100})
    DD = pd.DataFrame(dd_rows)
    DD.to_csv(os.path.join(OUT, 'cn_strategy_drawdowns.csv'), index=False)
    RL = pd.DataFrame(roll_rows)
    RL.to_csv(os.path.join(OUT, 'cn_strategy_rolling3y.csv'), index=False)
    print('\n  滚动 3 年年化收益：')
    print(RL.round(2).to_string(index=False))

    # ------------------------------------------------------------------
    # 4b. walk-forward (out-of-sample) for the Markowitz strategies
    # ------------------------------------------------------------------
    print('\n' + '=' * 92)
    print('【第四步 B】Walk-forward：每 12 个月用当时可得数据重估权重，再持有一年')
    print('=' * 92)
    wf_rows = []
    for est_name, est in [('ERC', 'erc'), ('MaxSharpe', 'mvo'),
                          ('AllWeatherMVO', 'awmvo')]:
        rm_w = rm_all[CORE].dropna()
        seg, i, wts = [], 0, []
        while i + 36 + 12 <= len(rm_w):
            hist = rm_w.iloc[i:i + 36]
            fut = rm_w.iloc[i + 36:i + 48]
            mh, ch, _, _ = lw(hist, CORE)
            mh_bl, _, _ = black_litterman(mh, ch, RF,
                                          np.ones(len(CORE)) / len(CORE))
            if est == 'erc':
                w_ = solve_erc(ch)
            elif est == 'mvo':
                w_, st_ = solve_max_sharpe(mh_bl, ch, RF, cap=0.60)
                if w_ is None:
                    w_ = penalty_max_sharpe(mh_bl, ch, RF)
            else:
                g = [[CORE.index(k) for k in CORE if CLASS[k] == c]
                     for c in ['Equity', 'Bond', 'Commodity']]
                g = [x for x in g if x]
                w_, st_ = solve_max_sharpe(mh_bl, ch, RF, groups=g,
                                           band=(0.10, 0.50), cap=0.60)
                if w_ is None:
                    w_ = penalty_max_sharpe(mh_bl, ch, RF, groups=g,
                                             band=(0.10, 0.50))
            pr = pd.Series((fut[CORE].values * w_).sum(axis=1), index=fut.index)
            seg.append(pr)
            wts.append({k: float(w_[j]) for j, k in enumerate(CORE)})
            i += 12
        allr = pd.Series(np.concatenate([s.values for s in seg]),
                         index=[d for s in seg for d in s.index])
        m = metrics(allr)
        m['method'] = est_name
        m['n_segments'] = len(seg)
        wf_rows.append(m)
        wf_rows[-1]['weights_path'] = wts
    WF = pd.DataFrame([{k: v for k, v in r.items() if k != 'weights_path'}
                       for r in wf_rows])
    print(WF[['method', 'ann_return_%', 'ann_vol_%', 'sharpe', 'max_dd_%',
              'months', 'n_segments']].round(2).to_string(index=False))
    print('\n  各次重估的权重路径：')
    for r in wf_rows:
        print(f'   {r["method"]:14s} ' + ' | '.join(
            ' '.join(f'{k.split()[0][:6]} {v*100:.0f}' for k, v in w.items())
            for w in r['weights_path'][:3]) + '  ...')
    WF.drop(columns=[]).to_csv(os.path.join(OUT, 'cn_walkforward.csv'), index=False)
    with open(os.path.join(OUT, 'cn_walkforward_weights.json'), 'w',
              encoding='utf-8') as f:
        json.dump({r['method']: r['weights_path'] for r in wf_rows}, f,
                  ensure_ascii=False)

    # ------------------------------------------------------------------
    # 4c. sensitivity suite
    # ------------------------------------------------------------------
    print('\n' + '=' * 92)
    print('【第四步 C】敏感性分析')
    print('=' * 92)
    groups_c = [[CORE.index(k) for k in CORE if CLASS[k] == c]
                for c in ['Equity', 'Bond', 'Commodity']]
    groups_c = [g for g in groups_c if g]

    def awmvo_weights(mu, cov):
        w_, stt = solve_max_sharpe(mu, cov, RF, groups=groups_c,
                                   band=(0.10, 0.50), cap=0.60)
        if w_ is None:
            w_ = penalty_max_sharpe(mu, cov, RF, groups=groups_c,
                                     band=(0.10, 0.50))
            stt = 'penalty'
        return w_, stt

    print('\nA. 起点敏感性：用前 36 个月估权重，在其后的全部样本上评估')
    rows = []
    for st in ['2013-08', '2015-01', '2016-01', '2018-01', '2020-01', '2022-01']:
        sub = core_rm.loc[core_rm.index >= f'{st}-01']
        if len(sub) < 36 + 24:
            continue
        hist = sub.iloc[:36]
        mh, ch, _, _ = lw(hist, CORE)
        mh_bl, _, _ = black_litterman(mh, ch, RF,
                                      np.ones(len(CORE)) / len(CORE))
        w_, stt = awmvo_weights(mh_bl, ch)
        fut = sub.iloc[36:]
        wv = np.array(w_)
        pr = pd.Series((fut[CORE].values * wv).sum(axis=1), index=fut.index)
        m = metrics(pr)
        m['estimate_start'] = st
        m['test_months'] = len(fut)
        m['status'] = stt
        rows.append(m)
        print(f'   估权区间 {st} 起（36 个月）→ 测试 {len(fut)} 个月:  '
              f'年化 {m["ann_return_%"]:5.2f}%  波动 {m["ann_vol_%"]:5.2f}%  '
              f'夏普 {m["sharpe"]:5.2f}  MDD {m["max_dd_%"]:7.2f}%  '
              f'权重 ' + ' '.join(f'{LABEL[k]} {w_*100:.0f}'
                                  for w_, k in zip(wv, CORE)))
    SENS_A = pd.DataFrame(rows)
    SENS_A.to_csv(os.path.join(OUT, 'cn_sens_start.csv'), index=False)

    print('\nB. 协方差方法：LW 收缩 vs 样本协方差')
    rows = []
    for nm, c in [('LW 收缩', cov_c), ('样本协方差', cov_c_s)]:
        w_e = solve_erc(c)
        mh = core_rm[CORE].mean().values * 12
        mh_bl, _, _ = black_litterman(mh, c, RF, np.ones(len(CORE)) / len(CORE))
        w_a, _ = awmvo_weights(mh_bl, c)
        rows.append({'covariance': nm,
                     **{f'ERC {LABEL[k]}': round(w_e[i] * 100, 1)
                        for i, k in enumerate(CORE)},
                     **{f'AWMVO {LABEL[k]}': round(w_a[i] * 100, 1)
                        for i, k in enumerate(CORE)}})
    SENS_B = pd.DataFrame(rows)
    print(SENS_B.to_string(index=False))
    SENS_B.to_csv(os.path.join(OUT, 'cn_sens_covariance.csv'), index=False)

    print('\nC. μ 敏感性（All-Weather MVO 权重，单位 %）')
    for k, a in enumerate(CORE):
        vals = []
        for mult in [0.5, 0.75, 1.0, 1.25, 1.5]:
            m2 = mu_c_bl.copy()
            m2[k] = mu_c[k] * mult
            w2, _ = awmvo_weights(m2, cov_c)
            vals.append(w2[k] * 100)
        print(f'   {LABEL[a]:24s} μ×[0.5,0.75,1,1.25,1.5] -> '
              f'[{", ".join(f"{x:5.1f}" for x in vals)}]')

    print('\nD. 相关性压力测试（相关系数整体放大）')
    sd = np.sqrt(np.diag(cov_c))
    cm0 = cov_c / np.outer(sd, sd)
    for mult in [1.0, 1.5, 2.0]:
        cm = np.clip(cm0 * mult, -0.99, 0.99)
        np.fill_diagonal(cm, 1.0)
        c2 = np.outer(sd, sd) * cm
        w_e = solve_erc(c2)
        w_a, _ = awmvo_weights(mu_c_bl, c2)
        print(f'   corr×{mult:.1f}:  ERC ' +
              ' '.join(f'{LABEL[k]} {w*100:.0f}' for w, k in zip(w_e, CORE)) +
              '  |  AWMVO ' +
              ' '.join(f'{LABEL[k]} {w*100:.0f}' for w, k in zip(w_a, CORE)))

    print('\nE. 无风险利率敏感性（All-Weather MVO）')
    for rf in [0.005, 0.010, 0.0165, 0.025, 0.035]:
        w2, _ = solve_max_sharpe(mu_c_bl, cov_c, rf, groups=groups_c,
                                  band=(0.10, 0.50), cap=0.60)
        if w2 is None:
            w2 = penalty_max_sharpe(mu_c_bl, cov_c, rf, groups=groups_c,
                                     band=(0.10, 0.50))
        print(f'   rf={rf:.2%}: ' + ' '.join(
            f'{LABEL[k]} {w*100:.1f}' for w, k in zip(w2, CORE)))

    print('\nF. 成本敏感性已在上表；补充：All-Weather China 与 All-Weather MVO '
          '在不同再平衡频率下的年化收益')
    rows = []
    for st in strategies:
        if st['key'] not in ('AW', 'AWMVO'):
            continue
        assets = list(st['weights'].keys())
        rm_s = rm_all[assets].dropna()
        wv = np.array([st['weights'][a] for a in assets])
        for rb in ['quarterly', 'band']:
            bt, _ = backtest(rm_s, assets, wv, rebal=rb)
            m = metrics(bt['ret'])
            m['key'] = st['key']
            m['rebalance'] = rb
            rows.append(m)
    print(pd.DataFrame(rows)[['key', 'rebalance', 'ann_return_%', 'ann_vol_%',
                              'sharpe', 'max_dd_%']].round(2).to_string(index=False))

    # ------------------------------------------------------------------
    # 5. charts + web payload
    # ------------------------------------------------------------------
    charts(P, curves, corr, AC, strategies)
    dump_web(P, CW, curves, AC, corr, strategies, rm_all, core_rm, sub,
             lc_basket, w_erc, w_mvo, w_awm, mu_c, mu_c_bl, cov_c, wf_rows)
    print('\nsaved output/cn_web.json')


def charts(P, curves, corr, AC, strategies):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for fp in [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']:
        if os.path.exists(fp):
            font_manager.fontManager.addfont(fp)
            plt.rcParams['font.sans-serif'] = [
                font_manager.FontProperties(fname=fp).get_name()]
            break
    plt.rcParams['axes.unicode_minus'] = False

    # equity curves
    fig, ax = plt.subplots(figsize=(11, 5.4))
    for k, c in curves.items():
        nm = P.loc[P['key'] == k, 'strategy'].iloc[0]
        ax.plot(c.index, c.values, lw=2.0 if k == 'AWMVO' else 1.3, label=nm)
    ax.set_title('China A-share strategies: growth of 100 (net of 30bps costs)',
                 color='#1f4e79', fontweight='bold')
    ax.set_ylabel('Value')
    ax.grid(alpha=.3)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'cn_strategies_curves.png'), dpi=150)
    plt.close(fig)

    # correlation heatmap
    fig, ax = plt.subplots(figsize=(9.5, 8))
    im = ax.imshow(corr.values.astype(float), cmap='RdBu_r', vmin=-1, vmax=1)
    ax.set_xticks(range(len(EXT)))
    ax.set_xticklabels([LABEL[a] for a in EXT], rotation=45, ha='right', fontsize=8)
    ax.set_yticks(range(len(EXT)))
    ax.set_yticklabels([LABEL[a] for a in EXT], fontsize=8)
    for i in range(len(EXT)):
        for j in range(len(EXT)):
            v = corr.values[i, j]
            if np.isnan(v):
                continue
            ax.text(j, i, f'{v:.2f}', ha='center', va='center', fontsize=7,
                    color='white' if abs(v) > 0.55 else '#222')
    ax.set_title('China A-share asset class correlation (monthly)',
                 color='#1f4e79', fontweight='bold')
    fig.colorbar(im, ax=ax, shrink=.8)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'cn_strategies_corr.png'), dpi=150)
    plt.close(fig)

    # weight bars
    fig, ax = plt.subplots(figsize=(12, 5.4))
    keys = list(P['key'])
    sleeves = ['Equity', 'Bond', 'Commodity']
    width = 0.8 / len(keys)
    for i, st in enumerate(strategies):
        sl = {}
        for a, x in st['weights'].items():
            sl[CLASS[a]] = sl.get(CLASS[a], 0) + x
        xs = np.arange(len(sleeves)) + (i - (len(keys) - 1) / 2) * width
        ax.bar(xs, [sl.get(s, 0) * 100 for s in sleeves], width,
               label=P.loc[P['key'] == st['key'], 'strategy'].iloc[0])
    ax.set_xticks(range(len(sleeves)))
    ax.set_xticklabels(sleeves)
    ax.set_ylabel('Allocation %')
    ax.set_title('Sleeve allocation by strategy', color='#1f4e79',
                 fontweight='bold')
    ax.legend(fontsize=7, ncol=3)
    ax.grid(alpha=.3, axis='y')
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'cn_strategies_sleeves.png'), dpi=150)
    plt.close(fig)
    print('saved figures/cn_strategies_{curves,corr,sleeves}.png')


def dump_web(P, CC, curves, AC, corr, strategies, rm_all, core_rm, sub,
             lc_basket, w_erc, w_mvo, w_awm, mu_c, mu_c_bl, cov_c, wf_rows):
    web = {
        'meta': {
            'generated': '2026-11-01',
            'rf': RF,
            'cost_bps_roundtrip': COST_BPS * 1e4,
            'rebalance': 'quarterly',
            'currency': 'CNY',
            'sample_start': str(core_rm.index[0].date()),
            'sample_end': str(core_rm.index[-1].date()),
            'months': int(len(core_rm)),
            'note': 'China A-share only. No cash / money-market leg. '
                    'Equity legs measured as index price + explicit dividend '
                    'yield; bond and commodity legs as ETF close prices.',
        },
        'assets': [],
        'tickers': {u[0]: u[3] for u in UNIVERSE},
        'cn': {u[0]: u[2] for u in UNIVERSE},
        'assetClass': [],
        'corr': {'assets': EXT, 'matrix': [[None if np.isnan(corr.loc[a, b])
                                           else round(float(corr.loc[a, b]), 4)
                                           for b in EXT] for a in EXT]},
        'strategies': [],
        'dates': [],
        'coreDates': [],
        'coreReturns': {},
        'commonDates': [],
        'commonReturns': {},
    }
    for k in EXT:
        web['assets'].append({'key': k, 'label': LABEL[k], 'cn': CN[k],
                               'class': CLASS[k],
                               'ticker': KEY[k][3]})
    ac = AC.to_dict('records')
    for r in ac:
        web['assetClass'].append({
            'key': r['asset'], 'label': r['label'], 'cn': r['cn'],
            'class': r['class'],
            'periods': {
                p: {
                    'ret': r.get(f'{p}_ret'), 'vol': r.get(f'{p}_vol'),
                    'mdd': r.get(f'{p}_mdd'), 'sharpe': r.get(f'{p}_sharpe'),
                    'n': r.get(f'{p}_n'),
                } for p, _ in PERIODS
            },
            'ytd': r.get('YTD_ret'), 'ytd_n': r.get('YTD_n'),
            'full': {'ret': r['full_ret'], 'vol': r['full_vol'],
                     'mdd': r['full_mdd'], 'sharpe': r['full_sharpe'],
                     'cum': r['full_cum'], 'years': r['years'],
                     'months': r['months'],
                     'best_year': r['best_year'], 'worst_year': r['worst_year']},
        })
    for st in strategies:
        k = st['key']
        pr = P.loc[P['key'] == k].iloc[0]
        cr = CC.loc[CC['key'] == k]
        web['strategies'].append({
            'key': k, 'name': st['name'], 'family': st['family'],
            'desc': st['desc'],
            'merged': bool(st.get('merged', False)),
            'merged_into': st.get('merged_into'),
            'weights': [{'key': a, 'label': LABEL[a], 'cn': CN[a],
                         'class': CLASS[a], 'w': float(st['weights'][a])}
                        for a in st['weights']],
            'perf': {kk: (None if pd.isna(pr[kk]) else float(pr[kk]))
                     for kk in ['ann_return_%', 'ann_vol_%', 'sharpe',
                                'max_dd_%', 'cum_return_%', 'best_year_%',
                                'worst_year_%', 'calmar', 'months']},
            'sample': {'start': pr['sample_start'], 'end': pr['sample_end'],
                       'months': int(pr['sample_months'])},
            'common': ({kk: (None if pd.isna(cr.iloc[0][kk])
                             else float(cr.iloc[0][kk]))
                        for kk in ['ann_return_%', 'ann_vol_%', 'sharpe',
                                   'max_dd_%', 'cum_return_%']}
                       if len(cr) else None),
            'curve': [float(x) for x in curves[k].values],
            'curveDates': [d.strftime('%Y-%m') for d in curves[k].index],
        })
    # monthly return matrices for the in-browser simulator
    web['coreDates'] = [d.strftime('%Y-%m') for d in core_rm.index]
    for k in CORE:
        web['coreReturns'][k] = [float(x) for x in core_rm[k].values]
    cw = rm_all.loc[core_rm.index[0]:]
    web['commonDates'] = [d.strftime('%Y-%m') for d in cw.index]
    for k in EXT:
        s = cw[k]
        web['commonReturns'][k] = [None if pd.isna(x) else float(x)
                                   for x in s.values]
    web['mvo'] = {
        'assets': CORE,
        'mu_sample': [float(x) for x in mu_c],
        'mu_bl': [float(x) for x in mu_c_bl],
        'cov': [[float(x) for x in row] for row in cov_c],
        'erc': [float(x) for x in w_erc],
        'mvo': [float(x) for x in w_mvo],
        'awmvo': [float(x) for x in w_awm],
    }
    web['walkforward'] = [
        {'method': r['method'],
         'ann_return_%': r['ann_return_%'], 'ann_vol_%': r['ann_vol_%'],
         'sharpe': r['sharpe'], 'max_dd_%': r['max_dd_%'],
         'cum_return_%': r['cum_return_%'], 'months': r['months'],
         'n_segments': r['n_segments']}
        for r in wf_rows
    ]
    with open(os.path.join(OUT, 'cn_web.json'), 'w', encoding='utf-8') as f:
        json.dump(web, f, ensure_ascii=False, separators=(',', ':'))


if __name__ == '__main__':
    main()
