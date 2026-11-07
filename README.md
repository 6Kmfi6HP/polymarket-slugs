# Polymarket UPDOWN Market Data

自动抓取 Polymarket 5m/15m UPDOWN 已关闭市场,以**无损原始观测层 + CSV 投影层**的双层结构保存到仓库。

## 架构

```
pull    Gamma API -> data/raw/YYYY-MM-DD.jsonl.xz   无损采集,append-only
build   raw -> data/{YYYY-MM-DD}/{interval}.csv     纯函数投影,可随时重算
snapshot  scripts/git-snapshot.sh                    滚动快照提交(不变)
```

`python3 scripts/fetch-slugs.py`(默认 `all`)依次执行 pull + build。

- **pull 层**:游标分页拉取已关闭市场;剥离纯展示字段后**原样**存入按市场窗口日期分区的压缩 JSONL;slug 不匹配的观测写入 `_quarantine.jsonl` 而不是静默丢弃;新出现的 API 字段写入 `_canary.jsonl`。
- **build 层**:从 raw 编译出每日 CSV(去重取最后一次观测,即结算终态);带状态文件只重写有新数据的分区;首次构建时会把旧 CSV 播种(`"seeded": true`)进 raw 层,使 raw 成为唯一事实来源。
- **snapshot 层**:与之前相同的滚动快照(amend + force-with-lease),历史不膨胀。

## 数据目录

```
data/
├── raw/
│   ├── 2026-08-27.jsonl.xz    # 该日市场窗口的全部观测(可能多于一播)
│   ├── _quarantine.jsonl      # slug 校验失败的观测(如每日 Up/Down 系列)
│   └── _canary.jsonl          # 审计后新出现的 API 字段
├── 2026-08-27/
│   ├── 5m.csv
│   └── 15m.csv
├── _summary.json              # 每日每 interval 行数(与旧格式一致)
└── _pipeline.json             # 流水线状态:行数/播种/隔离/canary
```

每个日期目录按市场开始时间(slug 内时间戳)的 UTC 日期分桶。

## CSV 格式

每个 `5m.csv` / `15m.csv` 包含表头,字段如下:

| 字段 | 说明 |
|------|------|
| `slug` | Polymarket 市场 slug |
| `asset` | 资产代码,例如 `BTC`, `ETH`, `SOL`, `XRP`, `BNB`, `DOGE`, `HYPE` |
| `question` | 市场问题文本 |
| `endDate` | 市场结束时间 |
| `eventStartTime` | 从 slug 推导出的 interval 开始时间,UTC ISO 格式 |
| `priceToBeat` | UPDOWN 市场的目标价格(窗口开盘价) |
| `winningOutcome` | 已解析的胜出方向,通常为 `Up` 或 `Down` |
| `outcomePrices` | Polymarket 返回的 outcome price JSON 字符串 |
| `lastTradePrice` | 最后一笔成交价格 |
| `volume` | 市场成交量 |
| `openInterest` | 事件 open interest,API 缺失时为空 |
| `oneHourPriceChange` | 1 小时价格变化 |
| `spread` | 买卖价差 |
| `umaResolutionStatus` | UMA 结算状态 |
| `conditionId` | 市场 condition id |
| `resolutionSource` | 结算数据源 URL |
| `finalPrice` | 窗口收盘价(与 `priceToBeat` 配对决定 Up/Down;raw 层启用后才有) |
| `closedTime` | 实际结算时间(raw 层启用后才有) |

2026-08-27 及之后(raw 层启用后)的分区为 18 列;此前的历史分区由旧 CSV 播种而来,`finalPrice`/`closedTime` 永久为空(API 已不提供回填),其余 16 列与旧数据逐值一致。

## 原始数据层(raw)

`data/raw/YYYY-MM-DD.jsonl.xz` 每行一个观测:

```json
{"fetched_at": "...", "hash": "sha256...", "market": { ...API 原始 payload... }}
```

- **无损**:除 `description`/`icon`/`image`(纯展示样板,占 payload 约 30%)外全字段保留;配合 xz 压缩,每天约 1.3 MB。
- **append-only**:同一 slug 的 payload 变化(如结算终态回填)会追加新观测,构建时取最后一次;未变化的市场不会重复追加。
- **隔离区**:slug 不匹配 `{asset}-updown-{interval}-{ts}` 的市场(例如每日 `btc-up-or-down-august-27-2026-9pm-et` 系列)记录在 `_quarantine.jsonl`,不再静默丢失。
- **canary**:每次 pull 对比 API 字段并集与 2026-08 审计基线,新字段追加到 `_canary.jsonl` 并在日志中 `[WARN]`,采集决策保持显式。

## Slug 格式

```
{asset}-updown-{interval}-{unix_timestamp}
```

| 字段 | 说明 |
|------|------|
| `asset` | 小写资产名,例如 `btc`, `eth`, `sol`, `xrp`, `bnb`, `doge`, `hype` |
| `interval` | `5m` 或 `15m` |
| `unix_timestamp` | 市场开始时间 Unix 秒,对齐到 5m/15m 边界 |

例如:`btc-updown-5m-1783181100` 表示 `2026-07-04T16:05:00Z` 开始的 BTC 5m 市场。

## 快照提交(历史不增长)

数据更新不会新增 commit 记录,仓库历史不会随 Action 运行而膨胀:

- 每次 Action 更新数据后运行 `scripts/git-snapshot.sh`:
  - 若 HEAD 已是快照提交(作者为 `bot@polymarket-slugs.local`),把数据变更合并进该提交(`git commit --amend`),再用 `git push --force-with-lease` 覆盖远端;
  - 若 HEAD 是普通代码提交,则在其上新建一个快照提交,之后的运行改为 amend 它。
- 因此 `main` 历史 = 代码提交 + 至多 1 个 `chore(data): snapshot ...` 提交。
- `--force-with-lease` 保证不覆盖别人新推的提交:若推送瞬间远端前移,本次运行失败退出;下个 15 分钟周期会重新抓取(默认回看 2 天)并快照,数据不丢失。
- 注意:该方案依赖 force push。若给 `main` 启用了分支保护,需允许 force push,否则快照推送会被拒绝。
- 拉取本仓库最新数据建议使用 `git fetch && git reset --hard origin/main`(历史会被重写,普通 `git pull` 可能提示非快进)。

## 手动运行

```bash
python3 scripts/fetch-slugs.py                 # pull + build(默认)
python3 scripts/fetch-slugs.py pull            # 只采集原始观测
python3 scripts/fetch-slugs.py build           # 只从 raw 编译 CSV
python3 scripts/fetch-slugs.py build --force   # 全量重建所有分区
python3 scripts/fetch-slugs.py pull --lookback-days 7
FETCH_END_DATE_MIN=2026-07-01T00:00:00Z python3 scripts/fetch-slugs.py
```

抓取后若要按 Action 的方式提交数据(amend 快照 + force-with-lease 推送):

```bash
bash scripts/git-snapshot.sh
```

## 读取示例

```python
import csv

with open("data/2026-07-04/5m.csv", newline="") as f:
    rows = list(csv.DictReader(f))

for row in rows:
    print(row["slug"], row["asset"], row["priceToBeat"], row["finalPrice"], row["winningOutcome"])
```

需要 CSV 之外的字段时,从 `data/raw/` 解压对应日期的观测即可——raw 是唯一事实来源,CSV 只是投影,新增列不需要重新抓取。

## 测试

```bash
python3 -m unittest discover -s tests
```

`tests/test_fetch_slugs.py` 覆盖 pull 去重/隔离/canary、投影列、播种与增量构建;`tests/test_git_snapshot.py` 用临时 git 仓库端到端验证快照脚本。
