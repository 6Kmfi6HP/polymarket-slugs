# Polymarket UPDOWN Slugs

自动抓取 Polymarket 5m/15m UPDOWN 市场 slug 并存储到仓库。

## 数据格式

```
data/
├── 2026-06-22/
│   ├── 5m.txt     # btc-updown-5m-{unix_ts}, eth-updown-5m-{unix_ts}, ...
│   └── 15m.txt    # btc-updown-15m-{unix_ts}, eth-updown-15m-{unix_ts}, ...
├── 2026-06-23/
│   ├── 5m.txt
│   └── 15m.txt
├── ...
└── _summary.json  # 汇总统计
```

### Slug 格式

```
{asset}-updown-{interval}-{unix_timestamp}
```

| 字段 | 说明 |
|------|------|
| `asset` | 资产名: `btc`, `eth`, `sol`, `xrp`, `bnb`, `doge`, `hype` |
| `interval` | 间隔: `5m` 或 `15m` |
| `unix_timestamp` | 市场起始时间的 Unix 秒，对齐到 5m/15m 边界 |

例如: `btc-updown-5m-1782116400` → 2026-06-22 08:20:00 UTC

## 工作原理

GitHub Action 每 15 分钟运行一次，通过 Gamma API (`gamma-api.polymarket.com`)
获取最近关闭的 UPDOWN 市场 slug，按天/间隔分组存储。

## 使用方式

### 从 pmdata.dev 获取历史价格数据

```python
import requests

with open("data/2026-06-22/5m.txt") as f:
    slugs = [line.strip() for line in f if line.strip()]

# pmdata.dev API 调用
for slug in slugs:
    url = f"https://pmdata.dev/api/v1/markets/{slug}"
    resp = requests.get(url)
    # ...
```

## 手动运行

```bash
python scripts/fetch-slugs.py
```
