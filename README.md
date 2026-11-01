# CN Asset Lab · 中国大类资产配置

一个只覆盖中国 A 股的大类资产配置研究站，外加一张「美国国会议员买卖股票」的查询台。
静态站点，数据每 6 小时由 GitHub Actions 自动刷新一次。

## 在线地址

**https://alexzhou41.github.io/cn-asset-lab/**

（国内访问更快的镜像：`https://bqtvkpthqal4c.space.mcode.cn`）

## 站点结构

| 标签页 | 内容 |
|---|---|
| 资产类别 | 12 个中国标的的收益/风险/回撤/夏普，1Y–全历史区间切换 |
| 相关性 | 12×12 两两相关性热力图（pairwise，最少 24 个月重叠） |
| 策略 | 8 个只做多的配置配方：净值曲线、环形配置、横向对比、walk-forward 样本外检验 |
| 配置构建器 | 5 个核心标的权重滑块 + 预设 + 0/10/30/50 bps 成本切换 |
| 美国政策交易 | 国会财务披露查询台：可筛选交易表、议员排行、板块净买入、事后收益对照 |
| 方法与局限 | 度量口径与已知局限 |

## 自动更新怎么跑

```
Capitol Trades (住宅 IP 抓取)
        ↓
GitHub Actions   ← 调度与日志在 GitHub 云端（cron: 17 */6 * * * UTC）
  runs-on: [self-hosted, cnassetlab]   ← 本机自托管 runner 只负责执行
        ↓
carry/policy_refresh.py   增量抓 5 页 → 合并快照 → 重算事后收益 → 重建站点
        ↓
carry/api_commit.py       经 api.github.com 的 Git Data API 发布到 main
carry/pages_deploy.py     写入 gh-pages 分支 → GitHub 服务端构建 → Pages 上线
```

三条设计约束（都来自实测，不是猜测）：

1. **执行端必须是住宅 IP。** Capitol Trades 挂在 Vercel Security Checkpoint 后面，
   对所有数据中心出口 IP 一律 429；`r.jina.ai`、`allorigins`、`codetabs` 这些公共代理
   本身也是数据中心段，同样 429。没有任何绕过路径，所以抓取只能从本机发出——
   调度、日志、构建、发布仍然全部在 GitHub 云端。
2. **不能用 `git push`。** 本机 `github.com` / `codeload.github.com` 直连超时，
   而 Windows 的 schannel TLS 会被重置（OpenSSL 正常）。因此读用 tree + blob，
   写用 create blob → create tree → create commit → update ref，全部走 `api.github.com`。
3. **不用 `actions/upload-pages-artifact`。** 产物上传要走 Azure 产物服务，
   本机 runner 的 .NET 到那个平面同样被重置。改用 legacy Pages：把站点写进
   `gh-pages` 分支，由 GitHub 自己的构建服务发布。

本机另有一个定时任务（每 6 小时的 :47 分，与云端调度错开）负责：
保证自托管 runner 在线、把云端产物同步回工作区、更新国内镜像站、刷新桌面端 Mini App。

## 目录

```
carry/                              抓取与分析脚本（与本机工作区同名目录）
  policy_refresh.py                 一次刷新：抓最新 → 合并快照 → 重算 → 写 data.json → 重建静态站
  policy_analysis.py                快照合并 + 事后收益 + 汇总统计
  cn_policy_export.py               写 miniapps/.../miniapp/client/data.json
  cn_site_build.py                  重建 site_cn_allweather/index.html
  us_prices.py                      新浪财经美股日线抓取（带本地缓存，缓存目录可用 US_PRICE_CACHE 覆盖）
  cn_strategies.py                  A 股大类资产配置引擎
  github_api.py                     api.github.com 传输层（Git Data API 读写 + 退避重试）
  api_commit.py                     把工作区产物发布到 main
  pages_deploy.py                   把站点发布到 gh-pages 分支
  cloud_task_sync.py                本地镜像同步 + runner 保活 + 漏跑补抓
  collect_policy_trades.py          首次大规模抓取（谨慎，会翻很多页）
  output/policy_trades_snapshot.csv 累积快照——自动更新的主状态，必须提交
miniapps/china-all-weather/         Mini App 源码（桌面端面板，数据在 Node 侧内联）
site_cn_allweather/index.html       构建产物（静态站）
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

## 注意

- `carry/output/policy_trades_snapshot.csv` 是累积快照，**不能删**：删掉就等于把历史清零。
- 价格缓存目录 `data/us_stocks/` 有 700+ 个 csv、约 65 MB，不提交；自托管 runner 上
  通过 `US_PRICE_CACHE` 指向仓库之外的持久目录。
- 自托管 runner 是本机后台进程，重启或休眠后需要重新拉起（本地定时任务会自动做）。
- 本项目用于研究，不构成投资建议。
