# RUNBOOK · 短视频流水线使用手册

> 给**要真正用这条流水线**的 Agent / 人。看完这一页就能跑起来，不用问作者。
> 最后更新：2026-11-09 · 对应 `pipeline.yaml` v1.4

---

## 0. 一句话说明

给一份物料（标题 / 正文 / 来源 URL），流水线自动完成：
**选题扩写 → TTS 配音 → 竖版视频 + 横版视频 → 人工审批 → 发飞书**。

支持两种产出形态：
- **竖版** `video.mp4` 1080×1920（抖音/视频号）
- **横版** `video_16_9.mp4` 1920×1080（B站）

> 小红书已于 2026-11-09 整体下线（账号封禁），相关抓取/发布/监控代码已移除。

---

## 1. 环境（踩错就报错，先看这条）

| 项 | 要求 |
|---|---|
| 解释器 | **`/opt/homebrew/opt/python@3.14/bin/python3.14`** |
| 不要用 | 系统 `python3` / conda 的 `python3`（缺项目依赖） |
| 依赖 | `bash scripts/bootstrap.py --ensure` 自动补齐 |
| 渲染 | Node + `npx remotion`（renderer/ 目录已装好，版本 4.0.200） |
| 字体 | 系统 CJK 字体（PingFang / Noto Sans CJK） |

**为什么强调解释器**：GATE-1 的 bootstrap 检查会因为用错解释器报
`15/20 PASS, MISSING python/edge-tts...`，看起来像代码坏了，其实只是 shell 里
`python3` 指向了 conda。所有脚本都支持 `PYTHON=/path/to/python` 覆盖。

---

## 2. 三分钟上手

```bash
cd ~/Downloads/workspace/team/project/autocontent-pipeline

# ① 先自检环境（0.7 秒，不渲染不联网）
bash scripts/verify_all.sh --fast

# ② 只出片、不发片（第一次跑强烈建议用这个）
DISPATCH_MODE=output_only \
  /opt/homebrew/opt/python@3.14/bin/python3.14 \
  -m src.pipeline.runner --material /path/to/material.json --run-id my-test-1

# ③ 正式跑（出片 + 审批 + 发飞书）
/opt/homebrew/opt/python@3.14/bin/python3.14 \
  -m src.pipeline.runner --material /path/to/material.json
```

**先干跑看计划**（不调模型、不写库）：

```bash
... -m src.pipeline.runner --material material.json --dry-run
```

---

## 3. 物料格式（必读）

最小可用：

```json
{ "title": "某开源项目开源了" }
```

规则来自 `manifest.schema.json`：

| 字段 | 要求 |
|---|---|
| `title` | **必填**，≤20 字（超了 hook 会拦） |
| `body` 或 `source_url` | **二选一必填**（有正文走正文，没正文按 URL 抓） |
| `account` | 可选，默认 `douyin-tech-main` |
| `images` | 可选，参考图 |

```json
{
  "title": "DeepSeek 开源了推理框架",
  "body": "正文素材……",
  "source_url": "https://example.com/a",
  "account": "douyin-tech-main"
}
```

⚠️ **`source_url` 缺失且没有 `body` 会直接 `ValueError`** —— 代码里没有占位 URL 兜底（T4.2 刻意设计）。

---

## 4. 运行模式（最容易踩的两个坑）

### 4.1 发不发片

| 环境变量 | 行为 |
|---|---|
| 不设 / `publish` | 出片 → 审批 → 发飞书 |
| `DISPATCH_MODE=output_only` | **只出片**，不审批不发送，产物路径写进 `artifacts.txt` |
| `dispatch.enabled=false`（yaml） | 同上 |

### 4.2 审批渠道

| `APPROVAL_MODE` | 行为 |
|---|---|
| `auto`（默认） | 有飞书凭据 → 走飞书；没有 → 走 cli |
| `feishu` | 发飞书通知，**轮询会话等你回复** `approve` / `reject` |
| `cli` | 终端交互；非 tty 时必须显式表态 |

**关键**：`feishu` / `auto` 会**阻塞等待**（默认 600 秒），超时按 **reject** 处理。

- 无人值守（cron/批处理）→ 用 `DISPATCH_MODE=output_only`，
  或 `APPROVAL_MODE=cli` + `APPROVAL_NONINTERACTIVE=approve`
- 想限定谁能批 → `APPROVAL_ALLOWED_USERS=ou_xxx,ou_yyy`
- 改超时 → `APPROVAL_TIMEOUT_SEC=1800`

---

## 5. 产物在哪

```
output/<YYYYMMDD>/<run_id>/
├── video.mp4            竖版 1080×1920
├── video_16_9.mp4       横版 1920×1080
├── cover.png            竖版封面
├── cover_16_9.png       横版封面
├── draft_script.json    分镜脚本
├── manifest.json        归一化后的物料
├── material_check.json  物料校验结果
├── approval.json        审批裁决（谁批的/证据 msg_id）
├── audit.jsonl          逐 hook 审计日志
├── dispatch.json        发送结果
├── props/               aligned_render.json + scene_N.mp3（Remotion 输入）
└── artifacts.txt        仅 output_only 模式：产物绝对路径清单
```

`renderer/public/runs/<run_id>` 是指向 `props/` 的软链（Remotion 只能读 `public/`）。

### 5.1 output_only 之后怎么交付（**必读，踩过坑**）

`output_only` 只出片不发送，**交付是调用方的事**。产物必须**竖版横版各 2 件全部发出**：

| 组 | 视频 | 封面 |
|---|---|---|
| 竖版 1080×1920 | `video.mp4` | `cover.png` |
| 横版 1920×1080 | `video_16_9.mp4` | `cover_16_9.png` |

**别手工挑文件**——实际发生过「只发了竖版封面 + 横版视频，漏了另外两件」。
用脚本，缺一件就拒绝发送：

```bash
# 先看计划（不发送）
python3 scripts/send_run_artifacts.py --run-id <run_id> --dry-run

# 真实发送（2 条视频 + 2 张封面大图，回执写 delivery.json）
python3 scripts/send_run_artifacts.py --run-id <run_id>

# 只要视频
python3 scripts/send_run_artifacts.py --run-id <run_id> --videos-only
```

`artifacts.txt` 也已按 `[vertical]` / `[landscape]` 分组标注，可直接照着发。

---

## 6. 幂等与重跑（第二容易踩的坑）

| 场景 | 结果 |
|---|---|
| 同物料、同账号再跑 | 命中 `uq_run_content` → **直接跳过**，不重新出片 |
| 抹掉 dispatch 完成标记再跑 | 命中 `send_ledger` → **不重发**，退出码 64 |
| 改了物料内容 | `content_hash` 变 → 视为新内容，正常跑 |
| 想强制重发 | 删 `send_ledger` 里对应 `dedupe_key` 行，或换 `--run-id` |

**这是特性不是 bug**：防止重复推送到粉丝群。

```bash
# 看某次发送记录
sqlite3 data/pipeline_state.db "select dedupe_key,task_id,feishu_msg_id,sent_at from send_ledger order by rowid desc limit 5;"
```

断点续跑：

```bash
... -m src.pipeline.runner --material m.json --run-id my-test-1 --resume
```

---

## 7. 验收 / 自检

```bash
bash scripts/verify_all.sh --fast   # 0.7s  静态扫描 5 项（可挂 pre-commit）
bash scripts/verify_all.sh          # ~2min 再加 GATE-1 声明层契约 + GATE-2 台账幂等
bash scripts/verify_all.sh --full   # ~12min 再加 GATE-3 端到端真实渲染
```

单项：

```bash
bash scripts/gate1.sh    # 声明层/模型契约（8 项）
bash scripts/gate2.sh    # 台账幂等（4 项）
bash scripts/gate3.sh    # 端到端不重发（12 项断言）
python3 scripts/test_task_contracts.py   # 任务契约回归 33 项
```

**改完代码必须跑 `--fast`，动到渲染/发送必须跑 `--full`。**

---

## 8. 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'src'` | 不在项目根目录跑 | `cd` 到项目根 |
| bootstrap `15/20 PASS, MISSING python/edge-tts` | 用了 conda 的 python3 | 显式用 homebrew 3.14，或 `PYTHON=...` |
| `MissingConfig: 缺失必填环境变量 TTS_RATE...` | `.env` 缺键 | 补 `.env`，或检查 `AUTOCONTENT_ENV_FILE` |
| `产物无音频流（无声片）` | `renderer/public/runs/<id>` 软链缺失 | 检查软链；这是硬断言，不会静默出无声片 |
| 跑完卡住十分钟不动 | 审批在等 `approve` | 回复 `approve`/`reject`，或改用 output_only |
| 退出码 64 | 命中发送台账（已发过） | 正常幂等，不是错误 |
| 退出码 78 | 缺必填配置 | 看报错里的环境变量名 |
| 封面三卡显示 `—` | 分镜缺 `highlight_text` | 物料正文太短，补内容 |
| 视频卡片时长显示 00:00 | 飞书服务端不回填 duration | **平台限制，修不了**，不要改成传秒（会变坏消息） |

---

## 9. 禁止事项

1. **不要 `git add -A`** —— 仓库多 Agent 并行，会把别人未完成的工作裹进提交。逐文件 `git add`。
2. **不要直接调 `PipelineDB.init_db()` 改生产库** —— 用 `scripts/migrate_db.py`（默认 dry-run，`--apply` 才写）。
3. **不要把审批时长改成秒** —— 飞书 media 消息会变成 `nonsupport` 坏消息。
4. **不要在 `renderer/public/tasks/` 放新产物** —— 已废弃，现在走 `runs/<run_id>`。
5. **不要写死账号 ID / 音色 / 模型名** —— 一律走 `config/accounts.yaml` 与 `pipeline.yaml` 的 `providers` 槽位，`verify_all` 会扫。

---

## 10. 文件地图

| 路径 | 作用 |
|---|---|
| `pipeline.yaml` | **唯一声明源**：步骤、hooks、providers、inputs |
| `spec_enhancement.md` | 增强计划与任务台账（改代码前先改这里） |
| `manifest.schema.json` | 物料契约 |
| `src/pipeline/runner.py` | 主流程、断点续跑、dispatch_policy |
| `src/pipeline/executors.py` | 各步骤执行体（渲染/发送/审批） |
| `src/pipeline/contracts.py` | 声明校验、`load_pipeline()` |
| `src/approval/` | 审批渠道（feishu / cli / auto） |
| `src/feishu/approval_service.py` | 飞书上传与发送 |
| `src/utils/accounts.py` | 账号解析（无默认值兜底） |
| `scripts/verify_all.sh` | 统一验收入口 |
| `data/pipeline_state.db` | 状态库（runs / artifacts / send_ledger / degraded） |

---

## 11. 最小排障顺序

出问题时按这个顺序看，90% 能定位：

```
1. bash scripts/verify_all.sh --fast     # 环境/静态检查
2. runner --dry-run                       # 声明层与步骤计划
3. 看 output/<日期>/<run_id>/ 里缺哪个产物
4. 看 data/pipeline_state.db 的 pipeline_runs.steps_json
5. 单跑对应 gate 脚本复现
```
