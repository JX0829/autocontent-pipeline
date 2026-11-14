# SPEC-20261114 · 科技速报信息源改造（降重复 / 关论文 / 换 X 关键词 / 收进流水线）

| 项 | 内容 |
|---|---|
| 提出 | 小品（产品经理，速报使用方） |
| 实现 owner | 小何 |
| 验收 owner | 小品（跑样张 + 编号制片回归） |
| 时间 | 2026-11-14 |
| 优先级 | P1（信息质量直接影响选题与出片意愿） |
| 用户原话要点 | 「信息源重复率太高」「GitHub 每天都是那几个项目」「Reddit 一天也不变」「论文太专业，暂时关闭」「推特热门话题跟想要的也不匹配」「要 AI + breaking news + 高关注度大企业」 |

## 1. 现状（已实测，非推测）

**编排位置（关键结论）**：速报**没有**进流水线。
- 抓取层在流水线：`src/crawlers/atomic_fetchers.py`（`fetch_twitter_x_tech` / `fetch_github_trending` / `fetch_reddit_ai_hot` / `fetch_huggingface_daily_papers` / `fetch_arxiv_ai_papers` / `fetch_official_blog_posts`）
- **编排/去重/板块/快照在我这边**：`agents_context/xiao-pin/scripts/fetch_trends.py`（`build_unified_hotspot_digest` / `filter_and_dedup` / `load_seen_trends` / `save_digest_snapshot`），`pipeline.yaml` 里没有任何 trends/digest 步骤
- cron：`~/.hermes/profiles/xiao-pin/scripts/run_push_stream.sh` → `push_feishu_stream.py`（no_agent 纯脚本）

**结构**：4 板块 9 条 = 官方博客 1 + X 2 ｜ HF 1 + arXiv 1 ｜ GitHub 2 ｜ Reddit 2

**重复率实测（2026-11-14 11:xx，真实抓取）**
```
GitHub 候选 10 条 → 6 条已在 48h 去重库 = 60%
Reddit 候选 10 条 → 6 条已在 48h 去重库 = 60%
```
两条根因（都在 `fetch_trends.py:filter_and_dedup`）：
1. `save_seen_trends` 是 **48 小时滑动窗口**，两天后同一批 repo/帖子重新可上；
2. **「顺延补齐」**：新条目不足 `count` 时从**已看过的候选**里往回捞凑数 → 宁可重复也不留空位。这是「每天都是那几个」的直接原因。

**X 关键词（写死）**：`src/crawlers/atomic_fetchers.py:875`
```python
def fetch_twitter_x_tech(query: str = "Claude 3.7 OR DeepSeek OR Coding Agent OR OpenAI", ...)
```
`fetch_trends.py` 调用未传 query → 每轮都吃这 4 个词。`Claude 3.7` 是旧版本号，无「大厂 / breaking」维度。

## 2. 需求

**R1｜板块合并：4 板块 9 条 → 2 板块 5~6 条**
- 板块 1「官方公告 + 一手动态」：官方 1 + X 2 = 3 条（保留现状时效门禁）
- 板块 2「开源爆款 + 社区热议」：GitHub 与 Reddit **合并成一个池子**，统一排序后出 **2~3 条**（跨源去重，同一实体只出一次）

**R2｜论文源关闭（做成开关，不是删代码）**
- `papers: false` → 不调用 `fetch_huggingface_daily_papers` / `fetch_arxiv_ai_papers`（顺带省两次 30s 超时预算）
- 以后要开，改配置即可

**R3｜去重与排序（降重复的核心）**
- 去重窗口 48h → **5 天**（可配置 `dedupe_window_days: 5`）
- **取消「顺延补齐」**：新鲜条目不足时**少出一条**，不许拿看过的条目凑数（宁缺勿凑）
- Reddit 加时效门禁：**近 2 天**（现在 `max_days=None`，一个月前的热帖也能进）
- GitHub 排序优先「今日新增 star」而非绝对 star；同一 `owner/repo` 5 天内只出一次

**R4｜X 关键词改造（三组查询 + 排除词，已实测跑通）**
```text
Q1 AI 主力   : OpenAI OR Anthropic OR DeepSeek OR Gemini OR Claude
Q2 大厂/突发 : NVIDIA OR Microsoft OR Apple OR Meta OR TSMC OR Tesla
Q3 发布信号  : announces OR launches OR release OR "generally available" OR breaking
排除词        : crypto / airdrop / giveaway / 空投
```
- 三组结果合并 → 按发布时间排序 → 跨查询去重 → 取 2 条
- **关键词必须配置化**（放进 `config/trends.yaml` 或 `config.yaml` 的 `sources.x.queries`），不要写死在函数默认参数里
- 实测样例（2026-11-14 真跑）：Q1 命中「OpenAI 解散机器人团队」「Anthropic IPO」；Q2 命中「黄仁勋离职相关」「Tesla 新岗位」

**R5｜速报编排收进流水线（这次一并迁移）**
- 把 `build_unified_hotspot_digest` / `filter_and_dedup` / `load_seen_trends` / `save_digest_snapshot` 从 `agents_context/xiao-pin/scripts/fetch_trends.py` 迁到流水线（建议 `src/digest/`），去重库与快照路径可配置（默认仍指 xiao-pin 的 `data/`）
- cron 侧 `run_push_stream.sh` 只保留一行调用入口（no_agent 纯脚本，stdout 直发不变）
- 目标：**信息源与编排只有一处实现**，我这边不再维护第二份

**R6｜兼容（不许破坏制片链路）**
- `data/last_digest_snapshot.json` 结构不变：顶层 `{"1": item, …, "N": item}`，每条含 `title/desc/url/source/badge/published_at/formatted_cst/unique_id`
- 用户回「N」→ 我按编号制片那条链路必须零改动可用

## 3. 不做

- 不改审批/发布/台账链路；不改飞书卡片样式；不动去重库历史数据（迁移后继续用，不重建）
- 不引入新的外部付费源；X 仍走 `agent-reach`，不碰 9222

## 4. 验收（我按这套回归）

| # | 场景 | 通过判据 |
|---|---|---|
| A1 | 跑一轮 | 共 **5~6 条**、板块 **2 个**、**无论文条目**；空板块必须显式告警 |
| A2 | 连跑两轮（或比对相邻两期快照） | 同一条目**不重复出现**；`unique_id` 交集为空；重复率 <20% |
| A3 | X 结果内容 | 至少 1 条属于「大厂 / breaking」；样例贴 3 条给用户确认口径 |
| A4 | 快照兼容 | `last_digest_snapshot.json` 顶层仍为编号 dict；取 `"1"` 的 `url/title/source` 齐全 |
| A5 | cron 链路 | `run_push_stream.sh` 仍 no_agent、stdout 即速报全文，飞书卡片正常收到 |

## 5. 影响面

| 文件 | 改动 |
|---|---|
| `src/crawlers/atomic_fetchers.py` | X 关键词改为从配置读取（保留默认值兜底） |
| `src/digest/`（新增） | 从 `fetch_trends.py` 迁入编排 + 去重 + 快照逻辑 |
| `config/trends.yaml`（新增） | `papers: false` / `dedupe_window_days: 5` / `sections` 条数 / `x.queries` / 排除词 |
| `scripts/run_push_stream.sh` | 只改调用入口一行 |
| 我的资产 | `agents_context/xiao-pin/scripts/fetch_trends.py` 迁走后保留为薄壳或删除（迁完通知我） |

**风险**：卡片条数变少（9→5~6），用户可能觉得信息量下降 → 用「不重复」换「新鲜」，符合用户诉求；论文关闭为开关，随时可开。
