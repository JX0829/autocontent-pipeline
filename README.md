# 🚀 AutoContent Pipeline (短视频全自动生产与发布流水线)

端到端的硬核科技短视频自动化生成、渲染与合规发布系统。

从全网前沿资讯抓取，到大模型分镜提炼、神经语音合成、Remotion 纯代码渲染，再到飞书移动端原片视频与交互卡片审批（HITL），最后由自动化引擎安全分发至多平台。

---

## 🌟 核心特性

- 🔍 **AI 热点深度提炼**：大模型智能评估开源与前沿科技爆点，生成分镜台词、视觉组件与评论区引流暗号。
- 🎙️ **毫秒级音画同步**：基于 Edge-TTS 拟真语音合成与字级对齐算法，计算毫秒级时间戳驱动视觉画卷。
- 🎨 **代码即视频（Remotion 4.x）**：动态代码终端、极客数据图表、微光翡翠绿封面 100% 纯代码声明式渲染，告别传统模板限制。
- 📱 **飞书 HITL 人工审批流**：出片自动直发飞书原片视频与富媒体交互卡片，支持原位毫秒级「一键批准 / 驳回」。
- 🛡️ **安全多平台分发**：Playwright 自动化托管发布，支持私密作品安全模式与 AIGC 合规自动声明。

---

## 📁 目录架构

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

## 🛠️ 快速上手

### 1. 安装依赖

```bash
# Python 依赖
pip install -r requirements.txt

# Remotion 依赖
cd renderer && npm install && cd ..
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入必要信息：
```bash
cp .env.example .env
```
配置项包括：
- `DEEPSEEK_API_KEY`：热点脚本大模型提炼密钥
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`：飞书应用凭证
- `FEISHU_APPROVAL_CHAT_ID`：用于接收待审视频与卡片的 open_id 或 chat_id

### 3. 运行流水线

```bash
# 从科技热点简报触发视频生成与飞书推送
python3 scripts/auto_generate_from_digest.py --content "你的科技热点新闻简报文本"

# 启动飞书长连接监听服务（响应卡片审批）
python3 scripts/run_feishu_worker.py
```

---

## 📄 开源许可

[MIT License](LICENSE)
