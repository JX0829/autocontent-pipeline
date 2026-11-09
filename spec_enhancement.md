# spec_enhancement.md — AutoContent Pipeline 声明式改造 · 去重加固 · 模型切换韧性

> **文档状态**：`v1.3` · 更新于 2026-11-09 · 基准 commit `60697fd`（工作区含小品未提交修复，见附录 D）
> **v1.1 变更**：整合 `qa_xiaopin.md`（2026-11-08→09 模型切换事故复盘）全部 12 类问题，新增 9 个任务、1 个阶段（P5）、2 个 hook、3 节设计约束
> **v1.2 变更**：① P-1~P-6 内聚缺口修补落位（P4 转为必做、新增 T1.7 物料契约 / T4.5 TTS 接线 / T4.6 审批抽象、修正 `render_landscape` 假依赖、统一横版命名）；② 将小品已落地的横屏/飞书修复**纳入本文档改动范围**（不单独分支），新增 §4.7 接管原则、附录 D 文件状态台账、C7/C8 耦合点、T0.3 规范任务，T3.2/T4.4 按内聚契约重写
> **v1.3 变更**：交付面（最后一公里）审计 4 处空缺落位 —— 新增 **T0.4 运行时自举**（Remotion 浏览器缺失，阻断级）、**T1.8 投料通道实现**（`builtin.intake` 只有声明无载体）、**T1.9 配置面收敛**（1 死键 + 4 未声明 + 无 fail-fast）、**T4.7 出片与发片解耦**（`output_only`）；配套新增 §1.7 交付面缺口审计（G-A~G-E）、**§4.9 交付面契约**、G9/G10、附录 A 运行时/配置/投料契约；任务总数 31 → **35**
> **执行原则**：本文件是**唯一执行依据**。任何实现与本文件冲突时，先改本文件，再改代码。
> **中断恢复**：每次任务结束后必须更新 `§2 进度快照`。恢复工作时，从快照中第一个非 `✅` 的任务继续，禁止跳步。

---

## 0. 状态图例与执行纪律

### 0.1 任务状态

| 标记 | 含义 | 允许的下一状态 |
|---|---|---|
| `⬜ PENDING` | 未开始 | 🟨 |
| `🟨 DOING` | 执行中 | ✅ / ⛔ |
| `✅ DONE` | 已完成并通过验收 | — |
| `⛔ BLOCKED` | 失败 2 次或验收不通过 | 🟨（需人工确认后） |
| `⏭️ SKIPPED` | 经确认跳过 | — |

### 0.2 纪律（防出错的核心机制）

1. **禁止跳步**：P(n) 未全部 `✅` 且 GATE 未通过，不得开始 P(n+1)。
2. **单任务单 commit**：commit message 前缀为任务 ID，例：`T2.2 feat(db): 新增三层加解锁表`。
3. **两振出局**：同一任务连续失败 2 次 → 执行 §7 回滚 → 标记 `⛔` → 停下等人工确认，**禁止自行改方案硬闯**。
4. **验收优先**：每个任务的"验收命令"必须真实执行并贴出输出，未执行视为未完成。
5. **不做清单**（§6）中的事项，任何阶段都不得顺手动。
6. **模型切换纪律**（v1.1 新增）：任何模型变更（换型号/换网关/换版本）**必须**走 附录 B 的 SOP，且**必须先跑 T1.6 自检脚本**。禁止"只改 .env 就上线"。
7. **接管纪律**（v1.2 新增）：工作区中已存在的小品修复**不回退、不推翻**，但必须按 §4.8 的内聚契约重做。**"能跑通"不等于"达标"**，验收以本文件的契约断言为准，不以"视频看着没问题"为准。
8. **声明即生效纪律**（v1.2 新增）：凡写进 `pipeline.yaml` / `.env` / `manifest.schema.json` 的键，必须被代码真实读取并有验收命令佐证；出现"写了没人读"的死键即视为 P0 级缺陷（对齐 G8）。

---

## 1. 背景与根因（实测取证）

基准数据：`content_tasks` 163 行 / `output/` 24 个日期目录 / `renderer/public/tasks` 179 个残留 task 目录。

### 1.1 去重链路失效（原有）

| # | 位置 | 实测证据 | 后果 |
|---|---|---|---|
| R1 | `src/hotspot_extractor.py` | `entity_key` 形如 `hackernews:claude-code-...-1794114373`，尾部为 unix 时间戳 | 同一事件重跑即生成新 key，`UNIQUE` 约束永不触发 |
| R2 | `scripts/generate_from_news.py:165` | `process_task` 全程未调用 `check_topic_duplicate()` | 物料直进流水线的路径**完全无去重** |
| R3 | `src/db.py:85` | `INSERT OR REPLACE INTO content_tasks` | 命中冲突时静默覆盖旧行，流程继续 |
| R4 | `src/feishu/approval_service.py:104` | `fname = f"{parent_task}_{name_part}_{int(time.time())}{ext}"` | 每次重发换新文件名，飞书中不可辨识 |
| R5 | `src/feishu/approval_service.py:134` | `msg_id` 仅写日志，未落库 | 无幂等依据，无法回溯 |

**实证**：DB 中「一键放行，密码上了公网」×2、「梁神又掀桌了…」×2 —— 同一选题重复生产并重复推送。

### 1.2 结构耦合点（原有）

| # | 位置 | 问题 |
|---|---|---|
| C1 | `renderer/public/tasks/` | 179 个 task 目录共享且无清理，竖版/横版共用，并发互相覆盖 |
| C2 | `generate_from_news.py:170` | `target_account_id="douyin-tech-main"` 硬编码 |
| C3 | `generate_from_news.py` | `source_url="https://news.hot-digest.local"` 硬编码 |
| C4 | `src/safety/comment_funnel.py:65-66` | 默认 `base_url=http://127.0.0.1:8317/v1`（即将下线的 CPA 代理）、默认模型 `gemini-3.8-flash` |
| C5 | `src/db.py::create_task` | 内部写死 `status="SCRIPTED"`，绕过 `INGESTED` 态 |
| C6 | 产物命名 | 竖版 `<HHMMSS>_<task>/video.mp4`，横版 `<task>/<task>_video_landscape_16_9.mp4` → **v1.2 统一为 `video_16_9.mp4` / `cover_16_9.png`（T4.1）** |
| **C7** | `scripts/render_landscape_bilibili.py` vs `scripts/render_landscape.py` | **两套「唯一入口」并存**：前者已提交（`31812d5`）用 A1 分辨率 / A2 封面分辨率 / **A3 首帧与封面像素差**；后者未提交，用 OCR bbox 跨度 ≥75%。**A3 抓不到「竖版封面塞横屏」**——错版的首帧与封面本为同一张图，像素差恒为 0，A3 必然通过。且 `generate_bilibili_horizontal_demo.py:122` 仍 `import render_landscape_bilibili`，新入口未被接入 → **这就是 QA#6 二次复发的机制** |
| **C8** | `renderer/src/components/BilibiliLandscapeCover.tsx`（小品已改） | 右侧三卡改取 `props.scenes[[0,1,3][i]]`，但含 `if (!text) return null;` —— **卡片静默消失**，违反 §4.5；并用 `(scene as any)` 侵蚀类型，schema 变更时无编译期报错 |
| **C9** | `scripts/run_publish_worker.py`（小品已改） | `_post_to_card2()` 把 post→Card 2.0 的转换**写死在发送函数里**，属未声明 transform；`os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_bc83...")` **硬编码真实会话 ID 作兜底** |

### 1.3 模型切换事故根因（v1.1 新增，来自 `qa_xiaopin.md`）

11-08 12:32 全局模型从 `gemini-3.8-flash` 切到 `deepseek-v4.1-flash`，同日爆发 3 起连锁故障。根因五条：

| # | 根因 | 现场证据 | 后果 |
|---|---|---|---|
| **M1** | **定时任务未 pin 模型**，执行引擎漂移护栏直接拒绝运行 | `RuntimeError: Skipped to prevent unintended spend: global inference config drifted ... this job is unpinned` | 脚本**根本没执行**，速报全断（13:00/15:00 连续失败） |
| **M2** | **模型名硬编码在默认值里**，网关已无该模型 | `[Level 2] LLM classification error: unknown provider for model gemini-3.8-flash` | 分类异常兜底返回 `"D"` → **所有评论判恶意、静默丢弃** |
| **M3** | **推理模型 token 预算严重不足** | `max_tokens=5 → finish=length, content=''`；抬到 1000 后正常 | 三层调用（classify/reply/seed）全部返回**空字符串**，且被当成正常结果 |
| **M4** | **降级路径不告警** | `if not items: continue`、`except: 判D`、`finish=length` 不检查 | 故障潜伏数天，靠用户主动反馈才被发现 |
| **M5** | **出厂质检只看分辨率** | 横屏片 `ffprobe` 通过但第 0.5 秒封面是竖版裁切图，二次复发 | 错版片推给用户 |

**M3 的具体数字**（QA 实测）：`deepseek-v4.1-flash` 每次先消耗 **250~450 reasoning tokens** 才输出正文。**⚠️ 2026-11-09 复测修正：当前实测已达 1000~1260（见 §5 T1.4 标定表），本段旧数值仅作历史参照，不可用于预算推导。** 原预算：

| 调用点 | 原 max_tokens | 实测 |
|---|---|---|
| `level2_classify` | 5 | `finish=length, content=''` → 判 D |
| `level3_generate_reply` | 100 | `finish=length, content=''` → 不发 |
| `generate_seed_comment` | 120 | `finish=length, content=''` → 不发 |
| 同 prompt 抬到 1000 | 1000 | ✅ 正常 |

> **这条是本次优化的核心靶心**：`pipeline.yaml` 里不能出现"调用方自定 max_tokens"，预算必须由能力槽位的 `model_profile` 统一声明并随模型切换重新标定。

### 1.4 QA 问题 → 任务映射（12 条全覆盖）

| QA# | 问题 | 归类 | 落地任务 |
|---|---|---|---|
| 1 | cron 未 pin 模型 → 静默断流 | 配置漂移 | **T3.4** + T2.5 |
| 2① | 模型名失效 → unknown provider → 判 D | 模型解耦 | **T1.4** + T1.5 |
| 2② | 推理模型 token 预算不足 → 空 content | 模型画像 | **T1.4** + T1.5 + T1.6 |
| 3 | 空板块静默跳过 / 超时预算不足 | 静默失败 | T2.5 + **T1.4**（timeout 声明） |
| 4 | 策略开关过期未解冻 + CDP 未启动 | 环境自检 | **T0.2** + T1.3（requires 自检） |
| 5 | 登录态未固化，重启即失 | 外部状态持久化 | **T5.1** |
| 6 | 横屏用竖版组件 / 封面写死尺寸 | 出厂质检 | **T4.4** |
| 7 | 模板硬编码假数据 | 出厂质检 | **T4.4** + T2.6 |
| 8 | post `code_block` 静默丢弃 | 静默失败 | **T3.2**（扩） |
| 9 | 摘要过短（34 字） | 内容质量 | **T1.4**（`min_output_chars`） |
| 10 | seen 提前写 → 漏回 | 幂等顺序 | **T5.1** + T3.2 |
| 11 | 判重精确匹配失效 | 指纹归一化 | T2.4（复用归一化规则） |
| 12 | 环境坑（timeout/DaemonThread/tmp/列名等） | 工程规范 | 附录 C |

### 1.5 三类共性问题（优化必须系统性解决）

1. **静默失败** — 判 D、`continue`、`except` 吞异常、API 返回 success 但渲染丢失。→ 设计约束 **§4.5**
2. **本地状态不可信** — "已处理" ≠ "已回复"；seen 写过就跳过。→ 设计约束 **§4.6**
3. **出厂质检缺失** — 只验分辨率、只信本地标记，不回读外部真值。→ 设计约束 **§4.6** + T4.4

### 1.6 内聚缺口审计（v1.2 新增，实测取证）

> 核心结论：**能力已经内聚，编排没有内聚。** 渲染/TTS/扩写/合规/质检全在仓内，不需要模型做任何特殊配置；但「投什么料、怎么拆字段、输出到哪」仍压在使用方身上。

| # | 外溢点 | 实测证据（2026-11-09 复核） | 内聚失败原因 |
|---|---|---|---|
| **I1** | 物料 → 15 字段编排由人或 Agent 手工完成 | `scripts/generate_from_news.py:20` `NEWS_TASKS = [` 为**硬编码内联 dict 列表**；`batch_generate_6_and_7.py::TASKS_DATA` 同为手写 15 字段 | 使用方必须**改源码**才能投料，不是"发物料" |
| **I2** | 物料契约无自描述 | 全仓 `find . -name "*schema*.json"` **零命中**，无 `manifest.schema.json` | 字段约定靠口口相传，新增需求必漂移 |
| **I3** | 音色/语速硬编码 | `generate_from_news.py:175` `voice = "zh-CN-YunxiNeural" if ... else "zh-CN-XiaoxiaoNeural"`；`:176` `rate="+10%"` | 换音色必须改代码 |
| **I4** | `.env` 配置项成为死键 | `grep -rn "TTS_VOICE" --include=*.py .` → **零引用**；而 `.env:5-7` 明明有 `TTS_VOICE_TECH` / `TTS_VOICE_NEWS` / `TTS_RATE` | 配置写了但没人读，运维以为改得动 |
| **I5** | 审批强绑飞书 | `pipeline.yaml` 附录 A `approval` 步仅 `message: "请在飞书卡片确认成片"`，无渠道抽象 | 非飞书环境直接跑不动 |
| **I6** | 横版渲染声明了假依赖 | `render_landscape(task_id, props_json, out_dir)` 实测**只读 props**，不读 `video.mp4`；而 `pipeline.yaml` 声明 `reads: ["draft_script.json", "video.mp4"]` | 假依赖使"只出横版"永远不可能（架构设计错误） |
| **I7** | 横版产物命名三处不一致 | 实测 `render_landscape.py:48,49` 产出 `video_landscape_16_9.mp4` / `cover_landscape.png`；方案声明 `video_16_9.mp4` / `cover_16_9.png`；`dispatch` 又按声明名读取 | 分发步永远读不到文件 |

**模型到底需要什么**（澄清项）：不需要任何"特殊配置"，只需三项基础能力，且已由 `profile` 覆盖 —— ① 能输出 JSON（不达则走 `retry.on_schema_error`）② output token 预算足够（由 `max_tokens = reasoning_reserve + 正文预算` 标定，**不是模型特性开关**）③ 能生成中文。

**修补映射**（6 处，全部在本版落位）：

| 编号 | 修补内容 | 落地位置 |
|---|---|---|
| **P-1** | P4 从"可选"改为**必做**，T4.1 提为 GATE-3 之后第一优先级 | §2 快照、§5 P4 章首 |
| **P-2** | 新增 **T1.7 物料契约 `manifest.schema.json`**（最小物料 ≥3 字段） | §5 T1.7 |
| **P-3** | 新增 **T4.5 TTS 配置接线**（废 `:175-176` 硬编码） | §5 T4.5 |
| **P-4** | 新增 **T4.6 审批渠道抽象** `approval.mode` | §5 T4.6 |
| **P-5** | 修正 `render_landscape` 的 `reads`（删假依赖 `video.mp4`） | 附录 A |
| **P-6** | 统一横版产物命名 `video_16_9.mp4` / `cover_16_9.png` | T4.1 + T4.4 + 附录 A |

### 1.7 交付面缺口审计（v1.3 新增，实测取证）

> 审计对象不是文档叙述，而是**把附录 A 的 `pipeline.yaml` 抽出来做机器审计 + 本机实测**。判据只有一条：**消费方拿到文档后，第一次能不能直接跑出片。**

**结论：9 项判据，6 项达标，3 项未达标（1 项阻断）。**

| # | 判据 | 结果 | 实测证据 |
|---|---|---|---|
| 1 | 每次执行只需提供物料 | ✅ | `min_fields: [title]` + `any_of: [[body],[source_url]]`，其余字段全有默认值 |
| 2 | 数据流自闭环，无隐性外部输入 | ✅ | 7 步 `reads` 全部有上游产出对应 |
| 3 | 账号默认值真实存在 | ✅ | `config/accounts.yaml:2` 确有 `douyin-tech-main` |
| 4 | 换模型不动流水线 | ✅ | 步骤体无模型名，7 个 env 键全走 `providers` |
| 5 | 去重幂等对消费方不可见 | ✅ | L1/L2/L3 三层，`dispatch` 强制过台账 |
| 6 | 出片运行时齐备（ffmpeg 等） | ✅ | ffmpeg / ffprobe / tesseract / npx / edge_tts 7.3.1 / node_modules 均 OK |
| 7 | **首次执行不联网、不额外准备** | ❌ **阻断** | 见 G-A |
| 8 | **配置面收口、缺即快失败** | ❌ | 见 G-B |
| 9 | **投料通道有实现** | ❌ | 见 G-C |

#### G-A 运行时自举缺失（阻断级）

| 探测项 | 实测结果 |
|---|---|
| `renderer/node_modules/.remotion` | **不存在** |
| `~/Library/Caches/remotion` | **不存在** |

**后果**：消费方第一次出片会卡在 `npx remotion render` 自动下载 Chrome Headless Shell（约 150MB）。**离线/内网环境直接失败。** 而 `requires` 只声明了 `renderer: "remotion>=4"`（版本号），**没声明浏览器与字体**。
→ 落地 **T0.4**

#### G-B 配置面未收口 + 无 fail-fast

`.env` 实测 12 个键，三类问题：

| 类别 | 键 | 实测 |
|---|---|---|
| 纯死键（代码零引用） | `REMOTION_DIR` | `grep --include=*.py` → **0 命中** |
| 代码实读但未纳入 `pipeline.yaml` 声明 | `FEISHU_APP_ID`(2) / `FEISHU_APP_SECRET`(2) / `FEISHU_APPROVAL_CHAT_ID`(8) / `SQLITE_DB_PATH`(3) | 用例数见括号，`providers` 仅声明 7 个键 |
| 已正确声明 | `LLM_*`×4 + `TTS_*`×3 | ✅ |

**无 fail-fast**：缺 `LLM_API_KEY` 要跑到第 3 步 `expand_script` 才炸，前两步白跑 —— 这是「静默失败」的配置版。
→ 落地 **T1.9**

#### G-C 投料通道只有声明，没有实现

`pipeline.yaml` 写的是 `material: {type: json, schema: manifest.schema.json}`，但 `intake` 步 `run: builtin.intake` **是空壳**。
「把物料发给他」到底怎么发 —— 飞书发文件？发文本？放哪个目录？命令行传路径？—— **全文没有一个字定义**。消费方在 P1–P5 全做完后，仍会卡在「我该往哪递」。
→ 落地 **T1.8**

#### G-D 审批默认值未定（轻微）
`approval_modes: [feishu, cli, auto]` 只声明取值域；`${APPROVAL_MODE:-auto}` 的默认值需在实现层落实，否则无飞书环境会在第 6 步 gate 卡住。→ 并入 **T4.7**

#### G-E 出片与发片未解耦（轻微）
`aspects` 只有 `vertical/landscape`，**没有「只要片、不发片」开关** → 出片与发片仍耦合，`dispatch` 会去碰各平台登录态。→ 落地 **T4.7**

**修补映射**（4 处，本版落位）：

| 编号 | 新增任务 | 治什么 | 阶段 |
|---|---|---|---|
| **T0.4** | 运行时自举（浏览器/字体/依赖一条命令装齐 + 启动自检） | G-A | P0 |
| **T1.8** | 投料通道实现（3 种载体，只依赖 manifest schema） | G-C | P1 |
| **T1.9** | 配置面收敛（死键处置 + 全键声明 + fail-fast） | G-B | P1 |
| **T4.7** | 出片/发片解耦（`output_only`）+ 审批默认值落实 | G-D / G-E | P4 |

> **性质判定**：这 4 项**不是设计缺陷，而是方案里还没写进步骤的空白**。契约层已做到「只给物料」，落地层还差三个「最后一公里」+ 一个解耦开关。

---

## 2. 进度快照（每次任务后必须更新）

| 阶段 | 任务 | 状态 | 完成时间 | Commit | 备注 |
|---|---|---|---|---|---|
| P0 | T0.1 基线快照与分支隔离 | ✅ DONE | 2026-11-09 | `94c2636` | 分支 `feat/pipeline-spec-enhancement`；baseline `163\|163` + `179` |
| P0 | T0.2 模型调用点与环境盘点 | ✅ DONE | 2026-11-09 | `94c2636` | QA#4；产出 `model_inventory.md`（4 调用点/网关 2 模型/7 job） |
| P0 | T0.3 工作区卫生与提交边界 | ✅ DONE | 2026-11-09 | `94c2636` | 条目 12 → 6；`_deprecated/` 归档 5 个一次性脚本 |
| P0 | T0.4 运行时自举 | ✅ DONE | 2026-11-09 | `2b40da1` | **v1.3 新增；G-A 阻断项已解除（system-fallback）** |
| P1 | T1.1 落 pipeline.yaml | ✅ DONE | 2026-11-09 | `98bdbce` | 11 段 contract / 6 槽位 / 7 步 / 22 hooks，零模型名 |
| P1 | T1.2 契约校验器 | ✅ DONE | 2026-11-09 | `437c23d` | 5+4 项校验；AST 扫描命中 3 处 max_tokens 违例 |
| P1 | T1.3 薄 Runner | ✅ DONE | 2026-11-09 | `437c23d` | dry-run 全绿 EXIT=0；5 项负向退出码全对 |
| P1 | T1.4 model_profile 能力契约 | ✅ DONE | 2026-11-09 | `a25d06c` | 实测 reasoning 1023~1259；main 6144/fast 2048 |
| P1 | T1.5 LLM 统一调用层 | ✅ DONE | 2026-11-09 | `505417e` | 4 调用点收口；6 项异常路径实测；删静默判D |
| P1 | T1.6 模型切换自检脚本 | ✅ DONE | 2026-11-09 | `d08789d` | 4 槽位真实自检 4/4 PASS；抓到预算过紧并回调 8192/2560 |
| P1 | T1.7 物料契约 `manifest.schema.json` | ✅ DONE | 2026-11-09 | `e9a696e` | 9 例契约矩阵 9/9；runner 已接线 |
| P1 | T1.8 投料通道实现 | ✅ DONE | 2026-11-09 | `7a89d0f` | 三载体 IDENTICAL True；重放被拦 |
| P1 | T1.9 配置面收敛 | ✅ DONE | 2026-11-09 | `7a89d0f` | DEADKEY=0 UNDECLARED=0；缺键 EXIT=78 |
| P1 | GATE-1 声明层与模型契约验收 | ✅ PASS | 2026-11-09 | `7a89d0f` | 7/7 检查全绿；可进 P2 |
| P2 | T2.1 Hook 执行引擎 | ✅ DONE | 2026-11-09 | `4dd146e` | 22/22 自检通过；runner 警告 20→0 |
| P2 | T2.2 DB 四表迁移 | ✅ DONE | 2026-11-09 | `d27db94` | 四表就绪；claim True→False；状态流转/续跑/幂等跳过实测 |
| P2 | T2.3 发送台账 Hook | ✅ DONE | 2026-11-09 | `8444872` | 首发 true→二次 already_sent；stale_artifact 实测 |
| P2 | T2.4 内容/视觉指纹 Hook | ✅ DONE | 2026-11-09 | `8444872` | normalize True/True；3 帧指纹+artifacts 落库 |
| P2 | T2.5 静默失败护栏 | ✅ DONE | 2026-11-09 | `b63678c` | 基线 37/37 新增 0；silence_guard degraded:1 实测 | QA#1#3#8 母题 |
| P2 | T2.6 外部真值校验 Hook | ✅ DONE | 2026-11-09 | `b63678c` | 4 种 kind 实测；CLI text_span 0.992 | QA#7#10 |
| P2 | GATE-2 台账幂等验收 | ✅ PASS | 2026-11-09 | `b63678c` | 4 项断言全绿 EXIT=0 | 3 发 1 落地才可进 P3 |
| P3 | T3.1 entity_key 归一化 | ✅ DONE | 2026-11-09 | `aec0c2c` | 生产已迁移：3 行改写、163/163、残留时间戳 0 |
| P3 | T3.2 发送链路加固 | ✅ DONE | 2026-11-09 | `0734803` | 5 条验收 grep 全绿；台账幂等 3 次重跑拦截 |
| P3 | T3.3 废弃 resend 脚本 | ⬜ PENDING | — | — | 依赖 T3.2 |
| P3 | T3.4 定时任务去模型依赖 | ⬜ PENDING | — | — | QA#1 |
| P3 | GATE-3 端到端不重发 | ⬜ PENDING | — | — | 连跑两次只发一次 |
| P4 | T4.1 run_root 隔离 + 横版命名统一 | ⬜ PENDING | — | — | **必做；GATE-3 后第一优先** |
| P4 | T4.2 去硬编码 | ⬜ PENDING | — | — | 依赖 T4.1 |
| P4 | T4.3 状态机归位 | ⬜ PENDING | — | — | 依赖 T4.2 |
| P4 | T4.4 横屏出厂治理 | ⬜ PENDING | — | — | QA#6#7，二次复发项 |
| P4 | T4.5 TTS 配置接线 | ⬜ PENDING | — | — | **v1.2 新增，P-3** |
| P4 | T4.6 审批渠道抽象 | ⬜ PENDING | — | — | **v1.2 新增，P-4** |
| P4 | T4.7 出片与发片解耦 | ⬜ PENDING | — | — | **v1.3 新增；G-D/G-E** |
| P5 | T5.1 幂等写入时机归位 | ⬜ PENDING | — | — | QA#5#10 |
| P5 | GATE-5 全链路静默失败扫描 | ⬜ PENDING | — | — | 静态扫描零告警 |

**当前进度**：**22/35**（P1、P2 完成 + T3.1/T3.2 完成），GATE-1 ✅ / GATE-2 ✅ PASS；下一步 T3.3 废弃 resend 脚本。

---

## 3. 目标与非目标

### 3.1 目标

- G1：流水线由 `pipeline.yaml` 单一文件声明，代码中不再有流程分支
- G2：同一物料/同一成片，**全局只推送一次**，任何重跑与人工救援都无法绕过
- G3：换模型只改 `.env`，流水线文件内不出现任何模型名
- G4：每步前后可挂 hook，hook 独立进程、JSON 进出、可单独测试
- **G5（v1.1）**：**换模型不再引发故障** —— token 预算、超时、JSON 能力由 `model_profile` 声明，切换前强制自检
- **G6（v1.1）**：**静默失败零容忍** —— 任何降级/跳过/兜底都必须留痕并告警，不允许"成功但没做事"
- **G7（v1.2）**：**编排内聚** —— 使用方只发最小物料（`title` + `body`/`source_url`），不改源码、不预拆字段、不关心输出路径，即可产出竖版与横版成片
- **G8（v1.2）**：**配置真生效** —— `.env` 与 `pipeline.yaml` 声明的每一项（音色、语速、审批渠道、产物名）都必须被代码真实读取；存在即生效，不存在即报错，禁止"写了没人读"的死键
- **G9（v1.3）**：**开箱即跑** —— 一条命令完成运行时自举（python/node/浏览器/字体/依赖），首次出片**不依赖联网下载**；离线与内网环境同样可跑
- **G10（v1.3）**：**投料有载体** —— 物料必须有一个明确的进站方式（CLI 路径 / stdin / 飞书文件），并可用一条命令自证通路；出片与发片可分离（`output_only`）

### 3.2 非目标

- N1：不重构 Remotion 渲染组件（T4.4 仅改封面数据注入与组件选择入口）
- N2：不重命名/迁移 179 个历史产物
- N3：不改 LLM prompt 文本内容（仅做模板化封装）
- N4：不改动发布/评论区的业务判定规则（只改失败处理与告警）
- **N5（v1.1）**：不在本方案内修复 QA#9 的摘要重复瑕疵（另立任务）

---

## 4. 设计摘要

### 4.1 分层

```
pipeline.yaml            声明层：步骤、契约、hooks、去重策略、模型画像
src/pipeline/runner.py   编排层：按序执行、状态流转、断点续跑
src/pipeline/hooks.py    执行层：hook 子进程调度（gate/transform/observe）
src/pipeline/contracts.py 校验层：产物存在性 + JSON Schema + 命名规范
src/llm/client.py        调用层（v1.1 新增）：唯一 LLM 出口，强制截断/空值即异常
src/db.py                状态层：任务状态机 + 指纹索引 + 发送台账
```

### 4.2 三层去重（防重发核心）

| 层 | 指纹 | 时机 | 作用 |
|---|---|---|---|
| L1 内容 | `sha256(normalize(title+slug+正文))` | intake 后 | 拦同一物料重投 |
| L2 视觉 | `md5(32×32 灰度帧)` ×3 | 渲染后 | 拦重渲染后内容相同的成片 |
| L3 台账 | `sha256(content_hash\|video_sha256\|account)` | **发送前** | 终局防线，同频道同片只发一次 |

L3 实现为 `INSERT ... ON CONFLICT DO NOTHING` 抢槽位：插入成功才允许发送。

> **归一化规则统一**（QA#11 沉淀）：剥离 `[xxR]` 类表情代码、去空白、统一标点、转小写、保留中英数字。L1 指纹与评论判重**共用同一函数**，避免两套规则漂移。

### 4.3 新增数据表

```sql
CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id       TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  account      TEXT NOT NULL,
  status       TEXT NOT NULL,
  run_root     TEXT NOT NULL,
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_run_content ON pipeline_runs(content_hash, account);

CREATE TABLE IF NOT EXISTS artifacts (
  task_id      TEXT NOT NULL,
  kind         TEXT NOT NULL,          -- vertical | landscape | cover | xhs
  path         TEXT NOT NULL,
  sha256       TEXT NOT NULL,
  frame_hash   TEXT,
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (task_id, kind)
);

CREATE TABLE IF NOT EXISTS send_ledger (
  dedupe_key    TEXT PRIMARY KEY,
  task_id       TEXT NOT NULL,
  channel       TEXT NOT NULL,
  chat_id       TEXT NOT NULL,
  video_path    TEXT NOT NULL,
  feishu_msg_id TEXT,
  sent_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- v1.1 新增：降级留痕（静默失败零容忍的落地表）
CREATE TABLE IF NOT EXISTS degraded_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id     TEXT NOT NULL,
  step_id     TEXT NOT NULL,
  hook_id     TEXT,
  kind        TEXT NOT NULL,          -- empty_result | truncated | skipped | fallback
  detail      TEXT,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 4.4 模型无关性契约（v1.1 核心）

**规则一：能力槽位必须带 `profile`。** 每个 `providers.*` 必须声明：

| 字段 | 含义 | 为什么必须有 |
|---|---|---|
| `max_tokens` | **总输出预算，含推理 token** | QA#2②：推理模型 250~450 tokens 被思考链吃掉 |
| `reasoning_reserve` | 预留给思考链的 token | 换推理模型时只需调这一个数 |
| `timeout_sec` | 单次调用超时 | QA#3：HF/arXiv 实测 20~25s，原设 5s 100% 超时 |
| `json_mode` | 是否支持强制 JSON 输出 | 决定是否需要 JSON 修复重试 |
| `min_output_chars` | 正文最小长度 | QA#9：摘要 34 字判为不达标 |
| `expected_latency_sec` | 期望延迟基线 | QA#2：切换后 12.2s，用于识别劣化 |

**规则二：调用方不得自设 `max_tokens`。** 只能通过 `uses: llm.main` 引用槽位；代码里出现 `max_tokens=` 字面量即视为违规（T2.5 静态扫描拦截）。

**规则三：换模型 = 换 profile + 跑自检。** 见附录 B SOP 与 T1.6。

### 4.5 静默失败零容忍（v1.1 核心）

所有降级路径**必须**满足三条：

1. **必须抛或在返回值里显式标记**，不允许 `except: pass` / `except: return 默认值`
2. **必须写 `degraded_events`**（`kind` ∈ `empty_result | truncated | skipped | fallback`）
3. **必须告警**：单任务降级 → 日志 ERROR；同类降级连续 2 次 → 飞书告警卡片

**明确禁止的三种反模式**（QA 实证均出现）：

| 反模式 | QA 出处 | 正确做法 |
|---|---|---|
| 分类异常兜底返回 `"D"` | #2① | 抛 `ClassificationError`，由上层决定重试或告警 |
| `if not items: continue` 静默跳过板块 | #3 | 输出 `⚠️ 板块为空` 并写 `degraded_events(kind=skipped)` |
| `finish_reason == length` 当作正常 | #2② | 抛 `TruncatedOutput`，携带实际 token 数 |

### 4.6 外部真值校验（v1.1 核心）

**原则：本地状态只是线索，判定必须回读外部真值。**

| 场景 | 真值来源 | 本地状态的角色 |
|---|---|---|
| 视频是否已发出 | 飞书 `message_id` + `send_ledger` | 辅助 |
| 视频画幅是否正确 | `ffprobe` 宽高 + 抽帧 OCR bbox 跨度 | 不采信 |
| 评论是否已回复 | 子楼层 API `/x/v2/reply/reply` 回读 | 辅助 |
| 封面是否贴合选题 | 抽帧 OCR 文本 vs `draft_script.json` 关键词 | 不采信 |

**写入时机铁律**（QA#10）：`seen` / `REPLIED` / `send_ledger` **一律在外部确认成功之后**才写。先写后发 = 永久漏发。

### 4.7 物料契约与内聚边界（v1.2 核心，P-2 依据）

**定义"内聚"的可执行判据**：使用方把物料交给流水线，**不改一行源码**即可拿到成片。

```text
使用方边界（只做这一件事）
  └── 发一份 manifest：{ title, body|source_url, account?, images? }

流水线边界（全部由本仓负责，使用方不需要知道）
  ├── 15 字段拆分        ← llm.main 扩写（expand_script 步，schema=DraftScriptPayload）
  ├── 分镜/口播/字幕      ← hook.expand_narration
  ├── 音色/语速           ← providers.tts.tech|news（T4.5 接线后真生效）
  ├── 竖版/横版渲染       ← builtin.render + render_landscape 唯一入口
  ├── 合规/敏感词/平台策略 ← dfa_filter + platform_policies
  ├── 出厂质检            ← landscape_guard（OCR bbox + 假数据断言）
  ├── 去重（内容/视觉/台账）← 三层指纹
  ├── 人工审批            ← approval.mode（T4.6 抽象后不绑渠道）
  └── 分发                ← send_ledger 抢槽位
```

**三条硬边界**：

| # | 规则 | 违反后的实测后果 |
|---|---|---|
| E1 | 物料字段只允许在 `manifest.schema.json` 里增删 | 无 schema 时新增字段靠口口相传，必漂移（§1.6 I2） |
| E2 | 任何产地/账号/音色/路径**不得**出现在调用方代码里 | 当前 4 处硬编码：`douyin-tech-main` / `news.hot-digest.local` / 音色 / `rate` |
| E3 | 任一产物名只允许在 `pipeline.yaml` 定义一次 | 当前横版命名三处不一致，分发步读不到文件（§1.6 I7） |

**跨模型适用性**：本契约与模型无关。换模型只影响 `providers.*.profile` 的预算参数，**不影响 manifest 字段、不要求使用方改物料格式**。

---

### 4.8 既有修复的接管原则（v1.2 核心，小品改动的处置依据）

> 本次优化的**起因就是这些故障**。小品已先行止血，因此这些改动**不回退、不推翻**，但要按本文件的契约重做。判定标准只有一条：**是否消除了"使用方/模型需要额外知道什么"**。

**四条接管规则**：

| 规则 | 含义 | 反例（小品现状） |
|---|---|---|
| **R1 止血 ≠ 达标** | 现象消失不代表机制修复。验收必须跑契约断言，不接受"看着正常" | A1–A3 断言下错版片能通过 |
| **R2 静默即缺陷** | 修复过程中出现的 `return null` / `continue` / 空兜底，一律按 `degraded_events` 处理 | 三卡 `if (!text) return null` |
| **R3 逻辑不得下沉到执行体** | 数据整形放 hook / 契约层，执行体只做调度 | `_post_to_card2()` 埋在发送函数 |
| **R4 双实现必须收敛** | 出现两套"唯一入口"时，保留合规的一套，另一套立即标记废弃并改调用方；禁止长期并存 | `render_landscape_bilibili.py` 与 `render_landscape.py` |

**处置矩阵（小品 4 个文件的结论）**：

| 文件 | 保留 | 需重做 | 归入任务 |
|---|---|---|---|
| `BilibiliLandscapeCover.tsx` | 去硬编码话术、改取真实分镜 | `if (!text) return null` → 告警；`[0,1,3]` 魔数 → 契约注入；`as any` → 类型化 | T4.4 |
| `run_publish_worker.py` | post → Card 2.0、`resp.code != 0` 告警 | 转换逻辑下沉 hook；删硬编码 chat_id 兜底；`msg_id` 落台账 | T3.2 |
| `landscape_guard.py` | OCR bbox ≥75% 判据（实测 0.904 有效） | 转 JSON stdin/stdout hook 协议；`expect_text` 比对加归一化；修陈旧 docstring（80%→75%） | T4.4 |
| `render_landscape.py` | 单入口 + 失败即抛（符合 §4.5） | props 默认读共享目录 → 由 runner 注入；产物名对齐契约；subprocess 加 timeout；废弃 `render_landscape_bilibili.py` | T4.1 + T4.4 |

**结论**：小品的修复是"把现象止住"，本方案要做的是"把机制立住"。两者不冲突，但**不能以"已经修过了"为由跳过 T3.2 / T4.4**。

### 4.9 交付面契约（v1.3 核心，G-A~G-E 的落地依据）

> 前三节管"内聚"（谁负责什么），本节管"交付"（东西怎么递进来、环境怎么备齐、片怎么出去）。判据：**新机器 + 一条命令 + 一份物料 = 一个成片。**

#### 4.9.1 运行时自举（T0.4）

| 层 | 内容 | 探测方式 | 缺失表现 |
|---|---|---|---|
| 二进制 | `ffmpeg` / `ffprobe` / `npx` / `tesseract` | `shutil.which()` | 渲染/质检直接崩 |
| Python | `>=3.10` + `edge-tts` / `openpyxl` 等 | `importlib.metadata.version` | TTS 静默无声 |
| Node | `renderer/node_modules` 存在 | `Path.exists()` | `npx remotion` 交互式安装，超时 |
| **浏览器** | `renderer/node_modules/.remotion` **或** `~/Library/Caches/remotion` | 两路径任一存在 | **`render` 首次自动下载 ~150MB，离线必失败** |
| 字体 | 渲染所需中文字体 | `fc-list` / 目录探测 | 文字变方框，OCR bbox 判据失效 |

**契约**：`requires` 必须声明上述五项；`bootstrap.py --ensure` 一次装齐并复检；`--dry-run` 打印探测表而不改环境。

#### 4.9.2 投料通道（T1.8）

三种载体，**共用同一份 schema 校验**，不得各写一套解析：

| 载体 | 触发方式 | 适用 | 输出 |
|---|---|---|---|
| `cli` | `runner --material <path.json>` | 本地/CI/定时 | 规范化 manifest |
| `stdin` | `cat m.json \| runner --material -` | 管道/Agent 调用 | 规范化 manifest |
| `feishu_file` | 飞书文件消息回调 → 落到 `inbox/<msg_id>.json` | 手机投料 | 规范化 manifest |

**铁律**：三者最终都必须产出**同一结构**的 manifest；`intake` 步只认 manifest，不认载体差异。`intake --selftest` 用一份最小样例把三条通路各跑一次。

#### 4.9.3 配置面收口（T1.9）

| 规则 | 说明 |
|---|---|
| C1 单一清单 | `.env.example` 的键集合 = 代码实读键集合，二者必须逐一对应，多了删、少了补 |
| C2 全键声明 | 代码实读的每个键都要在 `pipeline.yaml` 的 `providers` / `contract.config_binding` 中出现（含 Feishu 三键与 `SQLITE_DB_PATH`） |
| C3 死键处置 | 零引用键（当前 `REMOTION_DIR`）二选一：接线或删除，**不得留存** |
| C4 fail-fast | 必填键缺失/为空 → 启动第 0 步即退出，错误码 `EXIT_MISSING_CONFIG=78`，并打印缺失键名 |
| C5 密钥掩码 | 日志与报错中的 `*_KEY` / `*_SECRET` 一律脱敏 |

#### 4.9.4 出片 / 发片解耦（T4.7）

```yaml
dispatch:
  enabled: true          # false ⇒ 只出片，不发片
  mode: publish          # publish | output_only
```
- `output_only`：跳过 `approval` 与 `dispatch`，产物留在 `{run_root}` 并打印绝对路径清单
- 该模式下 **不得** 初始化任何平台登录态（不碰 CDP、不读 storage_state）
- `approval.params.mode` 必须有默认值（`auto`），无飞书凭据时按 §5 T4.6 降级 `cli` 并打印原因

---

## 5. 执行步骤（逐任务）

### P0 · 基线与盘点

#### T0.1 基线快照与分支隔离
- **状态**：⬜ PENDING
- **依赖**：无
- **执行**：
  ```bash
  cd ~/Downloads/workspace/team/project/autocontent-pipeline
  git checkout -b feat/pipeline-spec-enhancement
  mkdir -p .backup/20261109
  cp data/pipeline_state.db .backup/20261109/pipeline_state.db
  sqlite3 data/pipeline_state.db "select count(*), count(distinct entity_key) from content_tasks;" \
    | tee .backup/20261109/baseline.txt
  ls renderer/public/tasks | wc -l | tee -a .backup/20261109/baseline.txt
  ```
- **验收**：`.backup/20261109/pipeline_state.db` 存在且 `baseline.txt` 记录 `163|163` 与 `179`
- **预期输出**：`163|163` / `179`
- **回滚**：`git checkout main && rm -rf .backup`
- **失败处理**：备份不存在或行数不符 → 停止，不得进入 P1

#### T0.2 模型调用点与环境盘点
- **状态**：⬜ PENDING
- **依赖**：T0.1
- **QA 来源**：#4（策略开关过期、CDP 未启动）
- **执行**：扫描全仓，产出 `model_inventory.md`（项目根目录），必须包含四张表：
  1. **模型调用点表**：文件:行号 / 调用用途 / 当前 `max_tokens` / 是否支持 JSON / 超时
     ```bash
     grep -rn "max_tokens\|LLM_MODEL\|base_url\|ChatCompletion\|client.chat" --include=*.py src scripts | grep -v test
     ```
  2. **配置一致性表**：`.env` 中的 `LLM_MODEL_*` 与当前网关 `/v1/models` 实际可用模型列表比对
  3. **定时任务表**：每个 cron job 的 `pinned / unpinned`、`no_agent` 状态
     ```bash
     ls ~/.hermes/profiles/*/cron/jobs.json
     ```
  4. **环境开关表**：`platform_policies.yaml` 中所有 `freeze_until`（是否已过期未解冻）、CDP 端口监听状态
     ```bash
     grep -n "freeze_until\|status:\|enable_background_polling" config/platform_policies.yaml
     lsof -nP -iTCP:9222 -sTCP:LISTEN || echo "9222 未监听"
     ```
- **验收**：`model_inventory.md` 四张表齐备；每张表至少 1 行数据；`LLM_MODEL_*` 与网关模型列表的差异项被显式标注
- **预期输出**：文档存在，差异项 ≥1 条（已确认 `gemini-3.8-flash` vs `deepseek-v4.1-flash`）
- **回滚**：`rm model_inventory.md`
- **注意**：本任务**只盘点不修改**。发现的问题进 T0.2 的"待办"列，由后续任务处理

#### T0.3 工作区卫生与提交边界（v1.2 新增）
- **状态**：⬜ PENDING
- **依赖**：T0.1
- **背景**：工作区现存 3 个 `*.bak*`、4 个一次性脚本、2 个未跟踪的核心修复文件（小品），若无明确边界，提交时极易把一次性脚本混入主线，或误删未跟踪的核心修复
- **改动**：`.gitignore`、`scripts/_deprecated/`（新建目录）
- **执行**：
  1. `.gitignore` 追加：
     ```gitignore
     *.bak
     *.bak_*
     *.bak.*
     *.tmp
     .backup/
     ```
  2. 建立 `scripts/_deprecated/`，并在其中放 `README.md` 说明"此处脚本不再维护，能力已并入 runner，禁止再新增"
  3. **固化提交边界**（写进本文件 §6）：
     | 类别 | 处置 |
     |---|---|
     | 小品的核心修复（`landscape_guard.py` / `render_landscape.py` / 两个已改文件） | **入主线**，由 T3.2/T4.4 接管重做 |
     | 一次性救火脚本（`check_douyin_works.py` / `dispatch_douyin_only.py` / `submit_douyin_code.py` / `generate_rigorous_holiday.py` / `resend_sherlock_approval.py`） | **不入主线**，移入 `scripts/_deprecated/` 或保持未跟踪 |
     | `*.bak*` | 忽略，不入库 |
- **验收**：
  ```bash
  git status --porcelain | grep -E "\.bak" && echo "VIOLATION: bak 未忽略" || echo "OK: bak 已忽略"
  test -d scripts/_deprecated && echo "OK: _deprecated 已建立"
  git status --porcelain | wc -l
  ```
- **预期输出**：`OK: bak 已忽略` / `OK: _deprecated 已建立`，未跟踪条目数显著下降
- **回滚**：`git checkout .gitignore && rm -rf scripts/_deprecated`
- **注意**：本任务**不得** `git add` 一次性脚本

#### T0.4 运行时自举 `scripts/bootstrap.py`（v1.3 新增，G-A）
- **状态**：✅ DONE（2026-11-09，脚本段；接线段随 T1.1/T1.3）
- **依赖**：T0.1
- **缺口来源**：§1.7 **G-A** —— `renderer/node_modules/.remotion` 与 `~/Library/Caches/remotion` **双双不存在**，首次渲染会联网下载 Chrome Headless Shell（~150MB），离线环境必失败
- **实测结论（2026-11-09）**：
  - `npx remotion browser ensure` → `Has browser at /Applications/Google Chrome.app/Contents/MacOS/Google Chrome`，**未触发下载**
  - 真实渲染链路验证：`npx remotion compositions src/index.ts` 列出 7 个 composition；`npx remotion still … CoverStill` 输出 1.38MB PNG ✅
  - 即：本机阻断项已解除，但当前为 **system-fallback**（依赖系统 Chrome），迁移到无 Chrome 机器仍需 hermetic 预置（RK13）
- **改动**：新增 `scripts/bootstrap.py`（约 390 行）；`pipeline.yaml` 的 `requires` 段（`browser` + `fonts`）**已随 T1.1 落地接线**；`runner` 启动自检待 T1.3
- **执行**：
  1. `bootstrap.py` 探测五层环境（对齐 §4.9.1）：二进制 / Python 包 / Node 依赖 / **浏览器** / 中文字体
  2. `--dry-run`：只打印探测表（LAYER / ITEM / EXPECT / ACTUAL / STATUS / NOTE），**不改环境**
  3. `--ensure [--only 层名]`：缺什么补什么（浏览器与 node 依赖可自动；二进制/字体给出人工命令）
  4. 浏览器判定采用两级：**hermetic**（项目内 `.remotion`，离线可分发，优先）→ **system-fallback**（系统 Chrome/Chromium，可用但迁移需重装）→ MISSING
  5. `requires` 段补声明（接线段）：
     ```yaml
     requires:
       python: ">=3.10"
       binaries: [ffmpeg, ffprobe, npx, tesseract]
       renderer: "remotion>=4"
       browser: {engine: chrome-headless-shell, probe: ["renderer/node_modules/.remotion", "~/Library/Caches/remotion"]}
       fonts: [Noto Sans CJK SC]
     ```
  6. runner 启动第 0 步调用 `bootstrap.py --dry-run`，缺项直接拒绝运行（与 T1.9 的 fail-fast 合并出口）
- **验收**：
  ```bash
  python3 scripts/bootstrap.py --dry-run; echo "EXIT=$?"
  python3 scripts/bootstrap.py --dry-run --json | python3 -c "import json,sys;d=json.load(sys.stdin);print('OK',d['ok'],'MISSING',d['missing'])"
  ```
- **预期输出**：探测表 **20/20 PASS**，`BOOTSTRAP: 20/20 PASS`，`EXIT=0`；JSON 中 `OK True MISSING []`
- **回滚**：`rm scripts/bootstrap.py && git checkout pipeline.yaml src/pipeline/runner.py`
- **失败处理**：`--ensure` 若因网络失败 → 标记 `⛔`，记录缺失项，**禁止在文档里写"应该会自动装"糊过去**

---

### P1 · 声明层与模型契约（纯新增，零破坏）

#### T1.1 落 `pipeline.yaml`
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T0.1
- **改动**：新增 `pipeline.yaml`（项目根目录，全文逐字取自附录 A）
- **内容要点**：
  - `schema_version: "1.0"`
  - `contract`：`isolation` / `artifact_naming` / `material_contract` / `config_binding` / `runtime_bootstrap` / `intake_carriers` / `model_decoupling` / `artifact_ownership` / `deduplication` / **`model_switch`**（共 11 段）
  - `providers`：**六个**能力槽位（v1.3 语境）—— `llm.main` / `llm.fast` / `tts.tech` / `tts.news` / `feishu` / `db`，每个带 **`profile`**（LLM 两槽位六项齐备；其余先声明 `timeout_sec`，T1.4 补标定）
  - 7 个步骤 + 每步前后 hooks
  - `hooks` 注册表 **22 项**（v1.1 记 21，v1.2-接管新增 `hook.card_render`；已校验「注册=引用」零孤儿零缺失）
  - `requires` 含 `browser` + `fonts`（T0.4 的声明段在此接线完成）
- **验收**：
  ```bash
  python3 -c "import yaml;d=yaml.safe_load(open('pipeline.yaml'));print(d['schema_version'],len(d['steps']),len(d['hooks']))"
  ```
- **预期输出**：`1.0 7 22`
- **实测输出（2026-11-09）**：`1.0 7 22` ✅；另校验：步骤体/钩子/provider 中**零模型名**、14 个 env 键全为键名、`hooks` 注册与引用完全一致
- **回滚**：`rm pipeline.yaml`
- **注意**：YAML 流式映射中 `key:{` 缺空格会解析失败，一律写 `key: {`

#### T1.2 契约校验器 `src/pipeline/contracts.py`
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T1.1
- **改动**：新增 `src/pipeline/__init__.py`（包声明）+ `src/pipeline/contracts.py`（约 270 行）
- **必须实现**：
  1. `load_pipeline(path)`：解析 + `schema_version` 校验
  2. `validate_structure(cfg)`：步骤 id 唯一、hook 引用可解析、`uses` 槽位存在、**`kind: llm` 的 provider 必须含 `profile` 六字段齐全，`kind: tts` 至少含 `timeout_sec`**
  3. `assert_no_model_literal(cfg)`：扫描 `forbidden_literals`
  4. `verify_produces(step, run_root)`：逐个校验声明产物存在且非空
  5. `assert_no_caller_max_tokens(paths)`：**静态扫描代码中是否出现 `max_tokens=` 字面量**（QA#2②，白名单仅含 `src/llm/client.py`）
- **验收**：
  ```bash
  python3 -c "
from src.pipeline.contracts import load_pipeline, validate_structure, assert_no_model_literal
c=load_pipeline('pipeline.yaml'); e=validate_structure(c); assert_no_model_literal(c)
print('errors:', e)"
  ```
- **预期输出**：`errors: []`
- **实测输出（2026-11-09）**：`errors: []` ✅
- **实现补充（v1.3 落地时增强）**：
  - 除上述 5 项外，另校验：`contract` 六个核心段齐全、产物**唯一归属**（同一产物不得由两步声明）、`reads` 必须能溯源到上游 `produces`/`manifest.json`、注册但未被任何 step 引用的 hook 视为错误
  - `collect_warnings(cfg)` 单独返回非阻断提示（hook impl 未实现 20 条、profile 预算矛盾）
  - `assert_no_caller_max_tokens` 改用 **AST 解析**（`ast.Call.keywords` 中 `arg == "max_tokens"`），避免把注释/文档串里的说明文字误判为违例；当前实测命中 `comment_funnel.py:151/214/253` **3 处真实违例**（待 T1.5 收口）
  - 负向自测：注入 5 类缺陷（id 重复 / 未注册 hook / 幽灵 reads / profile 缺字段 / 孤儿 hook）**全部被捕获**
- **回滚**：`rm -rf src/pipeline/contracts.py src/pipeline/__init__.py`

#### T1.3 薄 Runner `src/pipeline/runner.py`
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T1.2
- **改动**：新增 `src/pipeline/runner.py`（约 330 行）
- **必须实现**：
  - CLI：`python -m src.pipeline.runner --material <path> [--account X] [--dry-run] [--run-id ID] [--resume]`
  - `--dry-run`：只做结构校验 + 产物对账 + 打印执行计划，**不调用任何 LLM**
  - **启动自检**（QA#4）：按 `requires` 逐项检查 `python / ffmpeg / ffprobe / npx` 版本与可执行性；`binaries` 缺失立即拒绝运行
  - 顺序执行 `steps`，每步前后调用 hook
  - 每步结束写 `pipeline_runs.status`，支持 `--resume`
  - 产物根：`output/{date}/{run_id}`
- **验收**：
  ```bash
  python3 -m src.pipeline.runner --material /dev/null --dry-run; echo "EXIT=$?"
  ```
- **预期输出**：逐步打印 7 个步骤 + 启动自检表，末尾 `DRY RUN OK`，`EXIT=0`
- **实测输出（2026-11-09）**：`DRY RUN OK` / `EXIT=0` ✅（启动自检 8 行全 OK；执行计划 7 步带 reads/produces/hooks；产物对账走「run_root 尚未创建」分支）
- **退出码实测（5 项负向自测全通过）**：
  | 场景 | 期望 | 实测 |
  |---|---|---|
  | 缺失二进制 `definitely_not_a_cmd` | 69 | **69** + `拒绝运行：1 项运行时缺失` |
  | step id 重复 | 2 | **2** + `✗ steps 中 id 重复: intake` |
  | 真实运行（无 `--dry-run`） | 64 | **64** + `拒绝执行：builtin.intake 尚未实现（T1.8）` |
  | 物料非合法 JSON | 2 | **2** + `物料不是合法 JSON` |
  | `--resume` 且状态表不存在 | 0 | **0** + `pipeline_runs 表未建立（T2.2），从头开始` |
  | `--resume` 且 3 步已完成（临时库实测） | 从第 4 步起 | **从 render_vertical 继续**，计划表前 3 步标 `✓` |
- **诚实边界**：真实运行一律在首个 hook 前拦截并返回 64，**不做「看起来跑通」的假执行**；`pipeline_runs` 写入在表缺失时静默降级为提示（T2.2 建表后自动生效）
- **回滚**：`rm src/pipeline/runner.py`

#### T1.4 `model_profile` 能力契约（v1.1 新增）
- **状态**：✅ DONE（2026-11-09 实测标定）
- **依赖**：T1.1
- **QA 来源**：#2② token 预算、#3 超时、#9 摘要过短
- **改动**：`pipeline.yaml` 的 `llm.main` / `llm.fast` 两段 `profile` 按实测填值（附录 A 同步）
- **实测数据（2026-11-09，`deepseek-v4.1-flash` @ `127.0.0.1:8317`，共 11 次真实调用）**：

  | 任务形态 | `max_tokens` | `reasoning_tokens` | `completion_tokens` | `finish` | 可见输出 | 延迟 |
  |---|---|---|---|---|---|---|
  | 最小「只回复 OK」×3 | 1024 | 9 / 0 / 0 | 12 / 3 / 3 | stop | 2 字 | 1.3–1.8s |
  | **分类 A/B/C/D** | **1024** | **1024（顶满）** | 1024 | **length** | **0 字** ❌ | 9.6s |
  | 分类 A/B/C/D | 2048 | 1099 | 1102 | stop | 1 字 ✅ | 10.1s |
  | JSON 扩写 150–200 字 ×3 | 4096 | 1146 / 1182 / 1259 | 1286 / 1299 / 1369 | stop | 198–246 字 ✅ | 10.2–10.8s |
  | 长正文 700–900 字 | 4096 | 1023 | 1529 | stop | 864 字 ✅ | 16.5s |

  **结论一（关键修正）**：QA 记录的 `reasoning 250~450` **已严重过期**。当前推理预算常态 **1000~1260**，即使是「判一个字母」的分类任务也要 ~1100。按 512 预留 → **必然截断**。
  **结论二（QA#2② 现场复现）**：分类任务 `max_tokens=1024` 时 reasoning 吃满 1024，`finish=length`、`content` 为空 —— 这正是"评论全判 D / 静默丢弃"的物理成因。
  **结论三（真实 draft 体量）**：仓内 3 份历史 `aligned_render.json` 实测 6115–6657 字符 ≈ **3700–4000 visible token**，JSON 键名额外占约 40–100 token。

- **最终标定值**：

  | 槽位 | `max_tokens` | `reasoning_reserve` | 推导 | `timeout_sec` | `expected_latency_sec` | `json_mode` | `min_output_chars` |
  |---|---|---|---|---|---|---|---|
  | `llm.main` | **8192** | **2560** | 2560（reasoning 实测上界 2215 +15% 余量）+ 5632 visible（真实 draft ≈4000，留 40% 峰值余量） | 120 | 25 | true | 80 |
  | `llm.fast` | **2048** | **1536** | 1536（实测上界 1099 +40%）+ 512（分类/短回复） | 120 | 20 | false | 1 |

  `reasoning_reserve` 取实测上界向上取整，三轮标定逐次上调：T1.4 首轮 1259 → 1536；T1.6 自检 1840 → 2048；同脚本复跑 **2215** → 最终 **2560**。
  **⚠️ 关键教训**：同一 JSON prompt 的 reasoning 在 1056~2215 之间波动（**2.1 倍**），`llm.fast` 同类提示在 40~1099 之间（**27 倍**）。**单次采样定预算必然偶发截断**，必须按多轮最坏值 + 余量。

- **验收**：
  ```bash
  python3 -c "
import yaml;d=yaml.safe_load(open('pipeline.yaml'))
need={'max_tokens','reasoning_reserve','timeout_sec','json_mode','min_output_chars','expected_latency_sec'}
for k,v in d['providers'].items():
    if v.get('kind')=='llm': print(k,'MISSING',need-set(v.get('profile',{})))
    else: print(k,'timeout_ok',bool(v.get('profile',{}).get('timeout_sec')))"
  ```
- **预期输出**：`llm.main MISSING set()` / `llm.fast MISSING set()` / `tts.tech timeout_ok True`
- **实测输出**：`llm.main MISSING set()` / `llm.fast MISSING set()` / `tts.tech|tts.news|feishu|db timeout_ok True`（6 槽位全过）✅；附录 A 与 `pipeline.yaml` 的 providers/steps/hooks/contract **逐项相等**
- **回滚**：`git checkout pipeline.yaml`
- **失败处理**：若无 `LLM_API_KEY` 或网关不可达 → 标记 `⛔`，用本次实测上界（reasoning 1259）作为保守下界先行填入，待网关恢复后复核
- **复用要求**：换模型必须重跑本节标定方法（附录 B SOP 第 3 步），**不得沿用本次数值**

#### T1.5 LLM 统一调用层 `src/llm/client.py`（v1.1 新增）
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T1.4
- **QA 来源**：#2① #2② #3 #9（全部调用层问题）
- **改动**：新增 `src/llm/__init__.py` + `src/llm/client.py`（约 300 行）；改造 `src/safety/comment_funnel.py`（3 处调用点）、`src/hotspot_extractor.py`（1 处）、3 个轮询脚本调用点
- **必须实现**（全部落地）：
  ```python
  class TruncatedOutput(Exception): ...      # finish_reason == "length"
  class EmptyCompletion(Exception): ...       # content 为空/纯空白
  class OutputTooShort(Exception): ...        # len < profile.min_output_chars
  class ClassificationError(...): ...         # 替代「异常即判 D」
  class ConfigError(...): ...                 # 槽位/env 缺键即失败

  class LLMClient:
      def __init__(self, slot: str, cfg: dict = None, *, client=None): ...   # 从 pipeline.yaml 槽位读 profile
      def complete(self, messages, **kw) -> str:
          # 1. max_tokens 只能来自 profile，调用方传了 → TypeError
          # 2. finish_reason == length → TruncatedOutput(实际 completion_tokens)
          # 3. content 空 → EmptyCompletion
          # 4. len < min_output_chars → OutputTooShort
          # 5. 任何失败先写留痕再抛（不吞）
          # 6. 记录 reasoning_tokens / 耗时 / 槽位名
      def complete_json(...) -> dict          # JSON 解析失败即抛，不返回空 dict
  ```
- **改造要求**：`comment_funnel` 三处调用（classify / reply / seed）改为 `self.llm.complete(..., task=...)`；**已删除**本地 `max_tokens=1024` ×3；**已删除** `except → 返回 "D"` 兜底，改为 `raise ClassificationError`
- **验收**：
  ```bash
  # ① 调用方不得自设 max_tokens（改用 AST 判定，见下方说明）
  python3 -c "from src.pipeline.contracts import assert_no_caller_max_tokens as f; v=f(); print('VIOLATION' if v else 'OK: 无调用方自设 max_tokens')"
  # ② 不得有静默判 D 兜底
  grep -rn 'return "D"' src/safety/comment_funnel.py && echo "VIOLATION" || echo "OK: 无静默判D兜底"
  ```
- **预期输出**：两行均 `OK:`
- **实测输出（2026-11-09）**：`OK: 无调用方自设 max_tokens` / `OK: 无静默判D兜底` ✅
- **⚠️ 验收命令修正**：原命令 `grep -rn "max_tokens" --include=*.py src | grep -v "src/llm/client.py"` 会误伤 `contracts.py` —— 因为**校验器必须写出字段名 `max_tokens` 才能校验它**（`LLM_PROFILE_FIELDS`）。已改为调用 `assert_no_caller_max_tokens()`（AST 精确匹配 `Call(keywords=max_tokens)`），语义更严且无误报。
- **异常路径实测（6 项，全通过）**：
  | 注入条件 | 期望 | 实测 |
  |---|---|---|
  | 调用方传 `max_tokens=99` | `TypeError` | **TypeError** + "预算只能来自 providers.llm.fast.profile" |
  | `finish_reason=length` | `TruncatedOutput` | **TruncatedOutput** + 实际 2048/2048 |
  | `content="   "` | `EmptyCompletion` | **EmptyCompletion** |
  | main 槽位输出 2 字 | `OutputTooShort` | **OutputTooShort**（2 < 80） |
  | 槽位 env 键不存在 | `ConfigError` | **ConfigError**（证明**不再回落** `gemini-3.8-flash`） |
  | profile 缺 `reasoning_reserve` | `ConfigError` | **ConfigError** |
- **真实调用实测**：
  | 调用 | 结果 | reasoning | visible | 延迟 |
  |---|---|---|---|---|
  | `level2_classify`（严格枚举分类提示） | `A` | 40 | 3 | 1.7s |
  | `generate_seed_comment` | 59 字评论 | — | — | — |
  | `llm.main.complete_json`（JSON 扩写） | 126 字 body | 1056 | 97 | 8.9s |
- **重要发现（修正 T1.4 的表述口径）**：`reasoning_tokens` **强烈依赖 prompt 形态** —— 自由问答可冲到 1024~1259（截断），严格枚举分类只需 40。因此 `reasoning_reserve=1536` 是按**最坏观测值**兜底，不是"每次都烧 1500"。T1.4 表中数字应理解为「最坏情形上界」。
- **附带加固**：
  - `LLMClient` 内部幂等 `load_dotenv`，独立调用也能取到 env（不依赖调用方先加载）
  - 新增 `process_comment_safe()`：长驻轮询脚本用它，LLM 故障时返回 `classification=None` + `reason=LLM_DEGRADED:...`（**不伪装成 D**）；已迁移 `poll_xhs_comments.py`、`poll_douyin_comments.py`、`run_comment_cron.py` 三个真实调用点（`poll_bilibili_comments.py` / `poll_channels_comments.py` 本就有 try 兜底）
  - 留痕双写：`data/degraded_events.jsonl` 必写；`degraded_events` 表存在时同时入库
- **回滚**：`git checkout src/safety/comment_funnel.py src/hotspot_extractor.py scripts/poll_xhs_comments.py scripts/poll_douyin_comments.py scripts/run_comment_cron.py && rm -rf src/llm/`
- **失败处理**：改造后评论分类实测若出现 `TruncatedOutput` → 说明 `max_tokens` 仍不足，回 T1.4 上调后重验，**禁止在调用点硬编码兜底**

#### T1.6 模型切换自检脚本 `scripts/model_switch_check.py`（v1.1 新增）
- **状态**：✅ DONE（2026-11-09，含预算回调）
- **依赖**：T1.5
- **QA 来源**：#1 #2（切换即事故）
- **改动**：新增 `scripts/model_switch_check.py`（约 300 行）
- **必须实现**（全部落地）：对每个能力槽位跑一次**真实最小调用**，断言：
  1. 调用成功（无 `unknown provider`）
  2. `content` 非空
  3. `finish_reason != "length"`
  4. `json_mode` 槽位的返回可被 `json.loads` 解析
  5. 延迟 ≤ `expected_latency_sec × 2`
  6. 输出长度 ≥ `min_output_chars`

  另对 `kind: tts` 槽位：真实合成 6 字短句，断言音频字节 > 0 且延迟 ≤ `timeout_sec`。
- **CLI**：`python3 scripts/model_switch_check.py [--slot llm.main]... [--dry-run] [--json]`
  - `--dry-run`：只打印槽位与 profile 摘要，不发请求
  - `--json`：机器可读（表头自动改走 stderr，保证 stdout 是纯 JSON）
  - 退出码：`0` 全通过 / `1` 有失败 / 未知槽位 `1`
- **验收**：
  ```bash
  python3 scripts/model_switch_check.py --dry-run; echo "EXIT=$?"
  python3 scripts/model_switch_check.py;            echo "EXIT=$?"
  ```
- **实测输出（2026-11-09）**：
  - `--dry-run` → 打印 **4 个槽位**及 profile 摘要，`DRY RUN OK`，`EXIT=0`
  - 真实自检 → **4/4 PASS**，`EXIT=0`：

    | 槽位 | model | 延迟 | 结果 | 明细 |
    |---|---|---|---|---|
    | `llm.main` | `deepseek-v4.1-flash` | 14.4s | PASS | 162 字 / reasoning 2215 / visible 96（上限 50s） |
    | `llm.fast` | `deepseek-v4.1-flash` | 2.6s | PASS | 11 字 / reasoning 179（上限 40s） |
    | `tts.tech` | `zh-CN-YunxiNeural @ +10%` | 1.4s | PASS | 音频 13968 字节 |
    | `tts.news` | `zh-CN-XiaoxiaoNeural @ +10%` | 1.3s | PASS | 音频 11376 字节 |

  - `--slot no.such` → `EXIT=1`；`--slot llm.fast` → `EXIT=0`（单槽位可用）
- **⚠️ 与原描述的差异**：原写「3 个槽位」是 v1.1 口径，v1.2 新增 `tts.news` 后为 **4 个**（2×llm + 2×tts）。`feishu`/`db` 属 `saas`/`storage`，不参与模型自检。
- **自检直接抓到的问题（并已修正）**：首轮 `llm.main` 实测 `reasoning=1840`，超出当时 `reasoning_reserve=1536`；复跑更高到 **2215**。说明 `max_tokens=6144` 的可见预算只剩 4304，离真实 draft 需求 4000 仅 7% 余量 → 已回调为 `max_tokens 8192 / reasoning_reserve 2560`（visible 5632，余量 40%）。**这是自检脚本的真正价值：它在上线前暴露了预算过紧。**
- **回滚**：`rm scripts/model_switch_check.py && git checkout pipeline.yaml`
- **强制**：写进附录 B，任何模型变更前必须 `EXIT=0` 才能继续

#### T1.7 物料契约 `manifest.schema.json`（v1.2 新增，P-2）
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T1.1
- **缺口来源**：§1.6 **I1 / I2** —— 全仓无 schema 文件，投料必须改源码
- **目标**：使用方**只发 3 个字段**即可跑通全链路，其余 15 字段由 `llm.main`（`expand_script` 步）扩写
- **改动**：新增 `manifest.schema.json`（项目根，JSON Schema Draft-07）+ `src/pipeline/manifest.py`（约 200 行）
- **最小物料契约**（已落盘）：

  | 字段 | 必填 | 类型 | 约束 | 说明 |
  |---|---|---|---|---|
  | `title` | ✅ | string | 4–80 字 | 选题标题，原样可用 |
  | `body` | ⚠️ 二选一 | string | 20–4000 字 | 原文/摘要，供扩写取材 |
  | `source_url` | ⚠️ 二选一 | string(url) | 必含 `http` | 来源链接，`body` 缺失时由抓取补齐 |
  | `account` | ❌ | string | 2–40 字 | 目标账号，缺省取 `inputs.account.default` |
  | `images` | ❌ | array[string] | ≤3 项 | 实景截图，缺失则由渲染层生成 UI 卡 |
  | `task_id` | ❌ | string | ≤80 字 | 可选：外部指定任务 ID |

  > **硬约束**：`body` 与 `source_url` 至少存在 1 个（`anyOf`），否则 `check_material` 步直接 `block`，禁止让模型凭空编造事实。
- **执行**（全部落地）：
  1. `manifest.schema.json`：`required: [title]` + `anyOf: [{required:[body]},{required:[source_url]}]`
  2. `src/pipeline/manifest.py`：`load_manifest(path|dict|"-")` / `validate(m) -> list[str]` / `require_valid(m)` / `Manifest.to_prompt_block()`
     - **schema 是唯一事实来源**：校验器读取 schema 并实现其用到的关键字子集（required / anyOf / type / minLength / maxLength / pattern / maxItems / minItems / items / enum），**不复制规则、不引第三方依赖**（环境无 `jsonschema`，不为 5 字段契约加包）
  3. `intake` 步只接受 manifest（声明层已在 T1.1 落地；执行体 `builtin.intake` 的载体分发由 **T1.8** 实现）
  4. `expand_script` 的 `hook.material_bundle` 用 `Manifest.to_prompt_block()` 把**整份物料**打包进 prompt（含「严禁编造」硬约束），不再由调用方预拆 15 字段
  5. **runner 已接线**：`--material` 非 `/dev/null` 时按契约校验，不合法直接 `EXIT=2` 并打印逐条错误
- **验收**：
  ```bash
  python3 -c "import json;j=json.load(open('manifest.schema.json'));print('REQ',j['required']);print('ANYOF',bool(j.get('anyOf')))"
  printf '{"title":"测试选题","source_url":"https://example.com/a"}' > /tmp/m1.json
  python3 -c "from src.pipeline.manifest import load_manifest,validate;print('MIN_OK',validate(load_manifest('/tmp/m1.json')))"
  printf '{"title":"x"}' > /tmp/m2.json
  python3 -c "from src.pipeline.manifest import load_manifest,validate;e=validate(load_manifest('/tmp/m2.json'));print('REJECT',bool(e))"
  ```
- **预期输出**：`REQ ['title']` / `ANYOF True` / `MIN_OK []` / `REJECT True`
- **实测输出（2026-11-09）**：`REQ ['title']` / `ANYOF True` / `MIN_OK []` / `REJECT True` ✅
- **9 例契约矩阵实测（9/9 符合预期）**：
  | 用例 | 期望 | 实际 |
  |---|---|---|
  | 仅 `source_url` | PASS | PASS |
  | 仅 `body` | PASS | PASS |
  | 缺 `title` | REJECT | REJECT（缺必填字段 title） |
  | `title` 1 字 | REJECT | REJECT（1 < minLength 4） |
  | `body`/`source_url` 全缺 | REJECT | REJECT（需至少满足其一） |
  | `source_url` 非 http | REJECT | REJECT（不匹配 `^https?://`） |
  | `body` 2 字 | REJECT | REJECT（2 < minLength 20） |
  | `images` 4 项 | REJECT | REJECT（4 > maxItems 3） |
  | `images` 含非字符串 | REJECT | REJECT（类型应为 string，实际 int） |
- **runner 集成实测**：`--material /tmp/m1.json --dry-run` → `DRY RUN OK / EXIT=0`；`--material /tmp/m2.json` → 打印 `物料不符合 manifest.schema.json：需至少满足其一 → body 或 source_url；title 长度 1 < 4`，`EXIT=2`；`--material /dev/null` → `EXIT=0`（干跑不受影响）
- **回滚**：`rm manifest.schema.json src/pipeline/manifest.py && git checkout src/pipeline/runner.py`
- **不做**：不新增字段就不改 schema —— **任何新物料字段必须先改本 schema**（对齐 §5 变更纪律）

#### T1.8 投料通道实现（v1.3 新增，G-C）
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T1.7（schema）、T1.3（runner 入口）
- **缺口来源**：§1.7 **G-C** —— `intake` 步 `run: builtin.intake` 是空壳，全文未定义"物料怎么递进来"
- **目标**：三种载体，**同一份 schema 校验**，产出结构完全一致的 manifest（§4.9.2）
- **改动**：新增 `src/pipeline/intake.py`（约 230 行）；`src/pipeline/runner.py`（`--material` 载体识别 + `--inbox`）
- **执行**（全部落地）：
  1. CLI 参数统一：`--material <path>` / `--material -`（stdin） / `--material feishu`（轮询投料箱，`--inbox` 可指定目录）
  2. 三个 loader 各自只做**取字节**，取到后一律走 `manifest.load_manifest()` + `validate()`：
     ```python
     raw = loaders[carrier]()          # 只负责拿到 JSON
     m = load_manifest(raw)            # 统一解析（唯一入口）
     errs = validate(m)
     if errs: raise ManifestInvalid(errs, carrier)   # 不静默、不补默认值
     ```
  3. `feishu` 载体：飞书文件消息落到 `inbox/<msg_id>.json`，处理完移到 `inbox/.done/<msg_id>.json`，**同一 `msg_id` 二次出现直接跳过**（防重放）
  4. `--selftest`：内置最小样例，三通路各跑一次，断言 `canonical()`（键排序+紧凑分隔符）**字节级一致**，并额外断言「重放被拦」「非法物料被拒」
  5. `msg_id` 属载体元数据，产出前从 raw 中剥离，保证三载体结构严格一致
- **验收**：
  ```bash
  printf '{"title":"投料自检","source_url":"https://example.com/x"}' > /tmp/min.json
  python3 -m src.pipeline.runner --material /tmp/min.json --dry-run; echo "C1=$?"
  cat /tmp/min.json | python3 -m src.pipeline.runner --material - --dry-run; echo "C2=$?"
  python3 -m src.pipeline.intake --selftest; echo "C3=$?"
  ```
- **预期输出**：`C1=0` / `C2=0` / `C3=0`，且 selftest 打印 `IDENTICAL True`（三载体产出等价）
- **实测输出（2026-11-09）**：`C1=0` / `C2=0` / `C3=0` ✅
  ```
  cli     : /tmp/min.json → 57 bytes
  stdin   : stdin        → 57 bytes
  feishu  : .../msg_001.json → 57 bytes (msg_id=msg_001)
  IDENTICAL True   REPLAY_BLOCKED True   INVALID_REJECTED True   SELFTEST PASS
  ```
  feishu 载体端到端：首轮 `--material feishu --inbox /tmp/inbox_t` → `DRY RUN OK / EXIT=0`，文件移入 `.done/`；把同一文件再放回去重放 → `EXIT=2`（无未处理物料，不重复出片）
- **回滚**：`rm src/pipeline/intake.py && git checkout src/pipeline/runner.py`
- **不做**：不为任一载体开后门式字段补全（缺字段一律 `ManifestInvalid`）

#### T1.9 配置面收敛（v1.3 新增，G-B）
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T1.1（`pipeline.yaml`）、T1.3（runner 启动流程）
- **缺口来源**：§1.7 **G-B** —— 12 个 `.env` 键里 `REMOTION_DIR` **零引用**（死键）；`FEISHU_APP_ID`(2)/`FEISHU_APP_SECRET`(2)/`FEISHU_APPROVAL_CHAT_ID`(8)/`SQLITE_DB_PATH`(3) 被代码读取却**未在 `pipeline.yaml` 声明**；且**无 fail-fast**
- **改动**：新增 `.env.example`、`src/pipeline/config.py`（约 220 行）、`scripts/audit_config.py`；`pipeline.yaml`（`config_binding.required_env` / `optional_env`）；`.env`（删除死键）
- **执行**（全部落地）：
  1. `.env.example`：键集合 = 代码实读键集合，分「必填 8 / 可选 9」两段并逐条注释；**不含 `REMOTION_DIR`**
  2. 补声明：`providers.feishu`（`app_id`/`app_secret`/`approval_chat_id`）+ `providers.db`（`path`）在 T1.1 已声明；本次在 `config_binding` 增加 `required_env`（8 键）与 `optional_env`（11 键），使「声明即生效」可被机器判定
  3. 死键处置：`REMOTION_DIR` 全仓 `.py` 零引用 → **已从 `.env` 删除**（备份 `.env.bak.t19`），`.env.example` 不再出现
  4. fail-fast：`config.load_required(cfg)` 作为 runner **第 0 步**（在契约/自检之前），缺失即 `MissingConfig` → `EXIT=78` 并打印 `missing: KEY1, KEY2`
  5. 掩码：`*_KEY`/`*_SECRET`/`*_TOKEN`/`*_PASSWORD`/`*_COOKIE`/`*_CT0` 输出为 `ab****yz`
  6. 巡检工具 `scripts/audit_config.py`：三面比对（代码实读 / pipeline 声明 / `.env` 实际），输出 `DEADKEY / UNDECLARED / MISSING_REQUIRED`
- **验收**：
  ```bash
  grep -rn "REMOTION_DIR" --include=*.py src scripts | wc -l      # 期望 0
  python3 scripts/audit_config.py                                 # EXPECT: DEADKEY=0 UNDECLARED=0
  env -u LLM_API_KEY python3 -m src.pipeline.runner --material /tmp/min.json --dry-run; echo "EXIT=$?"
  ```
- **预期输出**：`0` / `DEADKEY=0 UNDECLARED=0`；未设 `LLM_API_KEY` 时立即 `EXIT=78`（**不是**跑到第 3 步才炸）
- **实测输出（2026-11-09）**：
  - `grep REMOTION_DIR → 0` ✅
  - `audit_config`：声明 17 键（必填 8 / 可选 9）、`.env` 11 键、代码实读 10 键，`DEADKEY=0 UNDECLARED=0 MISSING_REQUIRED=0`，`EXIT=0` ✅
  - fail-fast：`EXIT=78` + `missing: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_FAST, LLM_MODEL_MAIN, SQLITE_DB_PATH, TTS_RATE, TTS_VOICE_NEWS, TTS_VOICE_TECH`，**一行未跑即退出** ✅
- **⚠️ 验收命令的一处必要修正**：单纯 `env -u LLM_API_KEY` **测不出**缺键 —— `.env` 文件会把该键重新补回环境。为此新增 `AUTOCONTENT_ENV_FILE` 覆盖变量，验收改为：
  ```bash
  env -u LLM_API_KEY AUTOCONTENT_ENV_FILE=/dev/null python3 -m src.pipeline.runner --material /tmp/min.json --dry-run; echo "EXIT=$?"   # → 78
  ```
  这是"用 /dev/null 当空白 env 文件"的真实模拟，比原命令更严格。
- **回滚**：`rm .env.example src/pipeline/config.py scripts/audit_config.py && git checkout pipeline.yaml && cp .env.bak.t19 .env`
- **禁止**：不得用 `os.getenv(k, "默认值")` 掩盖缺失 —— 默认值只允许出现在非敏感、非必填键上

#### GATE-1 · 声明层与模型契约验收
- **状态**：✅ **PASS**（2026-11-09）
- **准入条件**：T1.1–T1.9 全部 `✅`（已全部满足：9/9）
- **验收命令**（建议直接跑封装脚本）：
  ```bash
  bash scripts/gate1.sh; echo "GATE-1 EXIT=$?"
  ```
  等价于原链路：bootstrap → runner 干跑 → 契约结构 → model_switch 自检 → manifest 契约 → intake selftest → **hook 引擎自检（T2.1 加入）** → audit_config（共 8 项）
- **实测输出（2026-11-09）**：
  ```
  ✅ bootstrap 运行时自举    BOOTSTRAP: 20/20 PASS
  ✅ runner 干跑             DRY RUN OK
  ✅ 契约结构校验            STRUCT OK
  ✅ 模型切换自检            DRY RUN OK（未发起任何真实请求）
  ✅ 物料契约                MANIFEST OK required=1 anyOf=2
  ✅ 投料通道自检            SELFTEST PASS
  ✅ 配置面巡检              结论：配置面干净 —— 无死键、无未声明、必填齐全

  GATE-1 PASS
  GATE-1 EXIT=0
  ```
- **⚠️ 预期输出修正**：原写 `MANIFEST OK 3` 有误 —— `required` 只有 `title` 共 **1** 项，另两条约束（body / source_url）在 `anyOf` 里，`anyOf` 有 **2** 个分支。已改为打印 `required=1 anyOf=2`，避免后人拿错误数字对不上。
- **新增固化脚本**：`scripts/gate1.sh`（一次失败即汇总退出非零），GATE-1 从此不必手抄命令链
- **不通过处理**：退回对应任务，禁止进入 P2

---

### P2 · Hook 引擎与去重核心

#### T2.1 Hook 执行引擎 `src/pipeline/hooks.py`
- **状态**：✅ DONE（2026-11-09，含 20 个 hook 实现，警告 20 → 0）
- **依赖**：GATE-1
- **改动**：新增 `src/pipeline/hooks.py`（约 420 行）+ `scripts/hooks/_common.py`（约 250 行）+ **20 个 hook 实现**（`scripts/hooks/*.py`）
- **协议**（全部落地）：
  - 输入 `stdin`：`{task_id, run_root, step_id, artifacts:{...}, context:{...}}`
  - 输出 `stdout`：`{ok: bool, patch?: object, reason?: string}`
  - 超时：默认 30s，超时视为 `ok=false` **且写 `degraded_events(kind=skipped)`**（实测会写 `data/degraded_events.jsonl`）
  - `gate`：`ok=false` → 按 `on_fail`（`block` 中止 / `warn` 记录续行）
  - `transform`：`patch` 合并进 `context`（`run_chain` 顺序执行并合并）
  - `observe`：忽略返回值，仅落日志
- **退出码语义（实测定义）**：`0` = hook 正常执行完（`ok` 可为 false，那是业务判定）；`1` = 引擎层故障（`impl_missing` / 超时 / 崩溃 / 协议违规）或 `--strict` 下 `ok=false`
- **20 个 hook 实现**（`scripts/hooks/`）：

  | hook | 类型 | 判据（真实执行，非占位） |
  | :--- | :--- | :--- |
  | `envelope_validate` | observe | payload 三要素 + manifest 形态 |
  | `persist_task` | transform | 按 `entity_key` upsert `content_tasks`，回传规范 task_id |
  | `dedupe_entity` | gate/block | 近 60 条历史 theme + 发送台账，归一化/Jaccard≥0.8 |
  | `sensitive_scan` | gate/block | `config/sensitive_words.txt`(75) + 15 条注入/凭据正则 |
  | `policy_precheck` | gate/block | `config/platform_policies.yaml` 冷冻期/封禁/回复模式 |
  | `material_bundle` | transform | `Manifest.to_prompt_block()` 整份物料打包 |
  | `prompt_render` | transform | 物料块 + 账号 → 最终 prompt + `prompt_fp` |
  | `title_length_guard` | gate/warn | 平台上限（小红书 20/抖音 30）+ 最短 8 |
  | `material_check` | gate/block | schema + 事实可用性（占位串/重复度/长度） |
  | `expand_narration` | transform | 句末切分 + 4.5 字/秒时长预估 |
  | `content_fingerprint` | gate/warn | sha256(归一化 title+body) + 台账查重并登记 |
  | `frame_fingerprint` | transform | ffmpeg 抽 0.2s 灰度帧 → sha256 |
  | `frame_sample_check` | gate/block | 0.5s/2s 抽帧非纯色（stddev>3）+ 画幅比对（QA **M5**） |
  | `audio_duration_check` | gate/warn | ffprobe 时长 vs 字数推算（±30%） |
  | `landscape_precheck` | gate/block | 画幅声明 16:9 + 封面源横版且 ≥1280x720 |
  | `provenance_check` | gate/block | 正文数字逐个回查物料原文（QA **M1** 幻觉闸门） |
  | `send_ledger` | gate/block | `(entity_key, platform, date)` 已发则拦 |
  | `silence_guard` | gate/warn | 产物全缺 / `degraded_count>0` / 空 draft（QA **M4**） |
  | `external_truth` | gate/block | 产物真实存在+非空+ffprobe 可解码 |
  | `audit_log` | observe | run_root + `data/audit.jsonl` 双写审计 |
- **两个既有 impl 的适配器**（不改它们的源码，属 T3.2/T4.x 接管范围）：
  ```python
  ADAPTERS = {
      "hook.landscape_guard": ("scripts.landscape_guard", "check_landscape"),
      "hook.card_render": ("src.feishu.card_builder", "build_approval_card"),
  }
  ```
  引擎优先走适配器，其余一律 subprocess + stdin/stdout 协议。
- **验收**：
  ```bash
  python3 -m src.pipeline.hooks --list          # 22 项，impl 全部 ✓
  python3 -m src.pipeline.hooks --selftest      # 22 个 hook 真实调用 + 3 项负向验证
  python3 -m src.pipeline.runner --material /dev/null --dry-run | grep 警告
  ```
- **实测输出（2026-11-09）**：
  - `--selftest`：**22/22 通过**，耗时 0.02~0.63s/个；负向验证 3/3 按预期拦截（`sensitive_scan` 命中 `api_key`、`material_check` 拒绝无事实物料、`title_length_guard` 拦 1 字标题）→ `SELFTEST PASS（失败 0 项）`
  - runner 干跑：**`结构错误: 0    警告: 0`** ← 20 条警告清零
  - 自检夹具是**真物料**：1920x1080 / 3s / 带音轨的 H.264 视频 + PIL 渲染的可 OCR 横排大字封面 + 临时 SQLite 台账，不是 mock
- **回滚**：`rm src/pipeline/hooks.py scripts/hooks/*.py`
- **注意（实现期踩坑，已修）**：`ffmpeg` 抽帧必须用**二进制** `capture_output`，走 `text=True` 会在 `UnicodeDecodeError` 上崩（已写入 `_common.frame_gray` 注释）

#### T2.2 DB 四表迁移 `src/db.py`
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T2.1
- **改动**：`src/db.py`（+约 240 行）；`src/pipeline/runner.py`（状态流转）；`src/pipeline/hooks.py`（降级落表）；**新增 `scripts/migrate_db.py`（迁移入口，默认 dry-run）**
- **必须实现**（全部落地）：
  1. `init_db()` 中 `CREATE TABLE IF NOT EXISTS` 四表（§4.3，含 `uq_run_content` 唯一索引）
  2. `claim_send_slot(dedupe_key, ...) -> bool`：`INSERT ... ON CONFLICT DO NOTHING`，返回 `rowcount == 1`
  3. `record_artifact(task_id, kind, path, sha256, frame_hash)`
  4. `find_run_by_content(content_hash, account)`
  5. `log_degraded(task_id, step_id, hook_id, kind, detail)`（v1.1 新增）
  6. `create_task` 的 `INSERT OR REPLACE` **未改**（留到 T4.3）
- **⚠️ §4.3 的一处必要增补**：`pipeline_runs` 是 **run 级**表（无 `step_id`），但 `--resume` 需要步骤级进度。已新增 `steps_json`（`{step_id: status}`）+ `updated_at` 两列，**保持"四表"不变**（不加第 5 张表）。原 runner 里那份按 `(run_id, step_id)` 写入的伪代码已删除。
- **runner 状态流转（新增接线）**：
  | 时机 | 行为 |
  | :--- | :--- |
  | 真实运行启动 | `content_hash_of()` 算内容指纹 → `find_run_by_content()`；已有 `status=DONE` 同内容 → **幂等跳过写库并 EXIT=0** |
  | `--resume` | 复用已有 `run_id`，`resume_point()` 读 `steps_json` 算起点 |
  | 每步 | `set_step_status(RUNNING/DONE/BLOCKED/SKIPPED)` |
  | 被拦 | 步骤 `BLOCKED` + run `BLOCKED`，退出码仍 `64` |
  | 干跑 | **完全不写库**（实测 prod `pipeline_runs` 计数 0） |
- **降级留痕改走 DB**：`hooks._degraded()` 现在优先 `degraded_events` 表（带 task_id/step_id/hook_id），JSONL 仅兜底
- **迁移入口 `scripts/migrate_db.py`（dry-run 优先）**：
  迁移是最容易"跑一下就把生产库改了"的操作，因此脚本**默认只读**：
  ```bash
  python3 scripts/migrate_db.py            # dry-run（默认）：影子库算目标 schema + mode=ro 比对真实库
  python3 scripts/migrate_db.py --json     # 机器可读
  python3 scripts/migrate_db.py --apply    # 唯一会写库的入口
  ```
  - 做法：临时目录建影子库 → `init_db()` 得到目标 schema → 真实库 `file:...?mode=ro` 打开 → 比对 **表 / 列 / 索引** 三级差异 → 只打印将要执行的 DDL
  - 真实库在 dry-run 下不建表、不 ALTER、不产生 `-wal/-shm`
  - 实测三种场景（2026-11-09）：
    | 场景 | 输出 | 库文件 |
    | :--- | :--- | :--- |
    | 生产库现状 | 建表 0 · 加列 0 · 建索引 0（schema 已最新） | SHA256 `c85c9022…` 前后一致，大小/mtime 不变，无 wal |
    | 模拟旧库（删掉四表） | 建表 4 · 建索引 1（`CREATE_INDEX uq_run_content ON pipeline_runs`） | 副本 hash `a1d6e586…` 前后一致 |
    | 模拟缺列（删掉 `steps_json`） | 加列 1（`ADD_COLUMN pipeline_runs.steps_json`） | 副本 hash `eafcec53…` 前后一致 |
- **验收**（spec 原命令，实测）：
  ```bash
  python3 -c "
  from src.db import PipelineDB
  db=PipelineDB('./data/pipeline_state.db')
  print(db.claim_send_slot('k_test','t1','feishu','oc_x','/tmp/v.mp4'))
  print(db.claim_send_slot('k_test','t1','feishu','oc_x','/tmp/v.mp4'))
  db.log_degraded('t1','s1','h1','truncated','demo')"
  ```
  → `True` / `False` / 无异常 ✅（实测在 prod 库副本 `/tmp/t22.db` 上执行，避免把 `k_test` 测试行写进生产台账；prod 只做了建表迁移）
- **实测输出（2026-11-09，全部真实执行）**：
  - 迁移：prod DB 新增 4 表；`content_tasks` **仍为 163 行未动**；`pipeline_runs/send_ledger/degraded_events/artifacts` 全空
  - 方法级：`claim#1=True claim#2=False`；`record_artifact` 同键覆盖；`upsert_run` 对同 `(content_hash, account)` **复用原 run_id**（`run_b` → 返回 `run_a`）
  - 状态流转：真实运行 `EXIT=64`，`pipeline_runs` 行 = `status=BLOCKED, steps_json={"intake":"BLOCKED"}`
  - 断点续跑：预置 `{"intake":"DONE"}` → `已完成 1/7 步，从 check_material 继续`，`[intake] SKIP（断点已过）`
  - 幂等跳过：状态改 `DONE` 后重跑同物料 → `=== 幂等跳过（uq_run_content）===` + `EXIT=0`
  - 降级落表：hook 超时 `--timeout 0.001` → `degraded_events` 写入 `(t_degraded, expand_script, hook.envelope_validate, skipped, {...})`
- **同时修正**：`EXECUTORS_PENDING` 的说明已过期（T1.5/T1.8 组件都已就绪），改为「待接线：T2.5」，不再谎报"未实现"
- **回滚**：`git checkout src/db.py src/pipeline/runner.py` + 手动 drop 四表

#### T2.3 发送台账与来源校验 Hook
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T2.2
- **改动**：`scripts/hooks/send_ledger.py`（重写）、`scripts/hooks/provenance_check.py`（重写）
- **`send_ledger.py`**（全部落地）：
  - `dedupe_key = sha256(content_hash | video_sha256 | account)`
  - 走 T2.2 的 `claim_send_slot()`（`INSERT ... ON CONFLICT DO NOTHING`），抢到才算首发
  - 抢不到 → `{ok:false, reason:"already_sent"}`
  - 台账不可用 → **失败关闭**（`fail closed`）：宁可发不出去，也不冒重复发的风险
  - key 里带 `video_sha256` 的理由：同内容**重渲染的新片**（换封面/字幕）应允许发，同一份视频文件重复投递必须拦
- **`provenance_check.py`**（两层判据）：
  - A. **产物来源**：待发产物必须位于本次 `run_root` 内；落在 `output/<日期>/...` 而非本次 run → `{ok:false, reason:"stale_artifact"}`
  - B. **事实溯源**（保留 M1 闸门）：draft 正文的每个数字必须能在物料原文找到，否则 block
- **验收**（连续两次同 payload）：
  ```bash
  # 实测脚本：同 payload 连投两次
  ```
- **实测输出（2026-11-09）**：
  | 场景 | 结果 |
  | :--- | :--- |
  | 首次投递 | `ok=true`，`send_ledger` 落 1 行 |
  | 二次同 payload | `ok=false, reason="already_sent"` |
  | 产物在本次 run_root | `ok=true` |
  | 产物在 `output/20200101/run_old/video.mp4` | `ok=false, reason="stale_artifact: 不在本次 run_root 内"` |
  | 正文含物料没有的数字（`120`、`4`） | `ok=false, reason="正文含物料中不存在的数字 → 120, 4"` |
- **回滚**：`git checkout scripts/hooks/send_ledger.py scripts/hooks/provenance_check.py`

#### T2.4 内容与视觉指纹 Hook
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T2.2
- **QA 来源**：#11 判重精确匹配失效
- **改动**：新增 `src/utils/normalize.py`；重写 `scripts/hooks/content_fingerprint.py`、`scripts/hooks/frame_fingerprint.py`；`scripts/hooks/_common.py`（归一化改为委托）
- **`src/utils/normalize.py`**（**唯一归一化实现，评论判重与内容指纹共用**）：按 spec 实现（剥离 `[捂脸R]` 类表情码 → lower → 只保留 alnum + CJK），另提供 `content_hash(title, body, slug)`
  - `_common.normalize_text()` 改为**委托**该实现（保留兜底分支，防 hook 因导入问题整体失效）
- **`content_fingerprint.py`**（gate / `on_fail: warn`）：
  - `fp = sha256(normalize_text(title+slug+正文))[:16]`
  - 先查 `pipeline_runs.content_hash`（同 account），再查本地台账 `data/content_fingerprints.jsonl`
  - 命中其它 task → `ok=false`（warn 记录续行），同时把本次指纹登记进台账
- **`frame_fingerprint.py`**（transform）：
  - 抽 **3 帧**（0.5s / 中段 / 末-1s）→ 每帧 `scale=32:32` 灰度 → 拼接取 `md5[:16]`
  - 同时 `record_artifact(task_id, kind, path, sha256, frame_hash)` **写库**，事后可用 SQL 查"是不是重渲染的老片"
- **验收**：
  ```bash
  python3 -c "
  from src.utils.normalize import normalize_text as n
  print(n('DeepSeek V4 发布！')==n('DeepSeek  V4 发布  '))     # True 排版变体
  print(n('好用[捂脸R]')==n('好用'))                            # True 表情码剥离"
  ```
- **实测输出（2026-11-09）**：`True` / `True` / 幂等 `True` ✅
  指纹实测（真视频 4s / 640x360）：
  ```
  frame_fp = b27a12dde78a447e
  per_frame = {0.5s: d0d81d54, 2.0s: 6c7426ec, 3.0s: 26fe04b5}
  artifacts 行 = (t23, landscape, a1b644e34c91, b27a12dde78a447e)
  content_fp#1 ok=True；换 task_id 同内容 → ok=False「内容指纹重复（历史任务 t23）」
  ```
- **回滚**：删除 `src/utils/normalize.py`，`git checkout scripts/hooks/content_fingerprint.py scripts/hooks/frame_fingerprint.py scripts/hooks/_common.py`

#### T2.5 静默失败护栏（v1.1 新增）
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T2.2
- **QA 来源**：#1 #3 #8（共同母题"静默失败"）
- **改动**：重写 `scripts/hooks/silence_guard.py`；新增 `scripts/audit_silent_failure.py` + `config/silent_baseline.json`
- **`silence_guard.py`**（gate / `on_fail: warn`）4 条判据：
  1. `degraded_events` 里本 task/step 有记录 → `reason="degraded:N"`
  2. 声明的产物一个都不在磁盘（或存在但 0 字节）
  3. `draft.body` < 20 字 / `narration` 近乎为空
  4. `context.degraded_count > 0`
  - 告警落 `data/alerts.jsonl`（append-only，可统计"这条片子降级过几次"）
- **`audit_silent_failure.py`**（静态扫描器，防复发主力）：四类反模式
  | 反模式 | 判定 |
  |---|---|
  | 吞异常 | `except ...: pass / continue / return None\|""\|[]` |
  | 静默跳过 | `if not X: continue` |
  | 兜底默认值 | `return "D"` |
  | 截断当正常 | `finish_reason == "length"` 且 ±6 行内无 `raise` |
  - 支持基线：首次 `--init-baseline` 固化存量，之后**只报新增**
  - 退出码：新增违例 `0` 条 → `0`；有新增 → `1`
- **⚠️ 两处实现修正（相对原 spec 设计）**：
  1. **基线键不含行号**：原设计 `file:line:pattern` 一旦在违例上方插几行代码，老问题就会被误报成"新增"。改为 `file::pattern::snippet指纹#occurrence` —— 插注释不误报（实测 37/37 新增 0），同一文件多一处一模一样的问题仍能抓到（实测 37→38 新增 1）。
  2. **基线文件放 `config/silent_baseline.json`**（spec 原写 `.backup/...`）：`.backup/` 在 `.gitignore` 里，放那里 CI 拿不到基线。
- **验收**：
  ```bash
  python3 scripts/audit_silent_failure.py --init-baseline
  python3 scripts/audit_silent_failure.py; echo "EXIT=$?"
  ```
- **实测输出（2026-11-09）**：
  ```
  首次：存量违例 37（swallow_exception 36 / truncation_ignored 1）
  第二次：当前违例 37  基线 37  新增违例 0  →  EXIT=0
  ```
  - `silence_guard` 无降级 → `ok=true`；写入 1 条 degraded 后 → `ok=false, reason="silence_guard: degraded:1"`，并落 `alerts.jsonl`
- **回滚**：`git checkout scripts/hooks/silence_guard.py`；删除 `scripts/audit_silent_failure.py` 与基线
- **说明**：本任务**不清理存量 37 条**（属 N4 债务），只保证"不再新增"

#### T2.6 外部真值校验 Hook（v1.1 新增）
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T2.2
- **QA 来源**：#7 封面假数据、#10 seen 提前写
- **改动**：重写 `scripts/hooks/external_truth.py`；`scripts/hooks/_common.py`（新增 `ocr_text` / `ocr_text_span`）
- **按 `context.artifact_kind` 分派**（全部落地）：
  | kind | 校验手段 | 断言 |
  |---|---|---|
  | `vertical` | ffprobe | 1080×1920 |
  | `landscape` | ffprobe + 抽帧 OCR | 1920×1080 **且** 封面文字 bbox 横向跨度 ≥75% |
  | `cover_text` | tesseract OCR | OCR 文本与 draft 的 `title`/`resource_keyword` 有 ≥1 个公共 token |
  | `sent` | 台账 + 飞书回查 | `send_ledger` 行存在且 `feishu_msg_id` 非空；配了凭据再调 API 回查 |
- **CLI 形态**（spec 原样支持）：`--kind landscape --video <path> --dry-run`
- **实测输出（2026-11-09）**：
  ```json
  # python3 scripts/hooks/external_truth.py --kind landscape --video /tmp/lg.mp4 --dry-run
  {"ok": true, "ffprobe": {"width": 1920, "height": 1080}, "text_span": 0.992, "kind": "landscape"}
  ```
  其余用例：
  | 用例 | 结果 |
  | :--- | :--- |
  | 竖版 1080×1920 | `ok=true` |
  | 竖版给成 1920×1080 | `ok=false, reason="竖版分辨率错误 1920x1080，应为 1080x1920"` |
  | 横版纯图形（OCR 无文字） | `ok=false, reason="封面 OCR 失败，无法判定版式（不许当作通过）"` |
  | 封面文案命中 | `ok=true`，公共 token `cover/co/ca…` |
  | 封面文案无关 | `ok=false` + OCR 前 40 字 |
  | `sent` 无台账 | `ok=false`「send_ledger 无 task=… 的发送记录（seen 提前写？）」 |
  | `sent` 有行无 msg_id | `ok=false`「消息未经确认」 |
  | `sent` 台账+msg_id | `ok=true`（`api_checked=false`，未配飞书凭据） |
- **踩坑（已修，写进 `_common` 注释）**：
  1. 对静态图片不能带 `-ss`（抽不到帧）→ 图片走 `shutil.copyfile` 到真实临时路径再 OCR
  2. **macOS 上 tesseract 打不开 `/tmp/xxx.png`**（`/tmp` 是软链，QA#12）→ 必须复制到 `tempfile`（`/var/folders/…`）后再 OCR
- **回滚**：`git checkout scripts/hooks/external_truth.py scripts/hooks/_common.py`

#### GATE-2 · 台账幂等验收
- **状态**：✅ **PASS**（2026-11-09）
- **准入条件**：T2.1–T2.6 全部 `✅`（6/6 满足）
- **验收命令**（已封装）：
  ```bash
  bash scripts/gate2.sh; echo "GATE-2 EXIT=$?"
  ```
- **4 项断言（实测全绿）**：
  ```
  ✅ claim_send_slot ×3        claim=[True, False, False] rows=1
  ✅ hook 二次投递             first=True second=False already_sent
  ✅ 静默失败扫描              新增违例 0
  ✅ hook 引擎自检             SELFTEST PASS
  GATE-2 PASS   EXIT=0
  ```
- **与原 spec 命令的关系**：原命令 `sqlite3 data/pipeline_state.db "select count(*) from send_ledger where dedupe_key='k_test'"` 期望 `1`，但会往**生产台账**写测试行。`gate2.sh` 改为在临时库里跑同一断言（结果一致，且不污染生产），另外补了 hook 层与静态扫描两项。
- **不通过处理**：回退 T2.2，检查 `send_ledger.dedupe_key PRIMARY KEY` 是否生效

---

### P3 · 去重生效与发送链路加固（高风险）

#### T3.1 `entity_key` 归一化
- **状态**：✅ DONE（2026-11-09，含生产库迁移）
- **依赖**：GATE-2
- **风险等级**：**高**（影响历史数据比对）
- **改动**：`src/utils/normalize.py`（新增 `normalize_entity_key`）、`src/hotspot_extractor.py`、`scripts/hooks/persist_task.py`、新增 `scripts/migrate_entity_key.py`
- **执行**（已完成）：
  1. **归一化函数**：`normalize_entity_key()` 剥尾部 `-\d{10}`，且**只剥 16 亿~22 亿区间的 10 位数**（避免误伤 `gpt-4-0613` 这类版本号）；**不做小写化** —— 存储层保留历史原始大小写，比较时由调用方 `.lower()`
     - ⚠️ 实测教训：初版在归一化里顺手 `lower()`，dry-run 直接指出会**多改 17 行**无关历史数据（`github:NousResearch/Hermes-Agent` 之类只有大小写差异的行）。已收回，迁移范围从 19 行降到 3 行。
  2. **生成处不再拼时间戳**：`pick_and_convert_hotspot()` 写库前统一过 `normalize_entity_key`；SYSTEM_PROMPT 增加硬约束「entity_key 严禁拼接时间戳/序号/随机串」
  3. **比对处统一口径**：`check_topic_duplicate()` 两侧都先归一化再比
  4. **迁移脚本**默认 dry-run，`--apply` 才写库，写前自动备份到 `.backup/<日期>/pipeline_state_before_entity_key_<ts>.db`；冲突时 `--apply` **默认拒绝**，需 `--resolve-conflicts` 才按建议方案执行（落败行改 `<key>~dupN`），并生成 `.backup/entity_key_migration_report.json`
- **dry-run 实测（生产库，只读）**：
  ```
  总行数 163 · 将变更 3 行 · 冲突 1 组
  hotspot_1_claudecode_leak_1794114373  hackernews:...-1794114373 → hackernews:claude-code-dont-ask-again-leak
  hotspot_1_claudecode_leak_1794114400  hackernews:...-1794114400 → hackernews:claude-code-dont-ask-again-leak
  hotspot_1_qwen9b_local_1794129836     twitter:...-5.6gb-1794129836 → twitter:qwen35-9b-deepseek-distill-5.6gb
  ! 冲突: hackernews:claude-code-dont-ask-again-leak
        保留: hotspot_1_claudecode_leak_1794114400（DISPATCHED）
        落败: hotspot_1_claudecode_leak_1794114373（SCRIPTED）→ ...-leak~dup2
  ```
  生产库 SHA256 `58d33ed9…` 前后一致 —— **dry-run 未写库**
- **副本库实跑（`/tmp/ek.db`，不是生产）**：
  - `--apply` 无 `--resolve-conflicts` → **拒绝执行 EXIT=1**（符合"冲突必须人工裁决"）
  - `--apply --resolve-conflicts` → 写库 3 行，`总行 163 · 唯一 key 163`，无 `UNIQUE constraint failed`
  - 二次 dry-run → `将变更 0 行`（幂等）
- **去重生效验证**：候选 `entity_key=hackernews:claude-code-dont-ask-again-leak-1799999999` 对历史带时间戳 key → `True / 事件标识 entity_key 重复`（旧逻辑会漏判）
- **生产库执行（已授权执行，2026-11-09）**：
  ```
  迁移前 163 行 / 163 唯一 key，带时间戳 key 3 条；SHA256 607a6bf5…
  执行   python3 scripts/migrate_entity_key.py --apply --resolve-conflicts
  结果   已写库 3 行 · 总行 163 · 唯一 key 163 · 无 UNIQUE constraint failed
         备份 .backup/20261109/pipeline_state_before_entity_key_20261109_133413.db
         报告 .backup/entity_key_migration_report.json
  迁移后 带时间戳 key 0 条；SHA256 22a86ec9…
         状态分布不变（ARCHIVED 111 / DISPATCHED 43 / RENDERED 5 / WAITING_APPROVAL 2 / SCRIPTED 2）
         三行终值：…_1794114400 → hackernews:claude-code-dont-ask-again-leak（DISPATCHED，保留）
                   …_1794114373 → hackernews:claude-code-dont-ask-again-leak~dup2（SCRIPTED）
                   …_1794129836 → twitter:qwen35-9b-deepseek-distill-5.6gb
         二次 dry-run → 将变更 0 行（幂等）
  ```
- **真实库判重验证**：`get_recent_pipeline_topics()` 取真历史 → 候选 `…-1799999999` 判 `True / entity_key 重复`；全新事件 `github:brand/new-project-1900000000` 放行 `False`
- **顺带修掉一个生产污染**：`hooks._degraded()` 原先只认 `SQLITE_DB_PATH`，导致 `--selftest` 把 `selftest_t1` 的降级行写进**生产** `degraded_events`。已改为优先取 `payload.context.db_path`，并清掉那条测试行；实测自检跑完生产表仍为 0 行。
- **回滚**：`cp .backup/20261109/pipeline_state.db data/pipeline_state.db` 或还原 `pipeline_state_before_entity_key_*.db`
- **失败处理**：出现 UNIQUE 冲突 → 立即回滚，冲突行进人工裁决

#### T3.2 发送链路加固
- **状态**：✅ DONE（2026-11-09）
- **依赖**：T3.1
- **QA 来源**：#8 post code_block 静默丢弃、#10 seen 提前写、#12 同名文件缓存
- **改动**：`src/feishu/approval_service.py`、`scripts/run_publish_worker.py`、`src/feishu/card_builder.py`、`src/pipeline/hooks.py`、`pipeline.yaml`、`.env.example`
- **决策记录**：D1=**A 派生**（`content_hash` 缺省由视频文件 sha256 派生）/ D2=**接台账**（发送前 claim，成功后 mark_sent）/ D3=**下沉**（手机素材卡直接产 Card 2.0）
- **8 项执行结果**：
  1. **文件名**：`int(time.time())` → `f"{task_id}_{content_hash[:8]}{ext}"`，实测 `task_abc_12af95bb.mp4`
  2. **`feishu_msg_id` 回写**：`send_ledger.feishu_msg_id`，实测副本库 4 行台账中 `feishu_material` 行 = `om_fake_1`
  3. **无指纹拒发**：`content_hash` 缺省 → 由文件派生；文件读不到 → **拒绝发送**并落 `send_refused_no_fingerprint` 告警
  4. **强制 Card 2.0**：`notify_feishu` 只发 `interactive`；手机素材卡由 `build_mobile_copy_card()` 直接产出，**不再经过 post 富文本**
  5. **`code != 0` 统一告警**：`approval_service` 4 处调用全部改为 `_ok(resp, where)` 判定 + `data/alerts.jsonl`（可用 `AUTOCONTENT_ALERTS_FILE` 重定向）
  6. **`seen` 时机**：worker 侧 `mark_sent` 只在**发送成功后**调用（评论侧留 T5.1）
  7. **R3 下沉**：`_post_to_card2` 移入 `src/feishu/card_builder.py::build_card2_from_post()`；`hook.card_render` 适配器按 `context.card_kind`（`post` / `mobile_copy` / 默认审批卡）分派
  8. **R2 去硬编码**：`FEISHU_APPROVAL_CHAT_ID` 缺失 → `raise MissingChatIdError`，代码内不再出现 `ou_bc83…`
- **验收（spec 原命令，实测全绿）**：
  ```
  [1] OK: no timestamp filename
  [2] OK: no post code_block
  [3] OK: card build 已下沉
  [4] OK: 无硬编码会话 ID
  [5] feishu_msg_id 计数 = 3（≥1）
  ```
- **端到端（副本库 + 假 Provider/假 client，不发真消息）**：
  | 场景 | 结果 |
  | :--- | :--- |
  | 首次 `process_task` | 台账 4 行（bilibili / channels / douyin / feishu_material），`feishu_msg_id=om_fake_1`，状态 → `DISPATCHED` |
  | 第 2 次同内容重跑 | 4 个渠道全部「台账命中」拦截，台账仍 4 行 |
  | 第 3 次重跑 | 素材卡拦截（0 次），仅汇报卡 1 次，台账仍 4 行 |
  | 老调用点兼容（不传 content_hash/task_id） | `upload_and_send_video(...)` → 文件名 `t32r_4fb65636.mp4`，返回值正常 |
  | 视频文件不存在 | `None` + 明确日志（不静默） |
  | `FEISHU_APPROVAL_CHAT_ID` 为空 | `MissingChatIdError`（拦截生效） |
  | 飞书返回 code=10001 | `None` + `alerts.jsonl` 落 1 条 `feishu_api_error` |
- **新增配置面**：`AUTOCONTENT_ALERTS_FILE`（可选，声明进 `pipeline.yaml` / `.env.example`），用于自检/CI 不污染生产告警流
- **回归**：`hooks --selftest` 22/22 · 静默失败扫描新增 0（新代码里 2 处 `except OSError` 已补日志）· GATE-1 PASS · GATE-2 PASS
- **⚠️ 测试污染披露**：调试 `notify_feishu` 时因 `.env` 会重新注入 `FEISHU_APPROVAL_CHAT_ID`，误发过 1 条文本「hi」到审批会话（无业务影响）
- **回滚**：`git checkout src/feishu/approval_service.py scripts/run_publish_worker.py src/feishu/card_builder.py src/pipeline/hooks.py`
- **注意**：小品对 `run_publish_worker.py` 的改动**未回退**，其 `_post_to_card2` 逻辑已原样迁入 `card_builder` 并被复用

#### T3.3 废弃 `resend_*.py`
- **状态**：⬜ PENDING
- **依赖**：T3.2
- **改动**：`scripts/resend_sherlock_approval.py`、`scripts/send_showcase_to_feishu.py` → `scripts/_deprecated/`
- **替代**：`python -m src.pipeline.runner --resume --task-id <id>`（必须过 `send_ledger`）
- **验收**：
  ```bash
  ls scripts/resend_*.py 2>/dev/null || echo "OK: no resend scripts"
  ls scripts/_deprecated/ | wc -l
  ```
- **预期输出**：`OK: no resend scripts` + `2`
- **回滚**：`mv scripts/_deprecated/*.py scripts/`

#### T3.4 定时任务去模型依赖（v1.1 新增）
- **状态**：⬜ PENDING
- **依赖**：GATE-2
- **QA 来源**：#1 cron 未 pin 导致静默断流
- **改动**：cron job 配置 + `scripts/audit_cron_pin.py`
- **执行**：
  1. 盘点所有定时任务（T0.2 已列），分两类处理：
     - **纯抓取/推送类** → 改 `no_agent: true` + `script:`，彻底摆脱模型依赖（QA 已验证可行）
     - **必须用模型的** → **显式 pin 模型**（`model:` 字段写死）
  2. 新增 `scripts/audit_cron_pin.py`：扫描 jobs.json，任何 `unpinned 且 使用模型` 的 job 报警
- **验收**：
  ```bash
  python3 scripts/audit_cron_pin.py; echo "EXIT=$?"
  ```
- **预期输出**：列出所有 job 的 `pinned/no_agent` 状态；无"未 pin 且依赖模型"的 job，`EXIT=0`
- **回滚**：还原 jobs.json 备份
- **注意**：`~/.hermes/profiles/*/cron/jobs.json` 属运行时配置，改动前必须单独备份

#### GATE-3 · 端到端不重发验收
- **状态**：⬜ PENDING
- **准入条件**：T3.1–T3.4 全部 `✅`
- **验收步骤**：
  1. 用同一物料完整跑一次流水线（真实渲染）
  2. 不改任何东西，再跑一次
  3. 断言飞书只收到 1 条视频消息，`send_ledger` 只多 1 行
- **验收命令**：
  ```bash
  sqlite3 data/pipeline_state.db "select count(*) from send_ledger;"   # 运行前后差值必须为 1
  ```
- **预期输出**：增量 `1`；第二次运行日志出现 `already_sent`
- **不通过处理**：回退 T2.3，检查 `dedupe_key` 是否误把 `run_id` 算进去

---

### P4 · 隔离与解耦（**必做阶段**）

> **v1.2 修正（P-1）**：本阶段由「高风险，可选」升为 **必做**。理由：隔离是内聚的**前提**而非优化项 —— `renderer/public/tasks/` 共享目录不废，`run_root` 无法成立，T1.7 的 manifest 驱动也就落不到真实产物路径上。
> **排期约束**：GATE-3 通过后**立即启动**，**T4.1 为 GATE-3 之后第一优先级任务**；原先"稳定运行 ≥3 天再启动"的建议作废（该建议等于把内聚缺口长期冻结）。

#### T4.1 `run_root` 隔离 + 横版产物命名统一
- **状态**：⬜ PENDING
- **依赖**：GATE-3
- **改动**：`src/tts/aligner.py`、`scripts/generate_from_news.py`、`scripts/render_landscape.py`
- **执行**：
  1. `aligned_render.json` 与分段音频改写到 `{run_root}/props/`；Remotion 改传绝对路径 props；`renderer/public/tasks/` 只读保留
  2. **命名统一（P-6，v1.2）**：`scripts/render_landscape.py` 产物改名 —— `video_landscape_16_9.mp4` → `video_16_9.mp4`，`cover_landscape.png` → `cover_16_9.png`，与 `pipeline.yaml` 的 `produces` / `dispatch.reads` 对齐
     ```python
     video = out_dir / "video_16_9.mp4"      # 原 video_landscape_16_9.mp4
     cover = out_dir / "cover_16_9.png"      # 原 cover_landscape.png
     ```
  3. 同步修改 `scripts/landscape_guard.py` 与 `scripts/render_landscape.py` 内的文件名引用，`grep` 确认零残留
- **验收**：
  ```bash
  # 命名一致性（三处必须同名）
  grep -rn "video_landscape_16_9\|cover_landscape" scripts/ src/ && echo "VIOLATION" || echo "OK: 命名已统一"
  grep -n "video_16_9.mp4" pipeline.yaml scripts/render_landscape.py
  # 并发隔离
  ```
- **预期输出**：`OK: 命名已统一`；两处均出现 `video_16_9.mp4`；并发启动 2 个不同 task，`diff` 两者 `props/` 无交集，两片均正常渲染
- **回滚**：`git checkout` 三个文件

#### T4.2 去硬编码
- **状态**：⬜ PENDING
- **依赖**：T4.1
- **改动**：`generate_from_news.py`（`source_url`/`target_account_id` 改从 `manifest.json` 读）、`comment_funnel.py`（默认 `base_url` 移除，缺失即报错）
- **验收**：
  ```bash
  grep -rn "127.0.0.1:8317\|douyin-tech-main\|news.hot-digest.local" src scripts && echo "VIOLATION" || echo "OK: 无残留"
  ```
- **预期输出**：`OK: 无残留`
- **回滚**：`git checkout` 两文件

#### T4.3 状态机归位
- **状态**：⬜ PENDING
- **依赖**：T4.2
- **改动**：`src/db.py::create_task`
- **执行**：`status` 改为入参默认 `INGESTED`；`INSERT OR REPLACE` → `INSERT ... ON CONFLICT DO NOTHING` 并返回是否冲突
- **验收**：新任务创建后状态为 `INGESTED`；重复实体返回冲突而不覆盖
- **回滚**：`git checkout src/db.py`

#### T4.4 横屏出厂治理（v1.1 新增，v1.2 接管小品修复）
- **状态**：⬜ PENDING
- **依赖**：GATE-3
- **QA 来源**：#6 横屏用竖屏封面（**二次复发**）、#7 模板硬编码假数据
- **接管现状**（见 §4.8 C7/C8）：
  | 文件 | git 状态 | 结论 |
  |---|---|---|
  | `scripts/landscape_guard.py` | **未提交**（`??`） | 判据正确，实测有效 —— 保留，转 hook 协议 + 修 docstring |
  | `scripts/render_landscape.py` | **未提交**（`??`） | 方向正确 —— 保留，去共享目录依赖、对齐产物名、加超时 |
  | `scripts/render_landscape_bilibili.py` | 已提交 `31812d5` | **A1–A3 抓不到错版 → 废弃** |
  | `renderer/src/components/BilibiliLandscapeCover.tsx` | 已改未提交 | 方向正确 —— 保留，重做静默/null 与类型 |
- **改动**：`scripts/landscape_guard.py`、`scripts/render_landscape.py`、`scripts/render_landscape_bilibili.py`（标记废弃）、`scripts/generate_bilibili_horizontal_demo.py`（改指向）、`renderer/src/components/BilibiliLandscapeCover.tsx`
- **执行**：
  1. **（R4 双入口收敛）** 横屏只允许走 `scripts/render_landscape.py::render_landscape()`；`render_landscape_bilibili.py` 头部加 `# DEPRECATED: A1–A3 断言无法识别「竖版封面塞横屏」（首帧与封面同图，像素差恒 0），已由 landscape_guard.check_landscape 取代`，并把 `generate_bilibili_horizontal_demo.py:122` 的 `from scripts.render_landscape_bilibili import render_landscape` 改为 `from scripts.render_landscape import render_landscape`。runner 的 `builtin.render` 对 `aspect=landscape` 强制走此入口
  2. **（guard 转 hook 协议）** `landscape_guard.py` 增加 `main()` 支持 JSON `stdin → stdout`（`{video, expect_text}` → `{ok, span, reason}`），保持 CLI 兼容；`pipeline.yaml` 的 `hook.landscape_guard` 指向它
  3. **判据校准**（QA 实测，必须按此实现，勿改阈值）：
     | 判据 | 错版实测 | 正确实测 | 阈值 |
     |---|---|---|---|
     | 封面文字 bbox 横向跨度 | 32.8% | 82.4%~90.4% | **≥75%** 通过 |
     | 分辨率 | — | 1920×1080 | 必须精确匹配 |
     > QA 踩坑：最初用"像素方差列覆盖度"判据，把正确横屏误判成 57%。**必须用 tesseract tsv 的文字 bbox 跨度**
  4. **（docstring 纠正）** `landscape_guard.py` 文件头仍写「内容横向覆盖度 >= 80%」，与实现（OCR bbox ≥75%）不符 —— 改为「封面文字 bbox 横向跨度 ≥75%（错版实测 32.8%，正确 82.4%~90.4%）」
  5. **假数据检测**：封面 OCR 文本中的每个宣传短句，必须能在 `draft_script.json` 的 `title`/`resource_keyword`/`scenes[].highlight_text` 中找到 token；找不到即 `fail`
  6. **（R2 重做三卡）** 保留"改取真实分镜"的方向，但必须消除静默：
     - `if (!text) return null;` → 改为渲染占位卡 + `console.warn('[cover] scene_{i} highlight_text 缺失')`，并计入 `external_truth` 的 `degraded` 信号
     - 魔数 `[0, 1, 3]` → 由 `pipeline.yaml` 的 `cover.card_source` 声明（默认 `[0,1,3]`），组件只读不改
     - `(scene as any)` → 用 `AlignedSceneItem` 类型 + `Partial<>` 收窄，禁止 `any`
     - 副标题继续用 `props.resource_keyword`（小品改法保留）
  7. **（R2）`expect_text` 比对加归一化**：`if expect_text not in txt.replace(" ","")` → 走 `src/utils/normalize.py` 归一化后比对，避免 OCR 空格/全半角差异误判
  8. **产物名对齐契约**：`video_landscape_16_9.mp4`/`cover_landscape.png` → `video_16_9.mp4`/`cover_16_9.png`（与 T4.1、附录 A 一致）
- **验收**：
  ```bash
  # 1 正样本必须通过（实测 span≈0.904）
  python3 scripts/landscape_guard.py output/20261108/hotspot_6_sherlock_1794149735/hotspot_6_sherlock_1794149735_video_landscape_16_9.mp4; echo "EXIT=$?"
  # 2 负样本必须拒绝（实测 1080x1920）
  python3 scripts/landscape_guard.py output/holiday_rigorous/task_holiday_2027_rigorous_1793876640/video.mp4; echo "EXIT=$?"
  # 3 hook 协议可用
  echo '{"video":"<path>","expect_text":""}' | python3 scripts/landscape_guard.py
  # 4 双入口已收敛
  grep -rn "render_landscape_bilibili" scripts/*.py | grep -v DEPRECATED && echo "VIOLATION" || echo "OK: 无残留调用"
  # 5 假数据与类型
  grep -rn "自然语言直接生成\|30秒直出\|TOP1 登顶" renderer/src/ && echo "VIOLATION" || echo "OK: 无硬编码宣传语"
  grep -n "as any" renderer/src/components/BilibiliLandscapeCover.tsx && echo "VIOLATION" || echo "OK: 无 any 断言"
  grep -n "覆盖度 >= 80" scripts/landscape_guard.py && echo "VIOLATION: docstring 未纠正" || echo "OK: docstring 已纠正"
  ```
- **预期输出**：
  - 正样本 `{"size":"1920x1080","cover_text_span_x":0.904,"ok":true}` / `EXIT=0`
  - 负样本 `{"size":"1080x1920","reason":"分辨率错误..."}` / `EXIT=1`
  - `OK: 无残留调用` / `OK: 无硬编码宣传语` / `OK: 无 any 断言` / `OK: docstring 已纠正`
- **回滚**：`git checkout scripts/render_landscape_bilibili.py scripts/generate_bilibili_horizontal_demo.py renderer/src/components/BilibiliLandscapeCover.tsx`（两个未提交文件直接删除即可回到基线）
- **注意**：小品的两个未提交文件**先 `git add` 再改**，避免改造失败时被误删；验收里的两条 `EXIT` 断言是本次唯一"已实测通过"的基线，任何改造后必须保持

#### T4.5 TTS 配置接线（v1.2 新增，P-3）
- **状态**：⬜ PENDING
- **依赖**：T1.1（`providers.tts.tech` 骨架）
- **缺口来源**：§1.6 **I3 / I4** —— `generate_from_news.py:175-176` 硬编码音色与语速；`.env` 的 `TTS_VOICE_TECH` / `TTS_VOICE_NEWS` / `TTS_RATE` 实测**零引用**
- **改动**：`scripts/generate_from_news.py`、`src/tts/aligner.py`、`pipeline.yaml`
- **执行**：
  1. 删除 `generate_from_news.py:175-176`：
     ```python
     # 删除：
     voice = "zh-CN-YunxiNeural" if task_data["target_account_type"] == "tech" else "zh-CN-XiaoxiaoNeural"
     aligner = TTSAligner(voice=voice, rate="+10%")
     ```
  2. 改为从槽位解析（`account_type` → 槽位名映射，不出现任何音色字面量）：
     ```python
     slot = "tts.tech" if task_data["target_account_type"] == "tech" else "tts.news"
     voice = resolve_env(cfg["providers"][slot]["env"]["voice"])   # TTS_VOICE_TECH
     rate  = resolve_env(cfg["providers"][slot]["env"]["rate"])    # TTS_RATE
     if not voice or not rate:
         raise MissingConfig(f"{slot} 的 voice/rate 未配置，拒绝静默降级到默认音色")
     aligner = TTSAligner(voice=voice, rate=rate)
     ```
  3. `pipeline.yaml` 补 `tts.news` 槽位（`env: {voice: TTS_VOICE_NEWS, rate: TTS_RATE}`），与 `tts.tech` 对齐
  4. **禁止兜底**：env 缺失必须抛异常，不得回退到硬编码默认值（对齐 §4.5 静默失败零容忍）
- **验收**：
  ```bash
  grep -rn "zh-CN-YunxiNeural\|zh-CN-XiaoxiaoNeural\|rate=\"+10%\"" scripts/ src/ && echo "VIOLATION" || echo "OK: 无硬编码音色"
  grep -c "voice" .env    # 必须被读取
  python3 -c "from src.pipeline.config import resolve_env;print('VOICE',resolve_env('TTS_VOICE_TECH'),'RATE',resolve_env('TTS_RATE'))"
  ```
- **预期输出**：`OK: 无硬编码音色`；`VOICE zh-CN-YunxiNeural RATE +10%`（值来自 `.env` 而非代码）
- **回滚**：`git checkout scripts/generate_from_news.py src/tts/aligner.py pipeline.yaml`
- **验证方式**：改 `.env` 里 `TTS_RATE=+0%` 重跑，`ffprobe` 音频时长应随之变长 —— 证明配置真正生效

#### T4.6 审批渠道抽象（v1.2 新增，P-4）
- **状态**：⬜ PENDING
- **依赖**：T1.3（runner 的 gate 步骤）
- **缺口来源**：§1.6 **I5** —— `approval` 步仅硬编码飞书卡片文案，无 `mode` 抽象
- **改动**：`pipeline.yaml`（`approval` 步）、新增 `src/approval/` 适配层（`feishu.py` / `cli.py` / `auto.py`）、`src/pipeline/runner.py`
- **执行**：
  1. `pipeline.yaml` 的 `approval` 步改为：
     ```yaml
     - id: approval
       name: "人审"
       type: gate
       params:
         mode: ${APPROVAL_MODE:-auto}     # feishu | cli | auto
         message: "请确认成片"
       options: [approve, reject]
       on_reject: abort
     ```
  2. 适配层统一接口 `request_approval(run_id, artifacts, message) -> "approve"|"reject"`：
     - `feishu`：沿用现有卡片审批（`src/feishu/approval_service.py`）
     - `cli`：终端 `input()` 交互，适合本地/CI 手跑
     - `auto`：按 env 推断 —— 检测到 `FEISHU_APP_ID` + `FEISHU_APP_SECRET` 用飞书，否则降级 `cli`，并**打印一行降级原因**（不静默）
  3. `runner` 只依赖 `request_approval`，不得出现 `feishu` 字面量
- **验收**：
  ```bash
  grep -rn "feishu" src/pipeline/ && echo "VIOLATION" || echo "OK: runner 无渠道耦合"
  APPROVAL_MODE=cli python3 -m src.pipeline.runner --material /tmp/m1.json --dry-run; echo "EXIT=$?"
  ```
- **预期输出**：`OK: runner 无渠道耦合`；`cli` 模式下干跑打印 `[approval] mode=cli` 且 `EXIT=0`，不报飞书连接错误
- **回滚**：`git checkout pipeline.yaml src/pipeline/runner.py && rm -rf src/approval/`

#### T4.7 出片与发片解耦（v1.3 新增，G-D / G-E）
- **状态**：⬜ PENDING
- **依赖**：T4.6（审批抽象）
- **缺口来源**：§1.7 **G-D / G-E** —— `approval_modes` 只声明取值域无默认值；`aspects` 无"只要片不发片"开关，出片与发片仍耦合（`dispatch` 会去碰各平台登录态）
- **改动**：`pipeline.yaml`（`dispatch` 段、`approval.params.mode` 默认值）、`src/pipeline/runner.py`、`src/approval/auto.py`
- **执行**：
  1. `dispatch` 段按 §4.9.4 改造：
     ```yaml
     - id: dispatch
       name: "分发"
       run: builtin.dispatch
       params:
         enabled: true                 # false ⇒ 只出片
         mode: "${DISPATCH_MODE:-publish}"   # publish | output_only
     ```
  2. `output_only` 分支：跳过 `approval` 与 `dispatch`，在 `{run_root}/artifacts.txt` 写入产物绝对路径清单并 `stdout` 打印
  3. **登录态隔离**：`output_only` 下禁止 import/初始化 `xiaohongshu_cdp`、禁止读 `data/xhs_storage_state.json`（用 `if mode == "publish"` 门控，而不是 try/except 兜）
  4. `approval.params.mode` 默认值落实为 `auto`，并由 `src/approval/auto.py` 打印降级原因（T4.6 已定义接口）
  5. `aspects` 增加语义说明：`[vertical]` 只出竖版、`[landscape]` 只出横版、`[]` 报错
- **验收**：
  ```bash
  DISPATCH_MODE=output_only python3 -m src.pipeline.runner --material /tmp/min.json --dry-run; echo "EXIT=$?"
  grep -rn "xhs_storage_state\|xiaohongshu_cdp" src/pipeline/runner.py && echo "VIOLATION" || echo "OK: 无登录态耦合"
  python3 -c "import yaml;d=yaml.safe_load(open('pipeline.yaml'));s=[x for x in d['steps'] if x['id']=='dispatch'][0];print('DISPATCH',s['params'])"
  ```
- **预期输出**：`output_only` 干跑打印产物清单且 `EXIT=0`，日志无 CDP/登录态初始化；`OK: 无登录态耦合`；`DISPATCH {'enabled': True, 'mode': '${DISPATCH_MODE:-publish}'}`
- **回滚**：`git checkout pipeline.yaml src/pipeline/runner.py`
- **不做**：`output_only` 下**不得**顺手跑 `send_ledger` 抢槽位（那是发片才需要的），否则只出片会污染台账

---

### P5 · 幂等写入时机归位（v1.1 新增阶段）

#### T5.1 `seen` / 已处理状态写入时机归位
- **状态**：⬜ PENDING
- **依赖**：T3.2
- **QA 来源**：#10 seen 提前写导致漏回、#5 登录态未固化
- **改动**：`src/safety/comment_funnel.py`、`src/providers/xiaohongshu_cdp.py`、`scripts/run_comment_cron.py`
- **执行**：
  1. **铁律**：`seen` / `REPLIED` / `IGNORED` **只在外部确认成功后写入**（子楼层 API 回读成功 / 发送返回 `message_id`）
  2. 判重 SQL 改用 `src/utils/normalize.py` 归一化后比对（QA#11），并让 `IGNORED` 参与判重（原实现不参与 → C/D 类每轮重复）
  3. **状态固化**：登录成功后立即 `ctx.storage_state(path="data/xhs_storage_state.json")`；`ensure_ready()` 检测到无 `web_session` 时回灌（QA#5）
- **验收**：
  ```bash
  grep -n "storage_state" src/providers/xiaohongshu_cdp.py | head -3
  python3 scripts/audit_silent_failure.py --check seen-before-send; echo "EXIT=$?"
  ```
- **预期输出**：`storage_state` 至少 2 处（写入 + 回灌）；`seen-before-send` 检查 `EXIT=0`
- **回滚**：`git checkout` 三个文件
- **失败处理**：若改造后出现重复回复（判重失效）→ 立即回滚，判重逻辑优先于幂等顺序

#### GATE-5 · 全链路静默失败扫描
- **状态**：⬜ PENDING
- **准入条件**：T2.5 / T3.2 / T5.1 全部 `✅`
- **验收命令**：
  ```bash
  python3 scripts/audit_silent_failure.py && \
  python3 scripts/audit_cron_pin.py && \
  python3 scripts/model_switch_check.py --dry-run && \
  echo "GATE-5 PASS"
  ```
- **预期输出**：三项扫描全部无新增违例，`GATE-5 PASS`
- **不通过处理**：定位新增违例来源，回退对应任务

---

## 6. 不做清单（Do-Not-Do）

1. 不重命名或移动 `output/` 下历史产物
2. 不清理 `renderer/public/tasks`（T4.1 只做新链路隔离）
3. 不修改任何 Remotion 组件的视觉设计（T4.4 仅改数据注入与组件选择）
4. 不修改 LLM prompt 文本内容
5. 不在 P1–P3 阶段动 `src/db.py::create_task` 的行为
6. 不针对单个 `task_id` 写救援脚本（一律走 `runner --resume`）
7. **不修复存量静默失败违例**（只建基线、只禁新增；清理另立任务）
8. **不在调用点硬编码 `max_tokens`**（唯一例外：`src/llm/client.py`）
9. **不用"提高 max_tokens 到无限大"解决问题**（必须按 profile 标定）
10. **不通过"再发一次"来验证重发**（先查 `send_ledger`）
11. **（v1.2）不在调用方代码里内联投料列表**（`NEWS_TASKS` / `TASKS_DATA` 只允许作为迁移期临时输入，T1.7 后一律走 manifest）
12. **（v1.2）不新增 `.env` 键却不接线**（声明即生效，否则就是死键，见 G8）
13. **（v1.2）不在代码里写音色/语速/产物文件名字面量**（唯一来源：`providers.*.env` 与 `contract.artifact_naming`）
14. **（v1.3）不假设"首次渲染会自动下载浏览器"**（必须先 `bootstrap.py --ensure`，见 T0.4）
15. **（v1.3）不为投料载体各写一套解析**（三载体共用 `manifest.schema.json`，见 T1.8）
16. **（v1.3）不用 `os.getenv(k, "默认值")` 掩盖必填键缺失**（缺失即 `EXIT=78`，见 T1.9）

### 6.1 提交边界（T0.3 固化，2026-11-09）

| 类别 | 处置 | 是否入库 |
|---|---|---|
| 本次方案新增/修改的契约文件（`spec_enhancement.md`、`model_inventory.md`、`scripts/bootstrap.py`、`.gitignore`、`pipeline.yaml` 等） | 按任务单 commit 入库 | ✅ 是 |
| 小品的核心修复（`landscape_guard.py` / `render_landscape.py` / `BilibiliLandscapeCover.tsx` / `run_publish_worker.py`） | **不单独提交**，由 T3.2 / T4.4 接管重做后随任务入库 | ⏳ 后置 |
| 一次性救火脚本（5 个） | 移入 `scripts/_deprecated/` 并 `.gitignore` 忽略 | ❌ 否 |
| 备份文件（`*.bak*` / `.backup/` / `.env.bak*`） | `.gitignore` 忽略，只留本地 | ❌ 否 |
| 历史产物（`output/` / `renderer/public/tasks/`） | 测试驱动产物，保持忽略 | ❌ 否 |

**执行铁律**：`git add` 必须**逐文件指名**，禁止 `git add -A` / `git add .`。

---

## 7. 异常与回滚协议

| 场景 | 动作 |
|---|---|
| 单任务失败 1 次 | 读日志定位 → 修正 → 重试 |
| 单任务失败 2 次 | 执行该任务 `回滚` → 标记 `⛔` → 停止，等人工确认 |
| GATE 不通过 | 回退到该 GATE 覆盖的最后一个任务，禁止跨越 |
| DB 出现约束冲突 | `cp .backup/20261109/pipeline_state.db data/` 回滚 |
| 渲染链路异常 | `git checkout renderer/ scripts/generate_from_news.py` |
| 怀疑重复推送 | 先查 `send_ledger`，禁止直接重发 |
| **换模型后出现故障** | 立即 `python3 scripts/model_switch_check.py` 定位哪个槽位不达标 → 回滚 `.env` 模型名 → 按附录 B 重走 |
| **出现空输出/静默跳过** | 查 `degraded_events` 表，按 `kind` 定位是 `truncated`（预算不足）还是 `skipped`（逻辑跳过） |
| **定时任务突然不执行** | 先查是否 `unpinned + 配置漂移`（QA#1 原样复现路径），再看脚本日志 |
| **（v1.2）横版改名后分发读不到文件** | `grep -rn "video_landscape_16_9" scripts/ src/ pipeline.yaml`，有残留即回退 T4.1 |
| **（v1.2）改了 `.env` 音色但成片没变** | 按 G8 死键排查：`grep -rn "TTS_VOICE" --include=*.py .` 若零命中，说明 T4.5 未接线 |

**通用回滚命令**：
```bash
cd ~/Downloads/workspace/team/project/autocontent-pipeline
git status --porcelain            # 先看清改了什么
cp .backup/20261109/pipeline_state.db data/pipeline_state.db
git checkout -- <file>
```

---

## 8. 风险登记

| ID | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| RK1 | `entity_key` 归一化产生 UNIQUE 冲突 | 高 | 中 | T3.1 必须 dry-run + 备份；冲突行进人工裁决 |
| RK2 | 内容指纹误杀"同一事件新进展" | 中 | 中 | L1 设 `warn`，硬拦截交给 L3 |
| RK3 | 换模板重渲导致帧哈希失效 | 中 | 低 | 由 L1/L3 兜底 |
| RK4 | hook 子进程拖慢流水线 | 低 | 低 | 实测 ~7ms/次；总增 <100ms |
| RK5 | 废弃 resend 脚本后运维不便 | 中 | 低 | 提供 `runner --resume` 等价能力 |
| RK6 | 历史 179 个 props 目录持续膨胀 | 确定 | 低 | 非目标 N2，另立任务 |
| **RK7** | `model_profile` 标定值随模型升级失效 | 高 | 高 | T1.6 自检脚本 + 附录 B 强制流程 |
| **RK8** | 静默失败基线白名单过大，新违例被掩盖 | 中 | 高 | 基线只允许存量，GATE-5 检查"新增=0"；白名单行数上限 50 |
| **RK9** | 外部真值校验（OCR/ffprobe）带来额外耗时 | 中 | 低 | 仅在出厂步与发送前各跑 1 次，单次 <3s |
| **RK10** | T3.2 改 Card 2.0 后旧卡片样式回退 | 中 | 中 | 先灰度 1 个素材验证移动端可复制，再全量 |
| **RK11** | （v1.2）`manifest` 最小契约过窄，真实选题信息不足导致扩写编造 | 中 | 高 | T1.7 的 `anyOf(body, source_url)` 强制至少一项事实源；`check_material` 步 `on_fail: block`，不达标不进入扩写 |
| **RK12** | （v1.2）横版产物改名后，历史脚本/文档仍按旧名 `video_landscape_16_9.mp4` 读取 | 中 | 中 | T4.1 验收强制 `grep` 零残留；历史 `output/` 产物不重命名（对齐 N1），只改代码引用 |
| **RK13** | （v1.3）`bootstrap.py --ensure` 在内网/弱网失败，浏览器仍装不上 | 中 | **高** | 提供离线包导入路径（预置 `.remotion` 目录打包分发）；`--dry-run` 作为启动闸口，缺项即拒跑而非硬闯 |
| **RK14** | （v1.3）`feishu_file` 载体引入新入口，可能被重放或投毒 | 中 | 中 | `inbox` 处理即移入 `.done/`，`msg_id` 幂等；过 L1 内容指纹；文件大小与 MIME 白名单 |

---

## 9. 变更日志

| 版本 | 日期 | 变更 | 作者 |
|---|---|---|---|
| v1.0 | 2026-11-09 | 初版：根因取证 + 三层去重 + 17 任务分段执行 | 小何 |
| **v1.1** | 2026-11-09 | 整合 `qa_xiaopin.md` 全部 12 类问题：新增 M1–M5 模型切换根因、§1.4 映射表、§4.4/4.5/4.6 三节设计约束、9 个新任务（T0.2/T1.4/T1.5/T1.6/T2.5/T2.6/T3.4/T4.4/T5.1）、P5 阶段、GATE-5、2 个 hook、附录 B/C、RK7–RK10 | 小何 |
| **v1.2** | 2026-11-09 | 内聚缺口审计 6 处修补全落位：**P-1** P4 转必做 + T4.1 提为 GATE-3 后第一优先；**P-2** 新增 T1.7 `manifest.schema.json` 物料契约；**P-3** 新增 T4.5 TTS 配置接线；**P-4** 新增 T4.6 审批渠道抽象；**P-5** 修正 `render_landscape` 假依赖；**P-6** 统一横版产物命名。配套新增 §1.6 内聚缺口审计（I1–I7 实测）、§4.7 内聚边界、G7/G8、不做清单 11–13、RK11–RK12、附录 A 契约扩充；任务数 27 → **30** | 小何 |
| **v1.3** | 2026-11-09 | 交付面审计 4 处空缺落位：**T0.4** 运行时自举（Remotion 浏览器双路径缺失，阻断级）、**T1.8** 投料通道实现（cli/stdin/feishu_file 三载体共用 schema）、**T1.9** 配置面收敛（`REMOTION_DIR` 死键 + 4 键未声明 + fail-fast `EXIT=78`）、**T4.7** 出片与发片解耦（`output_only`）。配套新增 §1.7 交付面缺口审计（G-A~G-E，9 判据 6 达标）、§4.9 交付面契约、G9/G10、不做清单 14–16、RK13–RK14、GATE-1 扩至 T1.1–T1.9；任务数 31 → **35** | 小何 |
| **v1.2-接管** | 2026-11-09 | 将小品已落地修复纳入本文档改动范围（不单独分支）：新增 **§4.8 接管原则**（R1 止血≠达标 / R2 静默即缺陷 / R3 逻辑不下沉 / R4 双实现收敛）+ **附录 D 文件状态台账**；新增 **C7**（双入口并存、A3 抓不到错版）、**C8**（三卡静默 null + `as any`）、**C9**（`_post_to_card2` 未下沉 + 硬编码 chat_id）；新增 **T0.3** 工作区卫生与提交边界；重写 **T3.2**（+接管重做 2 项）、**T4.4**（+入口收敛/docstring/归一化/产物名/三卡重做，共 8 项）；任务数 30 → **31** | 小何 |

---

## 附录 A · `pipeline.yaml` 目标全文（v1.3）

```yaml
schema_version: "1.0"

pipeline:
  id: "autocontent"
  name: "科技短视频生产流水线"
  version: "1.3.0"
  description: "从收到物料到竖版/横版成片的声明式流水线。只声明契约，不含业务逻辑。"

requires:
  python: ">=3.10"
  binaries: [ffmpeg, ffprobe, npx, tesseract]
  renderer: "remotion>=4"
  browser:                              # v1.3（T0.4）：不声明浏览器 = 首次渲染联网下载 150MB
    engine: chrome-headless-shell
    probe: ["renderer/node_modules/.remotion", "~/Library/Caches/remotion"]
    ensure_cmd: "npx remotion browser ensure"
  fonts: [Noto Sans CJK SC]             # v1.3（T0.4）：缺字体导致 OCR bbox 判据失效

contract:
  isolation:
    run_root: "output/{date}/{run_id}"
    forbid_shared_dirs: ["renderer/public/tasks"]
    template_vars: [date, run_id, task_id, account, aspect]
  artifact_naming:                       # v1.2（P-6）：产物名唯一来源，代码不得各自命名
    vertical:  {video: "video.mp4",          cover: "cover.png"}
    landscape: {video: "video_16_9.mp4",     cover: "cover_16_9.png"}
    enforce_single_source: true
  material_contract:                     # v1.2（P-2）：物料契约唯一来源
    schema: manifest.schema.json
    forbid_inline_task_lists: ["NEWS_TASKS", "TASKS_DATA"]
  config_binding:                        # v1.2（P-3/P-4）：声明即生效，禁止死键
    require_all_env_keys_consumed: true
    forbid_hardcoded_voice: true
    approval_modes: [feishu, cli, auto]
    fail_fast_missing: true              # v1.3（T1.9）：必填键缺失启动即退出
    exit_code_missing: 78
    required_env:                        # v1.3（T1.9）：runner 第 0 步校验，缺一即拒跑
      - LLM_BASE_URL
      - LLM_API_KEY
      - LLM_MODEL_MAIN
      - LLM_MODEL_FAST
      - TTS_VOICE_TECH
      - TTS_VOICE_NEWS
      - TTS_RATE
      - SQLITE_DB_PATH
    optional_env:                        # v1.3（T1.9）：只在对应分支需要，缺失不阻断
      - FEISHU_APP_ID
      - FEISHU_APP_SECRET
      - FEISHU_APPROVAL_CHAT_ID
      - XHS_PROBE_PROXY
      - XHS_PROBE_COOKIE
      - TWITTER_PROXY
      - TWITTER_CT0
      - TWITTER_AUTH_TOKEN
      - AUTOCONTENT_ENV_FILE
      - AUTOCONTENT_DATA_DIR
      - AUTOCONTENT_ALERTS_FILE
      - FEISHU_DEFAULT_CHAT_ID
  runtime_bootstrap:                     # v1.3（T0.4）：运行时自举，缺项拒跑
    script: scripts/bootstrap.py
    enforce_on_start: true
    forbid_network_on_first_render: true
  intake_carriers:                       # v1.3（T1.8）：三载体共用一份 schema
    allowed: [cli, stdin, feishu_file]
    single_schema: manifest.schema.json
    forbid_per_carrier_field_fill: true
  model_decoupling:
    forbidden_literals: ["deepseek", "gemini", "gpt", "claude", "qwen"]
    resolve_via: "providers"
  model_switch:
    require_profile: true
    forbid_caller_max_tokens: true
    fail_on_truncated: true
    fail_on_empty: true
    preflight: scripts/model_switch_check.py
  silence_tolerance:
    forbid_except_pass: true
    forbid_silent_skip: true
    must_log_degraded: true
  artifact_ownership:
    enforce: strict
  deduplication:
    enabled: true
    layers: [content, visual, send_ledger]
    key_normalization:
      strip_timestamp_suffix: '^(.+?)-\d{10}$'
      strip_emoji_code: true
      strip_punct: true
    ledger_idempotent: true
    write_after_external_confirm: true
    on_duplicate: block

providers:
  llm.main:
    kind: llm
    env: {base_url: LLM_BASE_URL, api_key: LLM_API_KEY, model: LLM_MODEL_MAIN}
    schema_out: DraftScriptPayload
    profile:
      # T1.4/T1.6 实测标定：reasoning 40~2215（强依赖 prompt 形态），真实 draft ≈4000 visible token
      max_tokens: 8192          # = reasoning_reserve(2560) + visible(5632)
      reasoning_reserve: 2560
      timeout_sec: 120
      json_mode: true
      min_output_chars: 80
      expected_latency_sec: 25
  llm.fast:
    kind: llm
    env: {base_url: LLM_BASE_URL, api_key: LLM_API_KEY, model: LLM_MODEL_FAST}
    profile:
      # T1.4 实测：1024 时 reasoning 顶满→finish=length/content 空；2048 正常
      max_tokens: 2048
      reasoning_reserve: 1536
      timeout_sec: 120
      json_mode: false
      min_output_chars: 1
      expected_latency_sec: 20
  tts.tech:
    kind: tts
    env: {voice: TTS_VOICE_TECH, rate: TTS_RATE}
    profile:
      timeout_sec: 90
  tts.news:                             # v1.2 新增（P-3）：非 tech 账号槽位，禁止代码内 if/else 选音色
    kind: tts
    env: {voice: TTS_VOICE_NEWS, rate: TTS_RATE}
    profile:
      timeout_sec: 90
  feishu:                               # v1.3 新增（T1.9）：原本被代码读却未声明
    kind: saas
    env: {app_id: FEISHU_APP_ID, app_secret: FEISHU_APP_SECRET, approval_chat_id: FEISHU_APPROVAL_CHAT_ID}
    profile:
      timeout_sec: 30
  db:                                   # v1.3 新增（T1.9）
    kind: storage
    env: {path: SQLITE_DB_PATH}
    profile:
      timeout_sec: 5

inputs:
  material:
    type: json
    required: true
    schema: manifest.schema.json        # v1.2：物料契约唯一来源（P-2）
    carriers: [cli, stdin, feishu_file] # v1.3（T1.8）：三载体，同一份 schema
    min_fields: [title]                 # 其余由 llm.main 扩写
    any_of: [[body], [source_url]]      # 至少一项事实来源，禁止凭空编造
    prompt: "把物料发给我（标题/正文/来源 URL/截图），其余由流水线决定"
  account:
    type: string
    default: "douyin-tech-main"
  aspects:
    type: list
    default: [vertical, landscape]

# 封面卡片数据源：消除组件内 [0,1,3] 魔数（T4.4 #6，对标 C8）
cover:
  card_source: [0, 1, 3]        # 取 scenes 的哪几镜作为右侧三卡
  missing_policy: warn          # 缺 highlight_text 时告警占位，禁止静默消失
  subtitle_source: resource_keyword

steps:
  - id: intake
    name: "接收消息"
    run: builtin.intake
    produces: ["manifest.json"]
    params:
      carrier: "${INTAKE_CARRIER:-cli}"   # cli | stdin | feishu_file（v1.3 / T1.8）
      schema: manifest.schema.json
    dedupe: content
    before_hooks: [hook.envelope_validate]
    after_hooks: [hook.persist_task, hook.content_fingerprint]

  - id: check_material
    name: "检查资料"
    run: builtin.check_material
    reads: ["manifest.json"]
    produces: ["material_check.json"]
    before_hooks: [hook.dedupe_entity]
    after_hooks: [hook.sensitive_scan, hook.policy_precheck, hook.silence_guard]
    on_fail: block

  - id: expand_script
    name: "扩写生成脚本"
    uses: llm.main
    reads: ["manifest.json", "material_check.json"]
    produces: ["draft_script.json"]
    schema: DraftScriptPayload
    retry: {max: 2, on_schema_error: true}
    before_hooks: [hook.material_bundle, hook.prompt_render]
    after_hooks: [hook.sensitive_scan, hook.title_length_guard, hook.silence_guard]

  - id: render_vertical
    name: "生成竖版视频"
    run: builtin.render
    params: {aspect: vertical, composition: GeekShortVideo, width: 1080, height: 1920}
    reads: ["draft_script.json"]
    produces: ["video.mp4", "cover.png", "xhs_cards/"]
    before_hooks: [hook.sensitive_scan, hook.material_check, hook.expand_narration]
    after_hooks: [hook.frame_fingerprint, hook.frame_sample_check, hook.external_truth, hook.audio_duration_check]

  - id: render_landscape
    name: "生成横版视频"
    run: builtin.render
    params: {aspect: landscape, composition: BilibiliLandscapeVideo, width: 1920, height: 1080}
    reads: ["draft_script.json"]        # v1.2 修正（P-5）：横版只读 props，video.mp4 是假依赖
    requires_props: true                # props 由 TTS 对齐步产出，不依赖竖版成片
    produces: ["video_16_9.mp4", "cover_16_9.png"]
    before_hooks: [hook.landscape_precheck]
    after_hooks: [hook.frame_fingerprint, hook.external_truth, hook.landscape_guard]

  - id: approval
    name: "人审"
    type: gate
    params:
      mode: "${APPROVAL_MODE:-auto}"    # feishu | cli | auto（v1.2 / P-4）
      message: "请确认成片"
    options: [approve, reject]
    on_reject: abort

  - id: dispatch
    name: "分发"
    run: builtin.dispatch
    params:
      enabled: true                                # false ⇒ 只出片（v1.3 / T4.7）
      mode: "${DISPATCH_MODE:-publish}"             # publish | output_only
    reads: ["video.mp4", "video_16_9.mp4"]
    dedupe: send_ledger
    before_hooks: [hook.provenance_check, hook.external_truth, hook.card_render, hook.send_ledger]
    after_hooks: [hook.audit_log]

hooks:
  hook.envelope_validate:
    type: observe
    impl: scripts/hooks/envelope_validate.py
  hook.persist_task:
    type: transform
    impl: scripts/hooks/persist_task.py
  hook.dedupe_entity:
    type: gate
    impl: scripts/hooks/dedupe_entity.py
    on_fail: block
  hook.sensitive_scan:
    type: gate
    impl: scripts/hooks/sensitive_scan.py
    on_fail: block
  hook.policy_precheck:
    type: gate
    impl: scripts/hooks/policy_precheck.py
    on_fail: block
  hook.material_bundle:
    type: transform
    impl: scripts/hooks/material_bundle.py
  hook.prompt_render:
    type: transform
    impl: scripts/hooks/prompt_render.py
  hook.title_length_guard:
    type: gate
    impl: scripts/hooks/title_length_guard.py
    on_fail: warn
  hook.material_check:
    type: gate
    impl: scripts/hooks/material_check.py
    on_fail: block
  hook.expand_narration:
    type: transform
    impl: scripts/hooks/expand_narration.py
  hook.content_fingerprint:
    type: gate
    impl: scripts/hooks/content_fingerprint.py
    on_fail: warn
  hook.frame_fingerprint:
    type: transform
    impl: scripts/hooks/frame_fingerprint.py
  hook.frame_sample_check:
    type: gate
    impl: scripts/hooks/frame_sample_check.py
    on_fail: block
  hook.audio_duration_check:
    type: gate
    impl: scripts/hooks/audio_duration_check.py
    on_fail: warn
  hook.landscape_precheck:
    type: gate
    impl: scripts/hooks/landscape_precheck.py
    on_fail: block
  hook.landscape_guard:
    type: gate
    impl: scripts/landscape_guard.py
    on_fail: block
  hook.provenance_check:
    type: gate
    impl: scripts/hooks/provenance_check.py
    on_fail: block
  hook.card_render:
    type: transform
    impl: src/feishu/card_builder.py
  hook.send_ledger:
    type: gate
    impl: scripts/hooks/send_ledger.py
    on_fail: block
  hook.silence_guard:
    type: gate
    impl: scripts/hooks/silence_guard.py
    on_fail: warn
  hook.external_truth:
    type: gate
    impl: scripts/hooks/external_truth.py
    on_fail: block
  hook.audit_log:
    type: observe
    impl: scripts/hooks/audit_log.py
```

---

## 附录 B · 模型切换 SOP（v1.1 新增，防 QA#1/#2 复发）

> **任何**模型变更（换型号 / 换网关 / 换版本 / 换 provider）都必须完整走完本流程。适用于全部项目，不限于本仓。

### B.1 切换前（Pre-flight）

| # | 检查项 | 命令 / 动作 | 通过标准 |
|---|---|---|---|
| 1 | 目标模型在网关真实存在 | `curl -s $LLM_BASE_URL/models -H "Authorization: Bearer $LLM_API_KEY"` | 目标模型 id 出现在列表中 |
| 2 | 盘点所有模型引用点 | `grep -rn "LLM_MODEL\|max_tokens\|model=" --include=*.py src scripts` | 无遗漏的硬编码 |
| 3 | 标定 token 预算 | T1.4 的实测脚本 | `max_tokens ≥ reasoning + 正文预算` |
| 4 | 跑自检 | `python3 scripts/model_switch_check.py` | `EXIT=0` |

### B.2 切换中

1. 只改 `.env`（`LLM_MODEL_MAIN` / `LLM_MODEL_FAST`），**不改代码**
2. 同步更新 `pipeline.yaml` 中对应 `profile`（`expected_latency_sec` / `max_tokens`）
3. **定时任务同步处理**：`no_agent` 化 或 显式 pin 模型（QA#1 教训）

### B.3 切换后（Post-flight，必须全绿）

| # | 检查项 | 通过标准 |
|---|---|---|
| 1 | 自检脚本 | `EXIT=0` |
| 2 | 评论分类漏斗 dry run | 至少 1 条真实评论判为 A/B 而非 D |
| 3 | 一次完整出片 | 竖版+横版均通过 `external_truth` |
| 4 | 定时任务一轮 | 状态 `completed`，非 `error` |
| 5 | `degraded_events` | 24h 内无 `truncated` / `empty_result` |

### B.4 失败回滚

```
1. .env 模型名回滚
2. git checkout pipeline.yaml
3. 定时任务恢复 pin
4. 记录到 QA 文档（现象/根因/证据）
```

---

## 附录 C · 工程环境坑清单（v1.1 新增，QA#12 沉淀）

| 坑 | 表现 | 规避 |
|---|---|---|
| 推理模型 token 预算 | `finish=length, content=''` | 见 §4.4，预算含 reasoning |
| macOS 无 `timeout` | `command not found` | 用 `cmd \| tail` + `${PIPESTATUS[0]}` |
| 并行工具调用报错 | `'DaemonThreadPoolExecutor' object has no attribute '_initializer'` | 严格串行单调用 |
| tesseract 读 `/tmp` | `fopenReadStream: failed` | 输出到相对路径或 `/private/tmp` |
| 飞书 post `code_block` | API success 但移动端不渲染 | 强制 Card 2.0 |
| 飞书同名文件缓存 | 重发命中旧缓存 | 文件名带 `content_hash[:8]` |
| `content_tasks` 列名 | `no such column: draft_script` | 实际是 `draft_script_json` |
| 裸数字回复语义 | 回「1」被当成方案选项 | 明确区分"条目号"与"方案号" |
| 返回值类型误判 | `'str' object has no attribute 'get'` | `isinstance(resp, str)` 先判 |
| 二维码登录频控 | 连续「已过期」 | 改用手机号 + 验证码 |
| CDP 端口空置 | `127.0.0.1:9222` 无监听 | `ensure_ready()` 自动拉起 |
| 登录态未固化 | 重启即掉登录 | `storage_state` 落盘 + 回灌 |

---

## 附录 D · 既有修复文件状态台账（v1.2 新增）

> 基准：2026-11-09 `git status --porcelain` 实拍。**小品的修复是本次优化的起因，一并纳入本方案改动范围，不单独开分支。**
> 处置口径见 §4.8 四条规则（R1 止血≠达标 / R2 静默即缺陷 / R3 逻辑不下沉 / R4 双实现收敛）。

### D.1 需入主线（核心修复，4 个）

| 文件 | git 状态 | 小品改动内容 | 内聚评估 | 处置 | 任务 |
|---|---|---|---|---|---|
| `renderer/src/components/BilibiliLandscapeCover.tsx` | `M` 已改未提交 | 副标题改 `props.resource_keyword`；右侧三卡改取 `props.scenes[0/1/3].highlight_text`；删除 3 条硬编码营销话术 | ⚠️ **方向对，实现不合规**：`if (!text) return null` 静默 + `[0,1,3]` 魔数 + `as any` | 保留改法，重做 3 处（C8） | **T4.4 #6** |
| `scripts/run_publish_worker.py` | `M` 已改未提交 | 新增 `_post_to_card2()`；`msg_type("post")`→`"interactive"`+`schema:"2.0"`；补 `resp.code != 0` 告警 | ⚠️ **方向对**：结构转换埋在发送函数（违反 R3）；`os.getenv(..., "ou_bc83...")` 硬编码兜底 | 保留改法，下沉 hook + 删兜底（C9） | **T3.2 #7 #8** |
| `scripts/landscape_guard.py` | `??` **未跟踪** | 144 行：ffprobe 分辨率 + **OCR bbox 横向跨度 ≥75%** + OCR 命中主文案；失败即 `False` | ✅ **判据正确**，实测 0.904 通过、1080×1920 拒绝；⚠️ 仅 CLI，未适配 hook 协议；docstring 写「≥80%」与实现不符 | 保留，转 hook 协议 + 修 docstring | **T4.4 #2 #4** |
| `scripts/render_landscape.py` | `??` **未跟踪** | 73 行：横屏单入口，固定 `BilibiliLandscapeVideo`/`BilibiliCoverStill`，渲染后强制过 guard，失败抛 `LandscapeGuardError` | ✅ **失败即抛，符合 §4.5**；⚠️ props 默认读共享目录 `renderer/public/tasks/`；产物名 `video_landscape_16_9.mp4` 不合契约；subprocess 无 timeout | 保留，改造 3 处；并废弃旧入口 | **T4.1 + T4.4 #1 #8** |

### D.2 不入主线（一次性脚本，5 个）

| 文件 | git 状态 | 用途 | 处置 | 任务 |
|---|---|---|---|---|
| `scripts/resend_sherlock_approval.py` | `??` | 硬编码 `TASK_ID` 重发历史成片 —— **正是"飞书收到历史视频"的直接工具** | 移入 `scripts/_deprecated/` | **T3.3** |
| `scripts/dispatch_douyin_only.py` | `??` | 单腿补投（B站已发、抖音补发） | 不入主线 | T0.3 |
| `scripts/submit_douyin_code.py` | `??` | 抖音投稿验证 | 不入主线 | T0.3 |
| `scripts/check_douyin_works.py` | `??` | 只读核对抖音作品 | 不入主线 | T0.3 |
| `scripts/generate_rigorous_holiday.py` | `??` | 一次性节日选题生成 | 不入主线 | T0.3 |

### D.3 忽略（备份文件，3 个）

| 文件 | 处置 |
|---|---|
| `.env.bak.20261108_160319` | `.gitignore` 追加 `*.bak.*`（T0.3） |
| `config/platform_policies.yaml.bak_20261108_1905` | 同上 |
| `scripts/run_publish_worker.py.bak_20261109_0715` | 同上 |

### D.4 已提交但需废弃（1 个）

| 文件 | 现状 | 问题 | 处置 |
|---|---|---|---|
| `scripts/render_landscape_bilibili.py` | 已提交 `31812d5`，217 行 | A1 分辨率 / A2 封面分辨率 / **A3 首帧与封面像素差** —— 错版片首帧与封面同图，像素差恒 0，**A3 必然通过，抓不到目标缺陷**；且 `generate_bilibili_horizontal_demo.py:122` 仍 import 它 | 头部加 `DEPRECATED`，改调用方指向 `render_landscape.py`（T4.4 #1） |

### D.5 一句话结论

> 小品的 4 个修复**方向全部正确**，问题只在"止血"与"内聚"的差别：3 处静默兜底、1 处逻辑下沉、1 处共享目录依赖、2 处产物命名不合契约、1 组双实现并存。
> 这些**不构成推翻理由**，但**也不构成跳过 T3.2 / T4.4 的理由**。
