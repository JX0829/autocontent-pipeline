<div align="center">

# 🚀 AutoContent Pipeline

**End-to-End Automated Tech Short Video Production & Multi-Platform Distribution**
**端到端的硬核科技短视频自动化生成、渲染与多平台分发流水线**

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](LICENSE)
[![Related Project: study-schedule](https://img.shields.io/badge/Companion%20Project-study--schedule-cyan.svg)](https://github.com/JX0829/study-schedule)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Remotion 4.x](https://img.shields.io/badge/Remotion-4.x-0B84F3.svg?logo=react&logoColor=white)](https://www.remotion.dev/)
[![HITL Approval](https://img.shields.io/badge/HITL-Human--in--the--Loop-00D6B9.svg)](#)

<p align="center">
  <a href="#english">English</a> •
  <a href="#chinese">中文</a> •
  <a href="#architecture">Architecture / 架构</a> •
  <a href="#quickstart">Quick Start / 快速上手</a> •
  <a href="#docs">Docs / 文档地图</a>
</p>

</div>

---

<a name="english"></a>
## 🌐 English Overview

**AutoContent Pipeline** is a declarative, spec-driven short video automation engine for tech creators.
It turns a raw material JSON into a rendered vertical + landscape video pair, delivers them to a human
reviewer over IM, and only publishes to social platforms **after an explicit human approval**.

Everything — steps, providers, hooks, gates — is declared in [`pipeline.yaml`](pipeline.yaml). Code that
disagrees with the declaration fails the gate instead of silently drifting.

### 🌟 Key Highlights

- **Declarative 9-step pipeline**: `intake → check_material → expand_script → align_tts → render_vertical / render_landscape → deliver → approval → publish`, all wired through `pipeline.yaml`.
- **Provider slots, not if/else**: `llm.main`, `llm.fast`, `tts.tech`, `tts.news`, `approval.feishu`, `approval.cli` — swapping an implementation is a config change, not a code change.
- **Approval is pluggable and style-bound**: each approval provider owns its own card style (`approval_v2` / `approval_minimal`). Adding a channel = write a provider class → register → declare a slot. See `RUNBOOK.md` §4.3.
- **Triple-gated publishing** (default **off**): ① `publish.enabled` + non-empty `platforms` ② `approval.json` must be `approve` ③ `send_ledger` idempotency. Flipping the config alone can never publish without approval.
- **Code-as-Video (Remotion 4.x)**: 100% React/TypeScript rendering, vertical 1080×1920 + landscape 1920×1080 rendered in parallel (~189s full chain).
- **Fail-closed by default**: missing audio track, wrong aspect ratio, expired login, missing original-declaration checkbox — every one of them aborts instead of shipping a silent bad video.
- **Machine-checkable acceptance**: `scripts/verify_all.sh` (`--fast` / default / `--full`) with GATE-1 declaration contract, GATE-2 ledger idempotency, GATE-3 end-to-end no-resend.

### 🛡️ Platform Matrix (as-built, 2026-11)

| Platform | Implementation | Public | AI disclosure | Original |
|---|---|---|---|---|
| Douyin | `src/providers/douyin.py` (Playwright CDP) | ✅ | ✅ auto-checked | — |
| Bilibili | `src/providers/bilibili.py` + `scripts/bilibili_live_uploader.py` | ✅ | ✅ `creation_statement_id=1` | ✅ `original=True` |
| WeChat Channels | `src/providers/channels_cdp.py` (Playwright) | ✅ | ❌ **no such control on web** | ✅ fail-closed |

> Note: platform publishing runs through dedicated CLIs and is **not** yet wired into the runner's `publish` step by default (`publish.enabled: false`).

### 📁 Companion Project
- [**study-schedule**](https://github.com/JX0829/study-schedule) — daily task & reward system for kids (React + Vite, multi-channel notifications).

---

<a name="chinese"></a>
## 🇨🇳 中文说明

**AutoContent Pipeline** 是一套声明式的科技短视频自动化流水线：一条物料 JSON 进来，竖版 + 横版成片出去，
经飞书人工审批后**才**允许发到各平台。

流程的全部结构（步骤链、provider 槽位、钩子、门禁）都写在 [`pipeline.yaml`](pipeline.yaml) 里。
代码和声明不一致会被门禁拦下，不允许"悄悄漂移"。

### 🌟 核心特性

- **声明式 9 步流水线**：`intake → check_material → expand_script → align_tts → render_vertical / render_landscape → deliver → approval → publish`
- **Provider 槽位制，不是 if/else**：`llm.main` / `llm.fast` / `tts.tech` / `tts.news` / `approval.feishu` / `approval.cli`，换实现改配置即可
- **审批可插拔，且卡片样式跟渠道绑定**：飞书审批自带 `approval_v2`（含发布矩阵）/ `approval_minimal`（免刷屏）两套 Card 2.0 样式；
  新增渠道 = 写 provider 类 → register → 在 yaml 声明槽位三步，流程代码一行不用改（详见 `RUNBOOK.md` §4.3）
- **发布三重门禁（默认关闭）**：① `publish.enabled` + `platforms` 非空 ② `approval.json` 必须是 `approve` ③ `send_ledger` 幂等；
  只改配置而不走审批，一个平台都发不出去
- **代码即视频（Remotion 4.x）**：竖版 1080×1920 + 横版 1920×1080 并行渲染，全链实测约 189 秒
- **默认 fail-closed**：缺音轨、比例错版、登录态过期、找不到原创勾选框 —— 每一种都直接失败，不允许产出"看起来成功"的坏片
- **可机器验收**：`scripts/verify_all.sh`（`--fast` / 默认 / `--full`）含 GATE-1 声明层契约、GATE-2 台账幂等、GATE-3 端到端不重发

### 🛡️ 三平台现状（2026-11 实机）

| 平台 | 实现 | 公开发布 | AI 声明 | 原创 |
|---|---|---|---|---|
| 抖音 | `src/providers/douyin.py`（Playwright CDP） | ✅ | ✅ 自动勾选 | — |
| B站 | `src/providers/bilibili.py` + `scripts/bilibili_live_uploader.py` | ✅ | ✅ `creation_statement_id=1` | ✅ `original=True` |
| 微信视频号 | `src/providers/channels_cdp.py`（Playwright） | ✅ | ❌ **Web 端无此控件** | ✅ fail-closed |

> **封面口径**：封面帧已贴进成片首帧（播 0.5s），三平台都靠「首帧即封面」自动取用，
> 不再调用各平台的封面上传接口。
>
> 平台发布目前走独立 CLI，默认**没有**挂进 runner 的 `publish` 步（`publish.enabled: false`）。
> 视频号三个真机坑（直连被弹回、wujie iframe、原生文件选择器）见 `RUNBOOK.md` §6.1，照着做别自己摸索。

### 🆕 最近升级（2026-11 中旬）

> 下面这些都已在主干（`main` = `develop`），每一行都有机器门禁兜着，改之前先看 `RUNBOOK.md`。

| 升级 | 关键点 | 门禁 |
|---|---|---|
| **封面链路重构** | ① 封面帧直接**贴到成片最前面播 0.5s**（首帧即封面）② 底图取自**无字素材/背景 loop**，成片里烧的「锐评/备忘录」不再进封面 ③ 版式**居中 + 下移 32 + 边距 ≥64 + 字号 76/28/20** ④ 上下各 240px 纯深色底 + 70px 羽化 ⑤ **三个平台都不再单独上传封面**（抖音封面控件长期不可用） | `test_cover_prepend.py`、`test_cover_34_safe.py`、`audit_cover_standard.py` |
| **封面规范模板无关** | 版式常量集中在 `renderer/src/tech/coverFrame.ts`；新模板封面必须复用该模块，旧版式（Bilibili/课文）已标 `LEGACY-COVER` 显式认账 | `audit_cover_standard.py` |
| **竖版安全边距 100px** | 抖音信息流左右各裁约 9%，原 88px 裁完贴边；竖版 4 处文字层统一 `SAFE_X_VERTICAL=100`，横版保持 88 | `audit_safe_area.py` |
| **RUNBOOK 压缩为执行手册** | 1288 行 → 339 行，只留可直接照抄的命令；完整版归档 `docs/RUNBOOK-20261118-full.md` | `verify_runbook.py`（26 条命令逐条核对脚本/参数/解释器） |
| **`--publish` 三语义** | 默认真发 / `--publish-dry-run` 只预检 / `--stop-before-publish` 停发表前；dry-run 不再把审批卡刷成「已发布」 | 单测 + 实测回执 |
| **重渲正确姿势** | 同物料+同账号按 `content_hash` 复用 run_id，换 run-id 也拦不住；必须 `--resume --from-step render_vertical` | `RUNBOOK.md` §2.3 |
| **抖音标题/正文/话题** | 标题进独立「作品标题」框；正文与话题**一次原子插入**；回读比对先切掉话题段 | `test_douyin_topics_contract.py` |
| **视频号收回流水线** | 删 `publish_channels.py`，只读排障用 `probe_channels.py`；账号级 `headless` 生效但**视频号禁止无头**；`objectId` 当凭证 + 回执三态（published/unverified/failed） | `audit_channels_headful.py`、`test_channels_stability.py`、`audit_publish_entrypoints.py` |
| **素材轮换真正生效** | ① bg 随机单位改成**视觉族**（旧实现把池缩成带 `_loop` 的族 ⇒ 每条片子都是同一张电路板）② 新增 `mid` 幕间转场（场景交界 0.5s 覆盖）与 `end` 片尾收尾（最后 1.2s 覆盖），**只覆盖不改时间轴**，口播不脱轨 ③ 语义域判不出时按全池随机，不再留空 | `test_asset_rotation.py` |
| **代码版本自愈（谁跑都是最新）** | `runner` 启动先追平 `origin/develop`：干净就自动 `merge --ff-only`，脏工作区/有未推提交则 `STALE_CHECKOUT` 直接拒跑（不再静默吃旧代码）；`--no-sync` 或 `PIPELINE_SKIP_SYNC=1` 可关 | `test_checkout_sync.py`（6 项） |
| **横版原生 16:9 副本恢复** | `efa3a98` 曾把横版素材替换逻辑删掉，11-18 起横版又回到「竖版素材裁 16:9」（1080×1920 裁完只剩 1080×608）。已恢复并把 `mid/end` 纳入：开场/背景/幕间/片尾都优先用 `*_landscape.mp4`，无副本才回落并打日志 | `test_landscape_assets.py`（11 项） |
| **旧 Geek 模板下线** | `GeekShortVideo` 不再有任何入口（`DEFAULT_VERTICAL_COMPOSITION` → `TechStoryVideo`，T1~T5 标 `retired` + `enabled:false`），文件保留可回滚 | `test_asset_rotation.py` |
| **物料硬约束** | `title` 用物料原值（>20 字报 `TITLE_TOO_LONG`）；`tags` = 物料 + 生成去重取前 6（物料在前，超限报 `TAGS_OVER_LIMIT`） | `test_material_binding.py` |

### 📁 关联姊妹项目
- [**study-schedule**](https://github.com/JX0829/study-schedule) — React + Vite + 多通道即时通知的儿童习惯养成与任务规划系统

---

<a name="architecture"></a>
## 🏗️ Architecture / 架构与控制流

```text
material.json
     │
     ▼
[intake] ──► [check_material] ──► [expand_script] ──► [align_tts]
  物料校验        物料完整性          LLM 扩写(分镜)      Edge-TTS 字级对齐
                                                            │
                              ┌─────────────────────────────┴──────────────┐
                              ▼                                            ▼
                     [render_vertical]                            [render_landscape]
                       Remotion 1080×1920                           Remotion 1920×1080
                              └─────────────┬──────────────────────────────┘
                                            ▼
                                     [deliver]  飞书原片 + 素材卡（竖横各 2 件）
                                            ▼
                                    [approval]  人工裁决（provider 槽位，超时=reject）
                                            ▼
                                     [publish]  三重门禁（默认关闭）
                                            ▼
                      src/providers/*  →  抖音 / B站 / 微信视频号
```

### Directory Structure / 核心目录

```text
autocontent-pipeline/
├── pipeline.yaml                 # 唯一执行依据：步骤 / provider 槽位 / 钩子 / 契约
├── RUNBOOK.md                    # 操作手册（怎么跑、怎么排障、新增 provider 三步走）
├── spec_enhancement.md           # 声明式改造的执行规格与任务台账
├── AUTOCONTENT-PIPELINE-SPEC.md  # 视觉排版与全流程规范
├── config/
│   ├── accounts.yaml             # 平台账号矩阵（直播/发布开关、方向、声明、作者首评）
│   ├── platform_policies.yaml    # 差异化文本政策与 AIGC 合规
│   └── sensitive_words.txt       # DFA 过滤词库
├── data/                         # 状态机 DB、浏览器 Profile（不进 git）
├── renderer/                     # Remotion 4.x 渲染引擎
├── scripts/
│   ├── verify_all.sh             # 统一验收入口（--fast / 默认 / --full）
│   ├── gate1.sh gate2.sh gate3.sh
│   ├── send_run_artifacts.py     # 交付契约（4 件缺一即拒绝）
│   ├── digest_to_material.py     # 速报 → 物料
│   ├── channels_login.py         # 视频号扫码登录 / 二维码出图
│   ├── probe_channels.py         # 视频号只读探针（只探不发）
│   ├── publish_douyin_cdp.py / bilibili_live_uploader.py  # 单平台排障 CLI（默认拒绝；发布实现在 src/pipeline/executors）
│   └── hooks/                    # pipeline.yaml 声明的钩子实现
└── src/
    ├── approval/                 # 审批 provider 槽位（base / registry / providers / styles）
    ├── pipeline/                 # runner、executors、hooks 引擎、contracts
    ├── providers/                # 平台适配器（douyin / bilibili / channels_cdp）
    ├── feishu/                   # 飞书消息、卡片构建、发送服务
    ├── crawlers/                 # 热点抓取适配器
    ├── safety/                   # DFA 过滤与合规漏斗
    ├── tts/                      # TTS 槽位与时间戳对齐
    └── utils/                    # 简介生成、作者首评、归一化
```

---

<a name="quickstart"></a>
## 🛠️ Quick Start / 快速上手

### 1. Installation / 安装依赖

```bash
pip install -r requirements.txt
cd renderer && npm install && cd ..
```

### 2. Environment / 环境变量

```bash
cp .env.example .env
```

| 变量 | 用途 |
|---|---|
| `DEEPSEEK_API_KEY` | 分镜/文案生成 |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 审批与通知应用凭据 |
| `FEISHU_APPROVAL_CHAT_ID` | 审批卡片与原片的投放目标 |
| `APPROVAL_MODE` | `auto`（默认，有飞书凭据走飞书）/ `feishu` / `cli` |
| `DISPATCH_MODE` | `output_only` 只出片不发片 |

### 3. Verify before you run / 先自检

```bash
bash scripts/verify_all.sh --fast    # 0.7s，静态扫描 5 项
bash scripts/verify_all.sh           # ~2min，+ GATE-1 声明层 + GATE-2 台账幂等
bash scripts/verify_all.sh --full    # 真实渲染，实测约 6–7 分钟，+ GATE-3 端到端不重发
```

### 4. Run / 跑一条

```bash
# 只出片、不发片（第一次强烈建议用这个）
DISPATCH_MODE=output_only \
  python3 -m src.pipeline.runner --material materials/20261110_hive_mem.json --run-id my-test-1

# 正式跑：出片 → 飞书收到原片 + 审批卡片 → 人工确认
python3 -m src.pipeline.runner --material materials/20261110_hive_mem.json
```

先看执行计划（不调模型、不写库）：

```bash
python3 -m src.pipeline.runner --material materials/20261110_hive_mem.json --dry-run
```

产物落在 `output/<YYYYMMDD>/<run_id>/`：`video.mp4` / `video_16_9.mp4` / `cover.png` / `cover_16_9.png`
（交付契约 4 件缺一即拒绝发送）。

### 5. Publish / 发布

平台发布走独立 CLI，且受三重门禁约束（`publish.enabled` 默认 false）。命令见 `RUNBOOK.md` §6。

---

<a name="docs"></a>
## 📚 Docs / 文档地图

| 文档 | 读它来解决什么问题 |
|---|---|
| [`RUNBOOK.md`](RUNBOOK.md) | **操作手册**：怎么跑、产物在哪、三平台怎么发、幂等重跑、故障排查、**新增审批 provider 三步走（§4.3）**、视频号真机三坑（§6.1） |
| [`spec_enhancement.md`](spec_enhancement.md) | 声明式改造的**执行规格 + 任务台账**（35 项任务、四 GATE、附录含文件状态记录） |
| [`AUTOCONTENT-PIPELINE-SPEC.md`](AUTOCONTENT-PIPELINE-SPEC.md) | 视觉排版、文案与渲染规范 |
| [`pipeline.yaml`](pipeline.yaml) | 机器可读的唯一执行依据 |
| [`CHANGELOG.md`](CHANGELOG.md) | 变更记录 |

### 近期技术预研（spike）结论

| 主题 | 结论 | 落点 |
|---|---|---|
| 审批渠道抽象 | 从 `if/else` 改为 provider 槽位，超时/不可用一律 fail-closed，不偷偷降级 | `src/approval/` + `RUNBOOK.md` §4.2 |
| 审批卡片样式归属 | 样式属于渠道而非流程，下沉到 `styles.py` 并由 `profile.card_style` 绑定；未知样式名显式报错 | `RUNBOOK.md` §4.3 |
| 发布收口与门禁 | 顺序 `deliver → approval → publish`，三重门禁缺一不发，`publish.enabled` 默认关闭 | `pipeline.yaml` + `spec_enhancement.md` |
| 视频号 Web 端 | 直连发布页会被弹回首页；表单在 wujie iframe 内（Playwright 选择器失效）；`input[type=file]` 需 JS 点击 + `expect_file_chooser`；**Web 端无 AI 声明控件** | `RUNBOOK.md` §6.1 |
| 账号矩阵 | 一平台一账号；重复账号会导致卡片列两行、发布发两次 | `config/accounts.yaml` |

---

## 📄 License / 开源协议

Released under the [MIT License](LICENSE).
