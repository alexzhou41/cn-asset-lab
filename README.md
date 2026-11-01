# CN Asset Lab · 中国大类资产配置

一个只覆盖中国 A 股的大类资产配置研究站，外加一张「美国国会议员买卖股票」的查询台。
静态站点，数据每 6 小时由 GitHub Actions 在云端自动刷新一次。

## 在线地址

`https://<你的GitHub用户名>.github.io/cn-asset-lab/`

## 站点结构

| 标签页 | 内容 |
|---|---|
| 资产类别 | 12 个中国标的的收益/风险/回撤/夏普，1Y–全历史区间切换 |
| 相关性 | 12×12 两两相关性热力图（pairwise，最少 24 个月重叠） |
| 策略 | 8 个只做多的配置配方：净值曲线、环形配置、横向对比、walk-forward 样本外检验 |
| 配置构建器 | 5 个核心标的权重滑块 + 预设 + 0/10/30/50 bps 成本切换 |
| 美国政策交易 | 国会财务披露查询台：可筛选交易表、议员排行、板块净买入、事后收益对照 |
| 方法与局限 | 度量口径与已知局限 |

## 目录

```
carry/                              抓取与分析脚本（与本机工作区同名目录）
  policy_refresh.py                 一次刷新：抓最新 → 合并快照 → 重算 → 写 data.json → 重建静态站
  policy_analysis.py                快照合并 + 事后收益 + 汇总统计
  cn_policy_export.py               写 miniapps/.../miniapp/client/data.json
  cn_site_build.py                  重建 site_cn_allweather/index.html
  us_prices.py                      新浪财经美股日线抓取（带本地缓存）
  collect_policy_trades.py          首次大规模抓取（谨慎，会翻很多页）
  output/policy_trades_snapshot.csv 累积快照——自动更新的主状态，必须提交
miniapps/china-all-weather/         Mini App 源码（桌面端面板，数据在 Node 侧内联）
site_cn_allweather/index.html       构建产物（静态站）
data/us_stocks/                     美股日线缓存（不提交，用 Actions cache）
```

## 关键口径

- 权益腿＝指数价格 ＋ 显式股息率（沪深300 2.5%、上证红利 4.5%、创业板指 0.5%、
  中证500 1.5%、科创50 0.3%）；债券与商品腿＝ETF 收盘价。**不含现金腿。**
- 成本按往返 30 bps，在每次再平衡时对成交金额收取。
- 国会交易金额是**申报区间**（如 $1,001–$15,000），合计按区间中值估算，不是成交额。
- 事后收益从**申报成交价**起算，基准为 SPY；披露滞后平均约 23 天。

## 本地跑一遍

```bash
pip install -r requirements.txt
python carry/policy_refresh.py     # 抓取 + 重算 + 重建
python -m http.server -d site_cn_allweather 8000
```

## 自动更新的两种模式

1. **GitHub Actions（本仓库默认）**——每 6 小时在云端跑，电脑关机也更新。
   需要在仓库 Settings → Pages 里把 Source 设为 *GitHub Actions*。
2. **本机定时任务**——`carry/policy_refresh.py` 也可以被本机的调度器每 6 小时调用，
   再用平台部署工具重新发布。两套可以并存。

## 注意

- `carry/output/policy_trades_snapshot.csv` 是累积快照，**不能删**：删掉就等于把历史清零。
- 价格缓存目录 `data/us_stocks/` 有 700+ 个 csv、约 65 MB，不要提交到 git。
- 本项目用于研究，不构成投资建议。
