<div align="center">

# 🚀 AutoContent Pipeline

**End-to-End Automated Tech Short Video Production & Dynamic Multi-Platform Distribution**  
**端到端的硬核科技短视频自动化生成、渲染与多平台动态分流发布流水线**

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](LICENSE)
[![Related Project: study-schedule](https://img.shields.io/badge/Companion%20Project-study--schedule-cyan.svg)](https://github.com/JX0829/study-schedule)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Remotion 4.x](https://img.shields.io/badge/Remotion-4.x-0B84F3.svg?logo=react&logoColor=white)](https://www.remotion.dev/)
[![Feishu Open Platform](https://img.shields.io/badge/Feishu-HITL%20Approval-00D6B9.svg)](https://open.feishu.cn/)

<p align="center">
  <a href="#english">English</a> •
  <a href="#chinese">中文</a> •
  <a href="#architecture">Architecture / 架构</a> •
  <a href="https://github.com/JX0829/study-schedule">🔗 Study-Schedule Repository</a>
</p>

</div>

---

<a name="english"></a>
## 🌐 English Overview

**AutoContent Pipeline** is an intelligent, developer-centric short video automation engine designed for tech content creators. It bridges the gap between breaking tech news and fully-rendered high-definition videos with human-in-the-loop (HITL) approval and dynamic multi-platform matrix distribution.

### 🌟 Key Highlights

- 🔍 **Multi-Source Trend Radar**: Seamlessly integrates GitHub Trending, Reddit Hot, V2EX Geek Topics, Xueqiu Tech & MediaCrawler probes (XHS/X/Bilibili) into an autonomous 48h sliding-window deduplication feed.
- 🎙️ **Sub-Second Audio-Visual Alignment**: Uses Edge-TTS neural speech synthesis and word-level alignment to sync visual scenes down to the millisecond.
- 🎨 **Code-as-Video (Remotion 4.x)**: 100% pure React/TypeScript programmatic rendering. Features live terminal animations, glowing emerald badges, and responsive stats cards.
- 📱 **Feishu HITL Simplified Approval**: Dispatches raw video previews and unified approval cards to mobile Feishu. Reviewers can approve or reject with a single button (`APPROVE_AND_DISPATCH`).
- 🛡️ **Dynamic Multi-Platform Distribution**:
  - **Bilibili**: Fully automated upload to creator drafts in `only_self_visible` mode with official AIGC disclosures & `no_reprint` protection.
  - **WeChat Channels**: Persistent session management (`storage_state.json`) with automated public distribution.
  - **Xiaohongshu**: 7-day safe freeze protocol (until 2026-11-06) with instant one-tap mobile post copy generation.

### 📁 Companion Project
- Check out [**study-schedule**](https://github.com/JX0829/study-schedule) — a daily task management & positive reward system for kids built with React, Vite, and multi-channel instant notifications!

---

<a name="chinese"></a>
## 🇨🇳 中文说明

**AutoContent Pipeline** 是一套专为科技内容创作者打造的端到端短视频全自动生产与多平台动态分流发布系统。通过代码将前沿资讯、大模型分镜生成、拟真语音合成、纯代码视频渲染与移动端审批发布彻底打通。

### 🌟 核心特性

- 🔍 **全网科技趋势雷达**：集成 GitHub Trending 高星榜、Reddit 热门产品、V2EX 极客热议、雪球硬核产业及 MediaCrawler/Agent Reach 探针，通过 48 小时滑动窗口实体去重引擎全自动聚合。
- 🎙️ **毫秒级音画同步**：基于 Edge-TTS 拟真神经语音合成与字级对齐算法，计算毫秒级时间戳驱动视觉画卷。
- 🎨 **代码即视频（Remotion 4.x）**：动态代码终端、极客数据图表、微光翡翠绿封面 100% 纯代码声明式渲染，告别传统模板限制。
- 📱 **飞书极简 HITL 审批流**：出片自动直发飞书原片视频与二合一交互卡片，支持移动端原位「✅ 批准发布并分发」单一主按钮一键触发多端协同。
- 🛡️ **动态多平台分发矩阵**：
  - **哔哩哔哩 (B站)**：全自动直传创作者中心，默认开启 `only_self_visible`（仅自己可见/草稿箱）、官方 AIGC 声明与未经允许禁止转载；
  - **微信视频号 (Channels)**：通过 Playwright 持久化官方创作者助手会话，支持公域分发与草稿留痕；
  - **小红书 (XHS)**：严格执行 7 天安全冷冻期（至 2026-11-06），全自动备货并即时推送手机专例文案，手机端长按一键复制发帖。

### 📁 关联姊妹项目
- 欢迎探索 [**study-schedule**](https://github.com/JX0829/study-schedule) — 基于 React + Vite + 多通道即时通知（飞书/企微/TG/PushMe）的儿童习惯养成与任务规划系统！

---

<a name="architecture"></a>
## 🏗️ Architecture / 架构与控制流

```text
[阶段 1: 全网热点雷达] ➔ [阶段 2: 语音与对齐] ➔ [阶段 3: 视频渲染]
       ↓                        ↓                       ↓
fetch_trends.py             aligner.py          render_hotspot.py
(GitHub/Reddit/V2EX/Probe)                              ↓
                                                [阶段 4: 飞书推送]
                                                        ↓
[阶段 6: 动态多平台分发] ⬅ [阶段 5: 网关审批流] ⬅   approval_service.py
       ↓                         ↓
run_publish_worker.py      Hermes 原生网关
 ├── 📺 B站: 私密草稿箱 (BV...)
 ├── 📲 视频号: 公域分发/草稿
 └── 📕 小红书: 手机素材直发
```

### Directory Structure / 核心目录

```text
autocontent-pipeline/
├── AUTOCONTENT-PIPELINE-SPEC.md  # 视觉排版与全流程规范文档
├── config/                      # 账号与平台策略配置
│   ├── accounts.yaml            # 多平台矩阵账号规则 (B站/视频号/抖音/小红书)
│   ├── platform_policies.yaml   # 差异化文本政策与 AIGC 合规配置
│   ├── probe_config.yaml        # 只读探针小号与代理隔离配置 (已加入 .gitignore)
│   └── sensitive_words.txt      # DFA 过滤敏感词库
├── data/                        # 状态机数据库、草稿箱与浏览器 Profile
│   ├── drafts/                  # B站与视频号草稿留痕 (JSON)
│   └── browser_profiles/        # 微信视频号助手等持久化会话
├── renderer/                    # Remotion 4.x React 视频渲染引擎
├── scripts/                     # 流程编排与后台服务脚本
│   ├── auto_generate_from_digest.py # 总控：热点生成与飞书卡片推送
│   ├── bilibili_live_uploader.py    # B站官方 API 直传脚本 (含 AIGC 声明)
│   ├── channels_login_flow.py       # 微信视频号扫码登录与会话持久化辅助
│   ├── channels_live_publisher.py   # 微信视频号助手自动化发布脚本
│   └── run_publish_worker.py        # 统一多平台动态分流引擎
└── src/                         # 核心模块
    ├── crawlers/                # 社交热点适配器 (MediaCrawler, Agent Reach, X CLI)
    ├── feishu/                  # 飞书开放平台卡片构建与消息推送
    ├── providers/               # 平台自动化适配器 (Bilibili, Channels, Douyin, XHS)
    ├── safety/                  # DFA 文本过滤与合规漏斗
    └── tts/                     # 语音合成与时间戳对齐器
```

---

## 🛠️ Quick Start / 快速上手

### 1. Requirements & Installation / 安装依赖

```bash
# Python dependencies
pip install -r requirements.txt

# Remotion dependencies
cd renderer && npm install && cd ..
```

### 2. Environment Configuration / 配置环境变量

复制 `.env.example` 为 `.env` 并填入必要凭据：
```bash
cp .env.example .env
```
核心配置项：
- `DEEPSEEK_API_KEY`: 用于热点提取与分镜台词生成
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`: 飞书自建应用凭据
- `FEISHU_APPROVAL_CHAT_ID`: 接收审批卡片与视频原片的 `open_id` 或 `chat_id`

### 3. Run Pipeline / 启动运行

```bash
# 1. 抓取最新全网科技热点与趋势
python3 scripts/fetch_trends.py

# 2. 从热点摘要全自动生成短视频并推送到飞书审批卡片
python3 scripts/auto_generate_from_digest.py --content "最新开源突破要闻..."

# 3. 在移动端飞书点击【✅ 批准发布并分发】，系统自动多端并行分流：
#    - B站: 直传私密草稿箱 (BV...)
#    - 微信视频号: 公开分发/草稿暂存
#    - 小红书: 手机专例文案自动推送到飞书私聊
```

---

## 📄 License / 开源协议

Released under the [MIT License](LICENSE).
