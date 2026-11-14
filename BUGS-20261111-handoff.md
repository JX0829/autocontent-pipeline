# autocontent-pipeline 问题全量清单（2026-11-11）

移交：小品（产品）→ 小何（技术负责人）
覆盖范围：2026-11-10 晚 ~ 2026-11-11 傍晚，连续出片 6 条视频、发布 4 批。
生产上均已绕过，**未改代码的部分按职责移交**。

---

## 一、出片失败（4 次，全部 BLOCKED/EXIT≠0）

### 1.1 P1｜`fact_summary_150` 超 schema 上限直接崩（撞 3 次）

- **位置**：`src/schemas.py:21` `fact_summary_150: str = Field(..., max_length=200)`
- **现象**：prompt 要求「不超过 150 字」，schema 上限却是 200。模型产出超 200 字符时 pydantic 抛
  `ValidationError: string_too_long`，`exec_expand_script` 直接抛错，runner EXIT=1，整条片子白渲染。
- **实测复现**（2026-11-11）：
  ```python
  DraftScriptPayload(..., fact_summary_150='x'*250)
  # → ('fact_summary_150',) string_too_long: String should have at most 200 characters
  ```
- **真实失败 run**：`astryx-1`（body 250 字，日志 `/tmp/render_astryx.log`）、
  `freecc-1`（body 191 字，日志 `/tmp/render_fcc.log`）
- **现状**：✅ **已修复**（2026-11-11 12:23 复核确认）——
  `803b583` 新增 `@field_validator("fact_summary_150", mode="before")` 按标点截断（`src/schemas.py:55`），
  `406a07e` 把截断契约冻结成回归项，已并入 `verify_all.sh --fast`。
  实测：输入 384 字 → 输出 140 字，无 `ValidationError`；`origin/main a2a944c` 上同样生效。
- **原建议**（已按此实现，无需再改）：`@field_validator("fact_summary_150", mode="before")` 截断（对齐 `sanitize_title_length`）。

### 1.2 P1｜`renderer/public/runs/` 失效软链把 Remotion 打包打崩

- **现象**：残留指向已删 run 的软链时，Remotion bundler 递归 `realpath` 直接 ENOENT：
  ```
  Error: ENOENT: no such file or directory, realpath
    renderer/public/runs/20261109-183153-c1c2
    at copyDir (@remotion/bundler/dist/copy-dir.js:26)
  ```
  竖版 + 横版**同时**失败（`EXIT=64`），报错完全指不到真因。
- **真实失败 run**：`qwen4max-api-1`（日志 `/tmp/render_q4m.log`），本次清掉
  `openai-devs-tomorrow-3`、`20261109-183153-c1c2` 两条失效软链后跑通。
- **建议**：`render` 前置清理失效软链
  `find renderer/public/runs -maxdepth 1 -type l ! -exec test -e {} \; -delete`，
  或纳入 RUNBOOK §2 的 `rm -rf renderer/.cache` 同一处。

### 1.3 P1｜BLOCKED run 残留导致新 `--run-id` resume 到已删目录

- **现象**：`qwen4max-api-1` 失败留下 `pipeline_runs(BLOCKED)` + `content_tasks(intake)` 后，
  用 `--run-id qwen4max-api-2` 重跑，runner 仍去读 **api-1** 的产物：
  ```
  draft_script 不存在: output/20261111/qwen4max-api-1/draft_script.json
  ```
  必须手动删 `pipeline_runs` / `content_tasks` 行 + 删目录，`-3` 才跑通。
- **日志**：`/tmp/render_q4m2.log`
- **建议**：显式传 `--run-id` 时不应复用同 `task_id` 旧 run 的产物路径；
  或产物路径失效时自动从 `expand_script` 重头跑，而不是直接报缺产物。

---

## 二、投递失败（网关侧，连续 4 次）

### 2.1 P0｜`MEDIA:` 发视频，用户收到错片（4 次复现）

- **现象**：`MEDIA:/tmp/video_xxx.mp4` 走网关 `plugins/platforms/feishu/adapter.py::send_video`
  （`msg_type=media`），用户收到的是**上一条视频**，或**两条视频都是横屏**。
  发生时刻：`07:44:21/22`、`09:16:23/24`、`11:33:50/53`、`17:23:01/05`。
- **排除项**：本地产物完全正确 —— ffprobe 竖版 1080×1920 / 横版 1920×1080，md5 互异，
  抽帧 OCR 画面文字与选题一致，时长一致。**问题 100% 在投递层。**
- **定位证据**：
  - 拉取会话最近 150 条消息，类型分布 `post 48 / text 64 / interactive 29 / nonsupport 8 / image 1`，
    **`media` 类型为 0**；8 条 `nonsupport` 两两成对，时间与 4 次投递完全吻合。
  - `GET /open-apis/im/v1/messages/{message_id}` 对 media 消息返回 **500 `2200 Internal Error`**
  - `GET /open-apis/im/v1/messages/{id}/resources/{file_key}?type=file` 返回 **`234003 File not in msg`**
  - `?type=media` 返回 `234001 Invalid request param`
  → 网关这条路**发出去即不可回读**，投递结果是黑盒，只有用户反馈才知道错。
- **已验证的绕行（100% 可用，已固化 RUNBOOK §5.1.0）**：
  `scripts/send_video_verified.py` 直连 `im/v1/files`(`file_type=mp4`) + `im/v1/messages`(`msg_type=media`)，
  文件名 `{label}_{portrait|landscape}_{宽x高}_{md5前8}.mp4`，返回 `file_key`/`message_id`。
  用户已确认画面与文件名一致 ✅
- **请你在网关修**：
  1. `send_video` 上传后 `file_key` 是否真对应本次文件（怀疑复用旧 `file_key` / 文件名）
  2. media 消息为何在飞书侧变 `nonsupport` / 不可读 —— 对照官方 `msg_type=media` 要求逐项核对
  3. 投递后自检 `GET /im/v1/messages/{id}`，失败即报错，别静默当成功

---

## 三、发布链路问题

### 3.1 P1｜抖音 CDP 掉线无探测，失败还留 ledger claim

- **现象**：Chrome `--remote-debugging-port=9333` 窗口关闭后，
  `publish_douyin_cdp.py` 直接 `ECONNREFUSED 127.0.0.1:9333`，
  且这次失败在 `publish_ledger` 留下未收尾 claim（`f8f1edbf13e9fd14971c98f7ccfdef1d` @ 17:44:58），
  下一次发布会**被查重拦截**：
  ```
  ⛔ 发布前查重拦截：douyin/douyin-tech-main 有未收尾的发布（claimed @ 17:44:58）
  ```
  必须人工先回读平台确认没发出去，再 `python3 -m src.publish_ledger --release <key>`。
- **建议**：① 发布前探测 9333 端口，掉线自动拉起 Chrome 或明确提示「先启动 CDP Chrome」；
  ② CDP 连接阶段的失败不应占用 ledger claim（连接都没建立，不构成「可能已提交」）。

### 3.2 P2｜B站置顶首评必然先失败一次

- **现象**：首评发出后立即调置顶接口，稳定返回 `-404 啥都木有`（稿件未索引），
  15s 重试后第 2 次才 `code=0`。目前靠 `seed_comment` 重试 3×15s 兜住，但日志每次都先报一次错误。
- **位置**：`src/providers/bilibili_comment.py:216`（-404 直接 `return success=False`）＋
  `scripts/bilibili_live_uploader.py:172-179`（3×15s 重试）—— 重试只在上层，**provider 本身硬失败**，
  谁直接调 provider 谁踩坑。
- **建议**：重试下沉到 provider（-404 类 3×15s），上层只读最终结果；或发布成功后先轮询
  `resolve_bvid_by_title` 命中索引再发置顶，减少无效调用与噪音。

### 3.3 P1｜抖音「成功判定」用整页 inner_text 模糊包含，假阳性/假阴性双向都有

- **位置**：`src/providers/douyin.py:337-359` `_verify_published`
- **现象**：判据是 `key in page.inner_text("body")`（key = 标题前 14 字），拿**整页 OCR 文本**做包含匹配：
  - 假阳性：侧边栏/推荐位/历史作品里出现过同名片段就判成功；
  - 假阴性：OCR 串行粘连或乱序时命中不到，把**已发布判成失败**。
    例：`/tmp/dy_codex.log:5` 回读成「…R开命发者可据此自建同类终端智能体命a令中心…」这种乱码串。
- **建议**：读作品列表**首条卡片的标题 DOM 节点**做精确比对，文本包含只作兜底；
  与 `publish_douyin_cdp.py` 的 `VERIFIED_OK`（作品数 +1 且标题命中）判定对齐，别两套标准。

### 3.4 P2｜B站「21009 标题不合法」提交前无校验

- **位置**：发布入口从 `draft_script_json` 取文案的链路（`scripts/bilibili_live_uploader.py` 无 title 前置校验）
- **现象**：`draft_script_json` 取不到标题时退化成空标题，跑到接口才被拒：
  ```
  {'code': 21009, 'message': '标题不合法', 'ttl': 1}
  ```
- **证据**：`/tmp/pub_codex.log:10`
- **建议**：提交前校验 `title.strip()` 非空且 ≤30 字（超长按语义换短标题，**禁止物理截断**），
  不满足直接给可读错误并拒绝提交，不要走重试。

### 3.5 P3｜`bilibili_api` 只装在 agent-reach 的 venv，换解释器即 ModuleNotFoundError

- **现象**：homebrew python 下必现
  ```
  ModuleNotFoundError: No module named 'bilibili_api'
  ```
  只有 `~/.agent-reach/tools/bili-cli/venv/bin/python3` 有。跑到一半才崩。
- **证据**：`/tmp/pub_bili_hive.log:4`
- **建议**：脚本启动即 `import bilibili_api` 自检，失败输出修复命令并退出；或给项目固定 uv venv，
  不依赖外部目录（`~/.agent-reach` 可能被其它会话改动）。

### 3.6 P3｜抖音作者首评（seed_comment）链路未接

- **现象**：配置已就位（`accounts.yaml` 的 `seed_comment.pin=true`），但抖音这一路始终打印
  `seed_comment: 已配置(置顶=True)，抖音 CDP 首评链路待实拍验证后启用` —— 代码没接，配置形同虚设。
- **证据**：`/tmp/pub_13_batch.log:10`
- **建议**：走 CDP 真实 UI 发作者首评 + 置顶，实拍验证后启用；短期不做就在 `RUNBOOK §5.1.2`
  明确写「抖音首评暂不支持」，别留一个看起来生效、实际不生效的开关。

### 3.7 P3｜视频号「内容由 AI 生成」声明 Web 端无控件（平台能力缺口）

- **位置**：`src/providers/channels_cdp.py:95,312`
- **现象**：视频号 Web 端确实没有 AI 声明控件，当前只能手机端人工补勾；代码已如实记录
  `视频号 Web 无 AI 声明控件（需手机端补勾）`。
- **证据**：`/tmp/ch_pub.log:11`、`/tmp/pub_channels_claude.log:4`
- **建议**：属平台缺口不是 bug，保持现状；但要把「需手机端补勾 AI 声明」写进**发布回执**（目前只在 stdout），
  确保每条发布都不会漏掉合规声明。

---

## 四、速报内容质量问题

### 4.1 P1｜翻译语义反转 + 术语错译（会直接影响出片物料）

- 原文 `Qwen4-Max Preview is out on API access. No word on weights release yet`
- 实际输出：`Qwen4-Max 预览版**已不再支持** API 访问。目前还没有关于**重量释放**的消息`
- 两处错：
  1. `is out on API access`（**已上线** API）译成「已不再支持 API 访问」—— **语义完全反转**
  2. `weights release`（**权重发布/开源**）译成「重量释放」—— 术语错
- **影响**：速报快照标题即出片物料来源，错译会直接把错误结论带进视频脚本。本次我在写物料时人工纠正了。
- **建议**：`translate_to_zh` 对含 `weights / release / open-weight` 等术语走固定词表；
  对 `is out / now live / available` 这类状态词加反向校验（出现「不再」须人工复核）。

### 4.2 P2｜封面模板硬编码 `pip install`（✅ 已修复）

- **位置**：`renderer/src/Root.tsx` `CoverStill`
- **现象**：固定渲染 `$ pip install {props.resource_keyword?.toLowerCase()}` + 「开源技术速递」。
  Anthropic IPO、Signal 形式化验证、Qwen4-Max 这类新闻/论文条目，封面照样显示 `pip install xxx`。
- **现状**：✅ 已在 `803b583` 修复 —— 新增 `asPackageName()`（`Root.tsx:174`，正则只放行英文包名），
  `:293` 改为 `pkg ? \`$ pip install ${pkg}\` : (cover_sub_text || topicLabel(theme))`；
  中文短语/整句/空值不再渲染假命令。`verify_all.sh --fast` 已含「封面硬编码扫描」回归项。
- **原建议**（已按此实现，无需再改）：按 `target_account_type` 分支渲染，或 `resource_keyword` 为空时整行不渲染。

---

## 五、已修复项（我这边回归验证通过，供你 review）

| 项 | 验证结果 |
|---|---|
| B站新稿 view API `-404` 无法区分审核中 | ✅ 新增 `src/publish_evidence.py::resolve_bvid_by_title`（走 `member.bilibili.com/x/web/archives`，3×5s 重试） |
| `DouyinProvider` 假成功 | ✅ `_verify_published()` 真实打开内容管理页回读标题，未命中判「拒绝虚报成功」（判定方式仍待 3.3 加固） |
| `fact_summary_150` 超 schema 上限崩渲染 | ✅ `803b583` + `406a07e`：按标点截断 + 冻结回归契约（实测 384 字→140 字） |
| 封面硬编码 `pip install` | ✅ `803b583`：`asPackageName()` 只放行英文包名，其余回退副标题/主题标签 |
| 僵尸 RUNNING 收尸 / step 级 retry / 卡片回调 / 视频号 iframe 定位 | ✅ `verify_all.sh --fast` 全绿 |

---

## 六、验收方式

```bash
bash scripts/verify_all.sh --fast
```

- 1.1 用 `fact_summary_150='x'*250` 建 model 复现（现已截断通过，作为回归项）
- 1.2 造一条指向不存在目录的 `renderer/public/runs/*` 软链后跑任意 `--run-id`
- 1.3 先跑一次失败 run，再用新 `--run-id` 重跑
- 2.1 走 `MEDIA:` 发任意 mp4，然后在飞书拉消息列表看 `msg_type`
- 3.1 关掉 9333 的 Chrome 后跑 `publish_douyin_cdp.py`（应得可读指引 + 约定退出码，不占 ledger claim）
- 3.2 只调 `bilibili_comment.pin`（不经 uploader）发一次置顶，应自动重试后成功
- 3.3 发一条抖音后立即回读作品列表，校验判定读的是首条卡片 DOM 而非整页文本
- 3.4 用 title 为空的物料跑 B站发布，应在提交前被拦下，不产生 21009
- 3.5 homebrew python 直跑上传器，应在启动阶段报依赖缺失并给出修复命令
- 3.6 抖音发布后应自动发作者首评并置顶（或 RUNBOOK 明确写不支持）
- 3.7 视频号发布回执里应固定包含「需手机端补勾 AI 声明」

改完回我结论即可，我按上面的复现方式逐条回归。

---

## 七、2026-11-11 12:00 之后 · 「发视频」链路问题时间线（小品使用侧实录）

> 本节只记录**我作为使用方**遇到的错误与阻碍，不含代码改动建议的最终拍板。
> 所有时刻均为飞书消息时间（UTC+8），可从会话消息 API 复现核对。

| 时刻 | 环节 | 发生了什么 | 后果 |
|---|---|---|---|
| 17:10:31 | 出片（渲染） | `renderer/public/runs/` 存在指向已删 run 的失效软链，Remotion bundler `realpath` 报 ENOENT，竖版+横版同时失败 `EXIT=64` | 第 8 条视频第一次出片失败，`qwen4max-api-1` BLOCKED |
| 17:19:42 | 出片（重跑） | 清掉 `openai-devs-tomorrow-3`、`20261109-183153-c1c2` 两条软链后 `qwen4max-api-3` 成功 | 竖 1080×1920 / 横 1920×1080，27.63s |
| 17:23:01 / 17:23:05 | **发视频** | 用 `MEDIA:` 发两条成片，用户收到「两条都是横屏 + 第一条是上一个视频」——**同问题第 4 次** | 用户需人工判断哪条是哪条，交付信任受损 |
| 17:29:39 → 17:31:40 | **发视频（排查）** | 为定位问题连续发送 6 条测试视频，飞书侧全部落为 `nonsupport`（不可读） | 给用户造成噪音，且仍拿不到可核对的回执 |
| 17:44:58 | **发视频（抖音）** | `publish_douyin_cdp.py` 连 9333 直接 `ECONNREFUSED`（Chrome 窗口已关），且本次失败在 `publish_ledger` 留下未收尾 claim `f8f1edbf13e9fd14971c98f7ccfdef1d` | 17:48 重发被「有未收尾发布」查重拦截，需人工先回读平台、再 `--release` 才能重试 |
| 17:47 | **发视频（新路径验证）** | 改直连 OpenAPI（`im/v1/files` + `im/v1/messages`）发送，文件名带方向/分辨率/md5 前 8 位 | 用户确认「最后两个是对的」→ 投递层问题定性完成 |

### 7.1 核心结论（已定性，有证据）

`MEDIA:` 走网关 `adapter.py::send_video`（`msg_type=media`）发出的视频消息，**在飞书侧一律不可回读**：

- 会话最近 150 条消息类型分布：`post 48 / text 64 / interactive 29 / nonsupport 8 / image 1`，**`media` 为 0**
- 8 条 `nonsupport` 两两成对，时间正好对应 4 次投递（`07:44`、`09:16`、`11:33`、`17:23`）
- `GET /open-apis/im/v1/messages/{message_id}` → **500 `2200 Internal Error`**
- `.../resources/{file_key}?type=file` → `234003 File not in msg`；`?type=media` → `234001 Invalid request param`
- 同批文件用 OpenAPI 直连发送 → 用户确认画面正确 ✅

→ 结论：**投递是黑盒，我无法自证发对了**。这不是产物问题（ffprobe/md5/抽帧 OCR 全部正确），是投递层问题。

### 7.2 我作为使用方需要的三件事（请按此交付）

1. **可自证的投递**：无论走哪条实现，发送后必须能回读「这条消息里的文件就是我这个 md5」，回不来就算失败并报错。
2. **失败不占坑**：连接阶段就失败的发布（`ECONNREFUSED` 这类）不应占用 `publish_ledger` claim，否则一次掉线会连累下一次重试。
3. **文件名自带语义**：`{run_id}_{portrait|landscape}_{宽x高}_{md5前8}.mp4` 这种命名请在流水线里固化，我按名字就能判断竖/横，不必看画面。

### 7.3 当前我的处置（不改代码，只用）

- 发视频一律走 `scripts/send_video_verified.py`（RUNBOOK §5.1.0 已写明），不再用 `MEDIA:`
- 抖音发布前先 `curl 127.0.0.1:9333/json/version` 探活，掉线先拉起 CDP Chrome 再发
- 遇到 `publish_ledger` 拦截，先回读平台确认未发出，再 `--release` 重试
