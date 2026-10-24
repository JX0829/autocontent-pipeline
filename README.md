<div align="center">

# 🚀 AutoContent Pipeline

**End-to-End Automated Tech Short Video Production & Distribution System**  
**端到端的硬核科技短视频自动化生成、渲染与合规发布流水线**

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

**AutoContent Pipeline** is an intelligent, developer-centric short video automation engine designed for tech content creators. It bridges the gap between breaking tech news and fully-rendered high-definition videos with human-in-the-loop (HITL) approval.

### 🌟 Key Highlights

- 🔍 **AI Hotspot Extraction**: Evaluates tech news/digests with LLMs to automatically produce storyboard scripts, highlight stats, and resource keywords.
- 🎙️ **Sub-Second Audio-Visual Alignment**: Uses Edge-TTS neural speech synthesis and word-level alignment to sync visual scenes down to the millisecond.
- 🎨 **Code-as-Video (Remotion 4.x)**: 100% pure React/TypeScript programmatic rendering. Features live terminal animations, glowing emerald badges, and responsive stats cards.
- 📱 **Feishu HITL Approval Workflow**: Dispatches raw video previews and interactive card notifications to mobile Feishu. Reviewers can approve or reject with one click.
- 🛡️ **Safe Multi-Platform Dispatch**: Playwright-powered automated publisher supporting private draft mode (Safety Mode A) and mandatory AIGC disclosures.

### 📁 Companion Project
- Check out [**study-schedule**](https://github.com/JX0829/study-schedule) — a daily task management & positive reward system for kids built with React, Vite, and multi-channel instant notifications!

---

<a name="chinese"></a>
## 🇨🇳 中文说明

**AutoContent Pipeline** 是一套专为科技内容创作者打造的端到端短视频全自动生产与发布系统。通过代码将前沿资讯、大模型分镜生成、拟真语音合成、纯代码视频渲染与移动端审批发布彻底打通。

### 🌟 核心特性

- 🔍 **AI 热点深度提炼**：大模型智能评估开源与前沿科技资讯，自动生成分镜台词、极客代码片段与评论区引流暗号。
- 🎙️ **毫秒级音画同步**：基于 Edge-TTS 拟真神经语音合成与字级对齐算法，计算毫秒级时间戳驱动视觉画卷。
- 🎨 **代码即视频（Remotion 4.x）**：动态代码终端、极客数据图表、微光翡翠绿封面 100% 纯代码声明式渲染，告别传统模板限制。
- 📱 **飞书 HITL 人工审批流**：出片自动直发飞书原片视频与富媒体交互卡片，支持移动端原位毫秒级「一键批准 / 驳回」。
- 🛡️ **安全多平台分发**：Playwright 自动化托管发布，严格遵循方案 A（私密作品安全模式）并自动勾选 AIGC 合规声明。

### 📁 关联姊妹项目
- 欢迎探索 [**study-schedule**](https://github.com/JX0829/study-schedule) — 基于 React + Vite + 多通道即时通知（飞书/企微/TG/PushMe）的儿童习惯养成与任务规划系统！

---

<a name="architecture"></a>
## 🏗️ Architecture / 架构与控制流

```text
[阶段 1: 热点提炼] ➔ [阶段 2: 语音与对齐] ➔ [阶段 3: 视频渲染]
       ↓                       ↓                       ↓
hotspot_extractor.py        aligner.py          render_hotspot.py
                                                       ↓
[阶段 6: 平台发布] ⬅ [阶段 5: 卡片监听] ⬅ [阶段 4: 飞书推送]
       ↓                       ↓                       ↓
run_publish_worker.py   run_feishu_worker.py   approval_service.py
```

### Directory Structure / 核心目录

```text
autocontent-pipeline/
├── AUTOCONTENT-PIPELINE-SPEC.md  # 视觉排版与全流程规范规范文档
├── config/                      # 账号与敏感词过滤配置
│   ├── accounts.yaml            # 多平台矩阵账号规则
│   └── sensitive_words.txt      # DFA 过滤敏感词库
├── data/                        # 状态机数据库与本地草稿
├── renderer/                    # Remotion 4.x React 视频渲染引擎
├── scripts/                     # 流程编排与后台服务脚本
│   ├── auto_generate_from_digest.py # 总控：热点生成与飞书卡片推送
│   ├── run_feishu_worker.py         # 飞书审批长连接事件监听 Worker
│   └── run_publish_worker.py        # 平台安全分发 Worker
└── src/                         # 核心模块
    ├── feishu/                  # 飞书开放平台卡片构建与消息推送
    ├── providers/               # 平台自动化适配器 (Douyin 等)
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

Copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```
Key configuration items:
- `DEEPSEEK_API_KEY`: API key for hotspot extraction & script generation
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`: Feishu custom app credentials
- `FEISHU_APPROVAL_CHAT_ID`: `open_id` or `chat_id` receiving approval cards

### 3. Run Pipeline / 启动运行

```bash
# Generate video from news text & dispatch Feishu review card
python3 scripts/auto_generate_from_digest.py --content "Your breaking news digest..."

# Start background Feishu review worker (listen to card approval events)
python3 scripts/run_feishu_worker.py
```

---

## 📄 License / 开源协议

Released under the [MIT License](LICENSE).
