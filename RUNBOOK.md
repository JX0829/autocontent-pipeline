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

### 3.1 从速报直接转物料（量产链路 A）

速报快照 → 物料 JSON 不用手写：

```bash
# 看当前快照有哪些条目（编号与飞书速报一致）
python3 scripts/digest_to_material.py --list

# 第 3 条 → materials/digest-3-<uid>.json（标题自动重拟到 ≤20 字，非机械截断）
python3 scripts/digest_to_material.py --index 3

# 按 unique_id / 原文链接精确取
python3 scripts/digest_to_material.py --unique-id tw_2119746840887537800
python3 scripts/digest_to_material.py --url https://openai.com/news/xxx
```

产出物字段严格对齐 `manifest.schema.json`：`title`（≤20 字）+ `body` + `source_url` + `account`
（`account` 取自 `pipeline.yaml` 的 `inputs.account.default`，代码里没有字面量）。

拿到路径后直接进 §2 ② / ③。

---

## 4. 运行模式（最容易踩的两个坑）

### 4.1 发不发片

> **边界先说清（2026-11-10，v2 发布方案）**：
> 步骤链：`… → deliver（投递飞书）→ approval（人审）→ publish（平台发布）`
>
> - `deliver` **只发飞书**，不发平台
> - `publish` 是**默认关闭**的步骤，三重门禁任何一条不满足都不发布：
>   1. **配置门禁**：`publish.enabled=false`（默认）或 `platforms` 为空 → 整步 SKIP
>   2. **审批门禁**：`approval.json` 的 `decision` 必须 `approve`，否则 `EXIT=64` 拦住
>   3. **台账门禁**：`send_ledger` 的 `claim_send_slot` 抢到才发（同内容同账号不重发）
> - 另有 `dry_run: true`（默认）—— 只走预检、不真发，且**释放台账位**
>
> 所以默认跑完只到「飞书收到片子」。要真发，必须同时：改 `enabled: true` + 填 `platforms` +
> `dry_run: false` + 走到 approve。独立 CLI（§5.2 / §6）仍然可用，两者不冲突。

| 环境变量 | 行为 |
|---|---|
| 不设 / `publish` | 出片 → 审批 → **发飞书**（不发平台） |
| `DISPATCH_MODE=output_only` | **只出片**，不审批不发送，产物路径写进 `artifacts.txt` |
| `dispatch.enabled=false`（yaml） | 同上 |

### 4.2 审批渠道

| `APPROVAL_MODE` | 行为 |
|---|---|
| `auto`（默认） | 有飞书凭据 → 走飞书；没有 → 走 cli |
| `feishu` | 发飞书通知，**轮询会话等你回复** `approve` / `reject` |
| `cli` | 终端交互；非 tty 时必须显式表态 |

**关键**：`feishu` / `auto` 会**阻塞等待**（默认 600 秒），超时按 **reject** 处理。

**渠道解析规则（Provider 注册表）**：
- 显式 `APPROVAL_MODE=feishu` 但没凭据 → **直接报错，不偷偷降级**
- `auto`（默认）才按 `feishu → cli` 兜底，并打印为什么跳过
- 一个都不可用 → 报错，**绝不默认放行**
- 渠道异常统一转成步失败（`EXIT=64`），不会裸抛 traceback

- 无人值守（cron/批处理）→ 用 `DISPATCH_MODE=output_only`，
  或 `APPROVAL_MODE=cli` + `APPROVAL_NONINTERACTIVE=approve`
- 想限定谁能批 → `APPROVAL_ALLOWED_USERS=ou_xxx,ou_yyy`
- 改超时 → `APPROVAL_TIMEOUT_SEC=1800`（pipeline.yaml 的 profile.timeout_sec 优先）

### 4.3 新增一个审批 provider（照着做，流程代码不用动）

审批渠道是 **provider 槽位**，和 `llm.main` / `tts.tech` 同级。新增渠道（企业微信 / Slack / 钉钉…）只有 3 步：

**第 1 步：写 provider 类**（放 `src/approval/providers.py` 或独立文件）

```python
from .base import ApprovalProvider

class WecomApprovalProvider(ApprovalProvider):
    name = "wecom"

    def available(self) -> bool:
        return bool(os.getenv("WECOM_WEBHOOK"))

    def unavailable_reason(self) -> str:
        return "wecom: 缺 WECOM_WEBHOOK"

    def ask(self, run_id, artifacts, message) -> dict:
        # 1) 把审批送到人面前  2) 等裁决  3) 返回统一结构
        return {"decision": "approve", "decided_by": "wecom:user", "reason": "点了同意"}
```

**第 2 步：注册**（`src/approval/__init__.py`）

```python
from .providers import WecomApprovalProvider
register(WecomApprovalProvider(), override=True)
```

**第 3 步：在 `pipeline.yaml` 声明槽位**

```yaml
providers:
  approval.wecom:
    kind: approval
    profile:
      timeout_sec: 600
      card_style: approval_v2        # 渲染卡片的渠道；纯文本渠道写 renders_card: false
```

之后 `APPROVAL_MODE=wecom` 就能用。`exec_approval`、runner、别的渠道**一行都不用改**。

**卡片样式跟 provider 绑定**（不是散在通用 builder 里）：

| 位置 | 作用 |
|---|---|
| `src/approval/styles.py` 的 `STYLES` | 样式实现：`approval_v2`（4 元素，含发布矩阵）/ `approval_minimal`（2 元素，免刷屏） |
| `pipeline.yaml` 的 `profile.card_style` | 指定该渠道用哪个样式 |
| 新增样式 | 在 `STYLES` 里加个函数 → 改 yaml 字符串，不动流程 |

- 样式名写错 → 打印警告并回落 `approval_v2`，**不会发出空卡**
- GATE-1 强制校验：`kind: approval` 必须有 `timeout_sec`；`renders_card` 非 `false` 还必须有 `card_style`
- 改样式后必须能看到卡片结构变化（否则说明没真绑定）

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

**发片文件名必须用内容哈希**：`video_<md5>.mp4`。所有 run 的产物都叫 `video.mp4`，
直接发会被飞书按同名去重，把新片顶成之前发过的旧片（2026-11-10 踩过）。

```bash
for f in video.mp4 video_16_9.mp4; do cp "$f" "/tmp/video_$(md5 -q "$f").mp4"; done
```

### 5.1.1 三平台简介统一规范（2026-11-10 定稿）

简介是**给人读的**，不是分镜台词的搬运。三平台（B站 / 抖音 / 视频号）**同源生成**，
统一由 `src/utils/video_desc.py` 产出，禁止各发布脚本各写一套：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | `draft_script.json` 的 `video_desc` | 出片时 LLM 直接写好的 100~170 字人话简介 |
| 2 | `output/<date>/<run_id>/description.txt` | 老 run 现生成一次后缓存，避免重复烧 token |
| 3 | `humanize(fact_summary_150)` | LLM 不可用时的兜底：删斜杠命令、文件名、emoji，做词级去重 |

硬约束：**禁斜杠命令**（`/tdd`、`/grill-me`…）、禁文件名（`CONTEXT.md`）、禁 emoji、
禁话题标签混入正文、禁「本视频/点击/链接」话术、结尾不带引导语。

平台拼装差异（`build_desc(platform=...)`）：

| 平台 | 格式 |
|---|---|
| 视频号 | `正文`（provider 内部自己拼 `#标签`，调用方传 `include_tags=False`） |
| 抖音 | `标题 #标签\n正文`（标题必须在首行，发布器按首行识别） |
| B站 | `正文\n\n#标签`（标题单独走 title 字段） |

用法：

```bash
python3 scripts/publish_channels.py --run-id <run_id>            # 视频号
python3 scripts/publish_douyin_cdp.py --run-id <run_id>          # 抖音（CDP 9333）
python3 scripts/bilibili_live_uploader.py --run-id <run_id>      # B站（自动取横版+简介+标签）
```

### 5.1.2 作者首评 / 置顶（seed_comment，2026-11-10 补齐的漏字段）

发布成功后自动发作者首评、能置顶就置顶（首屏默认可见）。开关在
`config/accounts.yaml` **每个平台账号**下：

```yaml
seed_comment:
  enabled: true      # 发布成功后自动发首评
  pin: true          # 能置顶就置顶（首屏默认可见）；平台不支持则降级为普通首评
  text: ""           # 留空则用 video_desc 前 120 字 + 「欢迎在评论区交流讨论～」
```

统一解析器：`src/utils/seed_comment.py` 的 `resolve(account_cfg, draft, platform)`。

| 平台 | 发首评 | 置顶 | 说明 |
|---|---|---|---|
| B站 | ✅ 已自动化 | ✅ | `bilibili_live_uploader.py --run-id` 上传成功后自动发；置顶接口常返回 `-404 啥都木有`（稿件刚落库），需间隔 15s 重试至多 3 次 |
| 抖音 | ⏳ 配置就位 | ⏳ | 平台支持作者评论与置顶，但只能走 CDP 真机 UI；**待实拍验证后开启** |
| 视频号 | ❌ | ❌ | Web 端无作者首评接口，`resolve()` 会显式告警 `SEED_COMMENT_UNSUPPORTED`，需手机端手动 |

> 首评失败绝不影响发布结果，但必须打出 `SEED_COMMENT_FAILED` / `SEED_COMMENT_UNSUPPORTED`，禁止静默。

### 5.2 抖音发布：必须走 CDP 真机（**headless 必失败**）

抖音对 headless 自动化有指纹识别（`navigator.webdriver`、`--remote-debugging-pipe`），
扫码/短信验证会被判「二维码异常/已过期」并作废，`DouyinProvider` 只要页面跳回管理页就
`return success=True`——**那是假成功，不可信**。

唯一可行路径：`connect_over_cdp` 接管**已人工登录的真实 Chrome**。

```bash
# ① 启动真实 Chrome（headful，端口 9333；勿用 9222，该端口专供 Agent-Reach）
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9333 \
  --user-data-dir="$PWD/data/browser_profiles/douyin-cdp" \
  --no-first-run --no-default-browser-check --window-size=1280,900 about:blank

# ② 在该窗口内人工扫码/验证码登录创作者中心（一次即可，会话持久化在 user-data-dir）

# ③ 自检（只校验输入 + CDP 连通 + 回读作品数，不上传不发布）
python3 scripts/publish_douyin_cdp.py --run-id <run_id> --dry-run

# ④ 真实发布（按 accounts.yaml 的 publish_mode，默认「仅自己可见」私密作品）
python3 scripts/publish_douyin_cdp.py --run-id <run_id>
```

| 退出码 | 含义 |
|---|---|
| `0` | 已入库，且作品列表回读命中标题 |
| `2` | 入参/环境错误（找不到产物、缺 title、账号配置缺失） |
| `6` | 点发布后回读未命中（风控静默拦截），截图存 `output/douyin_diag/` |
| `7` | 需要人工二次验证（短信/扫码），截图存 `output/douyin_diag/` |

**成功判据（只认这两个）**：作品数 `作品 (N)` → `作品 (N+1)`，且列表正文命中标题。
脚本已内置幂等：标题已在列表中则直接跳过，不会重复发。

---

### 5.3 磁盘卫生：output/ 只留最近 3 天

`output/` 每次全链产出 video/cover/props/音频，单天 40–90MB，不清理会一直涨（清理前 931MB）。

```bash
# 保留最近 3 天（含今天），其余移入废纸篓（可恢复，不是 rm）
ls -d output/2026*/ | awk -F/ '$2 < "'$(date -v-2d +%Y%m%d)'"' | xargs trash
```

2026-11-10 执行结果：931MB → 170MB，只剩 `20261108 / 20261109 / 20261110`。
无日期的历史演示目录（demo_* / template_* / catwu_spotlight 等）一并清掉。

## 6. 三平台发布（现状必须分清）

**发布入口现在收口在 pipeline 内**：`deliver → approval → publish`（`publish.enabled` 默认 false）。
下面的 CLI 是**薄壳**——发布与文案都走 `src/pipeline/executors._invoke_provider`，只保留
`--probe` / `--dry-run` / `--stop-before-publish` / 台账运维这些手工动作（2026-11-11 收敛，见 §7.5）。
runner 跑完只代表「片子出来了 + 飞书收到了」，要公开发布仍需审批 + 打开门禁。

| 平台 | 实现 | 公开发布 | AI 合成声明 | 原创 |
|---|---|---|---|---|
| 抖音 | `src/providers/douyin.py`（Playwright CDP） | ✅ `public_publish` | ✅ 自动勾选 | — |
| B站 | `src/providers/bilibili.py` + `scripts/bilibili_live_uploader.py` | ✅ `public` | ✅ `creation_statement_id=1` | ✅ `original=True` |
| 视频号 | `src/providers/channels_cdp.py`（Playwright） | ✅ 发表后公开 | ❌ **Web 端无此控件** | ✅ 勾选（fail-closed） |

### 6.1 微信视频号（真机实测 2026-11-10，照着做，别自己摸索）

登录态会过期，过期后所有发布都会返回 `LOGIN_EXPIRED`（不会假装成功）。

```bash
# ① 检查登录态
python3 scripts/channels_login.py --check

# ② 过期了就重新扫码（打开浏览器；--qr-out 会自动把二维码截成图，方便发给操作人）
python3 scripts/channels_login.py --qr-out /Users/a1/Downloads/qr/qr.png

# ③ 探针：确认登录有效、发布 iframe 可达（不上传不发布）
python3 scripts/publish_channels.py --probe

# ④ 只验证到发表前（上传+填文案+勾原创，不产生公开内容）
python3 scripts/publish_channels.py --run-id <run_id> --stop-before-publish

# ⑤ 真实发布
python3 scripts/publish_channels.py --run-id <run_id>
```

**三个必须知道的硬约束（踩过，别重复踩）**：

1. **不能直连发布页**：访问 `/platform/post/create` 会被重定向回 `/platform` 首页，
   必须先点首页的「发表视频」按钮。
2. **表单在 wujie 微前端 iframe 里，且 Playwright 选择器全部失效**：
   iframe 是 `micro/content/post/create`，它的 `document` 被 wujie 代理过 ——
   `frame.evaluate("document.querySelectorAll('*')")` 能看到 2587 个元素，
   但 `frame.query_selector()` / `frame.locator()` **一律返回 0**。
   → 所有交互必须走 `frame.evaluate(...)`，不要浪费时间调选择器。
3. **上传不能用 `set_input_files`**：页面里确实有 `input[type=file]`（display:none），
   但 `set_input_files` 设不进去。正确做法：
   ```python
   async with page.expect_file_chooser() as fc:
       await ui.evaluate("document.querySelector(\"input[type='file']\").click()")
   chooser = await fc.value
   await chooser.set_files(video_path)
   ```

**AI 声明**：视频号 Web 端**确认没有**「含 AI 合成内容」控件
（`内容由AI生成 / AI生成 / 人工智能生成 / AIGC / AI创作` 全扫零命中），
只能手机端补勾。返回 `aigc_state="NOT_SUPPORTED_ON_WEB"`，不要以为漏做了。

**行为约定**：

- 要求勾原创但找不到原创复选框 → **中止发布**（`ORIGINAL_CHECKBOX_NOT_FOUND`）
- 点了「发表」但没拿到平台侧证据（成功文案 / URL 跳转）→ `PUBLISHED_UNVERIFIED`，不报成功
- `--stop-before-publish` 会在后台留一条已上传未发表的记录，**需要人工清理**
- 截图落在 `data/browser_profiles/channels-tech/`（`before_publish.png` / `after_publish.png`）

**注意**：本机 Playwright 未下载自带 Chromium，统一用系统 Chrome（`channel="chrome"`）。

---

## 7. 幂等与重跑（第二容易踩的坑）

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

### 7.1 平台发布台账（publish_ledger）—— 2026-11-11 事故后新增

**事故**：同一条视频在 B站被发了两遍（07:49 / 07:56，两条一模一样的稿件）。
根因是三个发布 CLI 直接调 submit，没有任何"这条发过没"的记录；第一次返回异常时只能重跑，于是多了一条。

台账口径：`publish_key = sha256(platform | account | video_sha256)[:32]`
（按**视频内容**算，跟文件名、标题、run_id 无关 —— 改个标题重发同样会被拦）

| status | 含义 | 再次发布时 |
|---|---|---|
| `claimed` | 已占位，正在发（或进程中途死了） | ⛔ 拦住 |
| `published` | 平台已确认，`post_id` 是 bvid/aid | ⛔ 拦住，并打印 post_id |
| `unverified` | **发了但拿不到证据**（超时、PUBLISHED_UNVERIFIED） | ⛔ 拦住，要求人工核对 |
| `failed` | 确认没发出去（未登录、找不到原创勾选、参数错） | ✅ 允许重试 |
| `deleted` | 平台上人工删掉了 | ✅ 允许重发 |

原则：**只有拿到"确定没发出去"的证据才允许重试**，其余一律拦住让人核对。
（把 `unverified` 并回 `failed` 会重新打开重复发布的口子，`scripts/test_publish_ledger.py` 有断言盯着。）

运维命令：

```bash
# 看台账
python3 -m src.publish_ledger --list
python3 -m src.publish_ledger --list bilibili

# 去平台核对后：确认没发出去 → 释放占位（claimed / unverified 都能清）
python3 -m src.publish_ledger --release <publish_key>

# 平台上确实删掉了这条 → 标记 deleted，允许以后重发
python3 -m src.publish_ledger --deleted <publish_key>
```

跑测试（已挂 `verify_all.sh --fast`）：

```bash
python3 scripts/test_publish_ledger.py   # 19 断言
```

### 7.2 发布凭证回查（post_id 一直是空）

上传接口的返回值里经常没有 bvid，台账 `post_id` 因此一直空 —— 拦得住，但报错说不出是哪条。
现在 B站发布成功后会调只读的稿件列表接口**按标题回查 bvid**（`src/publish_evidence.py`），重试 3 次 × 5s
（稿件刚落库有几秒延迟），查不到返回空串，**不影响发布结论**。

```bash
python3 scripts/test_publish_evidence.py   # 7 断言（离线桩，不联网）
```

### 7.3 僵尸 RUNNING 收尸

进程被外部杀掉时（`SIGKILL`/终端关闭），状态库会永远停在 `RUNNING` —— 2026-11-10 的
`astryx-1` / `freecc-1` 就是这样卡在 `expand_script` 的。

判据：`status=RUNNING` 且 `updated_at` **超过 15 分钟**没更新（最长单步渲染约 2.5 分钟，不会误杀）。
收尸只改状态为 `ABANDONED`，**不删产物、不动 run_root**，下次同内容重跑仍按「重试」复用原 run_id 与原目录。

```bash
python3 scripts/reap_stale_runs.py              # 只列出（dry-run）
python3 scripts/reap_stale_runs.py --apply      # 真标记为 ABANDONED
python3 scripts/reap_stale_runs.py --minutes 30 # 换阈值
```

runner 每次真实运行开始时也会自动收尸一次（阈值取 `STALE_RUN_MINUTES`，默认 15）。

> 补充：`expand_script` 的重试**不在这里做** —— `pipeline.yaml` 的 `expand_script.retry.max`
> 由 `runner._execute_step` 统一读取，`llm/client.py` 的 `_is_transient()` 区分超时/连接/429/5xx
> （小品 PR #1，2026-11-10）。收尸解决的是它覆盖不到的情况：**进程被 SIGKILL/超时强杀**，
> 任何代码路径都没机会写库。

---

### 7.4 审批卡片「点按钮即裁决」（2026-11-11 打通）

审批卡按钮是 `behaviors:[{type:"callback"}]`，点下去飞书推 `card.action.trigger`。
这条链路走过三个坑，最后落在一个**不依赖 site-packages** 的方案上：

| 坑 | 现象 | 结论 |
|---|---|---|
| ① pipeline 内进程内长连接 | 连得上，420 秒收不到任何回调 | 审批 app 与小何网关**同一个 app_id**，飞书对同 app 多客户端**随机投递**，事件被网关抢走 |
| ② 只在一个 profile 装处理程序 | 有时成功、有时没反应 | 共用该 app 的 4 个 profile（xiao-he / xiao-kai / xiao-pin / xiao-xie）都可能收到，必须都装 |
| ③ 改 site-packages 适配器把它变成 COMMAND | 能用，但 hermes 升级会被覆盖 | 改用官方扩展点 `pre_gateway_dispatch`（见下），补丁已撤 |

**最终机制（三处，全在 profile 侧，升级不丢）：**

```
飞书点击 → 适配器转成文本 {"decision":"approve","run_id":"xxx"}
        → 插件 pipeline-approval-bridge 的 pre_gateway_dispatch 钩子认出来
        → 共享模块校验（run_id 正则 / decision 白名单 / 产物目录必须存在 / 拒绝改判）
        → 原子写 data/approvals/<run_id>.json
        → 返回 {"action":"skip"} —— 消息丢弃，不进 LLM（0 token）
```

- 共享实现：`~/Downloads/workspace/team/tools/pipeline_card_bridge.py`
- 各 profile 薄壳：`~/.hermes/profiles/<p>/plugins/pipeline-approval-bridge/`（10 行，`register_bridge(ctx)`）
- 覆盖 profile：`xiao-he` `xiao-kai` `xiao-pin` `xiao-xie`（**同一飞书 app 的所有消费者**，缺一个就有 1/4 概率失效）
- 生效条件：改完必须 `hermes --profile <p> gateway restart`（网关不能从自身进程内重启）

**排障口径：**

```bash
# 点击有没有落到某个网关
grep -h "Routing card action" ~/.hermes/profiles/*/logs/gateway.log | tail -3
# 插件有没有处理、消息有没有被吞掉
grep -h "pipeline-card-bridge\|pre_gateway_dispatch skip" ~/.hermes/profiles/*/logs/gateway.log | tail -3
# 裁决文件是否存在
ls -la data/approvals/
```

实测证据（2026-11-11 13:53）：

```
[xiao-pin] [pipeline-card-bridge] dispatch: ✅ 已记录 cardtest-4 → approve
[xiao-pin] pre_gateway_dispatch skip: reason=✅ 已记录 cardtest-4 → approve platform=feishu
裁决文件: {"decision": "approve", "by": "card:hermes", "at": 1794376399}
```

> 兜底：文字回复 `approve` / `reject` 仍然有效（`ask()` 同时扫会话回复与裁决文件），
> 按钮挂了不至于卡死审批。

---

### 7.5 发布回执卡（≤100 字，2026-11-11）

发布结果**只发这一张卡**：`src/feishu/card_builder.build_publish_receipt()`，
摘要由 `publish_receipt_text(report, title, limit=100)` 生成，`pipeline.yaml` 的
`publish.receipt: true` 控制开关（默认开）。

```
《Edge0开源145B…》✅ 已发布 3/3：抖音·B站·视频号
《Claude双版本炸场》⚠️ 2/3 成功｜B站失败：登录过期
⚠️ 0/3 成功｜抖音、B站 等3个失败：未确认发布
《Qwen4开源》⏭ 未发布（1 项被门禁拦下）
```

- 错误码做中文化映射（`LOGIN_EXPIRED`→登录过期、`PUBLISH_SIGNAL_MISSING`→未确认发布…），
  **不会把英文截半**；未知码取中文部分前 8 字
- 全部被门禁拦下时不发卡（避免噪音）；发送失败只打印，**不改变发布结论**
- 回执卡唯一实现在 `card_builder`，pipeline（`exec_publish`）和
  `scripts/publish_channels.py` 都调 `executors.send_publish_receipt()`

**为什么之前回执很长**：仓库里根本没有回执模板，每次由 agent 现写（网关日志实测
最长 8701 字）。现在结果落成确定性卡片，agent 不需要再复述。

**发布入口收敛**：`scripts/publish_channels.py` 从 197 行降到 ~150 行，只保留
`--probe` / `--dry-run` / `--stop-before-publish` / 台账查重，发布与文案都走
`src/pipeline/executors._invoke_provider`。同时修掉两处历史分叉：

| 历史分叉 | 现状 |
|---|---|
| `compose_caption_body()` 写死收尾语「它不接管整个项目，只把流程拆成可复用的几步」，且 `load_run()` 算完即弃 | 已删除，文案统一走 `src/utils/video_desc` |
| `_invoke_provider` 硬编码 `declare_original=True, declare_aigc=True`，与 `accounts.yaml` 的 `channels-tech-main: declare_aigc: false` 矛盾 | 改为从账号配置读；CLI 可用 `--no-original` / `--no-aigc` 覆盖 |

> 渠道自己的配置文件（`config/accounts.yaml`、`config/platform_policies.yaml`）**不动**，
> 收敛只发生在代码侧。

---

## 8. 验收 / 自检

```bash
bash scripts/verify_all.sh --fast   # 0.7s  静态扫描 5 项（可挂 pre-commit）
bash scripts/verify_all.sh          # ~2min 再加 GATE-1 声明层契约 + GATE-2 台账幂等
bash scripts/verify_all.sh --full   # 真实渲染，实测约 6–7 分钟（含 GATE-3 端到端）
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

## 9. 故障排查

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

## 10. 禁止事项

1. **不要 `git add -A`** —— 仓库多 Agent 并行，会把别人未完成的工作裹进提交。逐文件 `git add`。
2. **不要直接调 `PipelineDB.init_db()` 改生产库** —— 用 `scripts/migrate_db.py`（默认 dry-run，`--apply` 才写）。
3. **不要把审批时长改成秒** —— 飞书 media 消息会变成 `nonsupport` 坏消息。
4. **不要在 `renderer/public/tasks/` 放新产物** —— 已废弃，现在走 `runs/<run_id>`。
5. **不要写死账号 ID / 音色 / 模型名** —— 一律走 `config/accounts.yaml` 与 `pipeline.yaml` 的 `providers` 槽位，`verify_all` 会扫。

---

## 11. 文件地图

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

## 12. 最小排障顺序

出问题时按这个顺序看，90% 能定位：

```
1. bash scripts/verify_all.sh --fast     # 环境/静态检查
2. runner --dry-run                       # 声明层与步骤计划
3. 看 output/<日期>/<run_id>/ 里缺哪个产物
4. 看 data/pipeline_state.db 的 pipeline_runs.steps_json
5. 单跑对应 gate 脚本复现
```
