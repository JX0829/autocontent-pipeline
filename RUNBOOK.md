# RUNBOOK（照抄执行版）

> 这是**执行手册**，只写「怎么做」。设计原因、历史事故、方案论证在
> `docs/RUNBOOK-20261118-full.md`（1288 行完整版，2026-11-18 归档）。
> 使用方（小品）：给物料 → 出片 → 审批 → 发布 → 回执，四个环节都在这份里。
> 本文件里每条命令都由 `scripts/verify_runbook.py` 校验（脚本存在 / 参数真实定义 / 解释器显式），
> 已挂进 `verify_all.sh`，改文档写错命令会直接被门禁拦下。

## 0. 三条铁律

1. **解释器只用 `$PY`**：`/opt/homebrew/opt/python@3.14/bin/python3.14`。
   裸 `python3` 会命中 conda，缺 `lark_oapi`/`edge_tts` → 自检直接 `python_deps FAIL`。
2. **出片和发布只有一条路**：`$PY -m src.pipeline.runner …`。
   不直连平台页面、不调 provider、不用任何单平台脚本（视频号的已删除）。
3. **发布必须过两门**：审批 `approve` + 台账 `publish_ledger`。
   跑不通就去排障（§7），**不要绕过门禁**——绕过去等于跳过审批和去重。

```bash
# 每个新 shell 先定义一次
PY=/opt/homebrew/opt/python@3.14/bin/python3.14
cd ~/Downloads/workspace/team/project/autocontent-pipeline
```

---

## 1. 环境自检（30 秒，出片前必跑）

```bash
bash scripts/verify_all.sh --fast
```

期望最后一行 `VERIFY PASS`。红了先看 §7.1，不要硬着头皮出片。

---

## 2. 出片（生成视频）

### 2.1 正常出片：只出片、不发飞书、不发布

```bash
DISPATCH_MODE=output_only $PY -m src.pipeline.runner \
    --material materials/20261118_openai_chile.json \
    --run-id openai-chile-1
```

- `DISPATCH_MODE=output_only` = 不投递飞书；**发布本来也不会自动发生**（还要审批 + 台账）
- 全流程约 3~6 分钟（脚本 → TTS → 竖版渲染 → 横版渲染）
- 产物落 `output/<日期>/<run-id>/`：

| 文件 | 说明 |
|---|---|
| `video.mp4` | 竖版 1080x1920（抖音、视频号） |
| `video_16_9.mp4` | 横版（B站） |
| `cover.png` / `cover_16_9.png` | 两版封面 |
| `video_body.mp4` | 竖版**正片**（去掉片头封面段，出问题用它比对） |
| `draft_script.json` | 脚本+分镜（改文案要从这里改或换物料） |
| `artifacts.txt` | 本次产物清单 |

### 2.1.1 封面 = 片头首帧（2026-11-18 起，不用再单独上传封面）

竖版渲染完会自动把 `cover.png` **贴到成片最前面静止播放 0.5s**，然后接正片：

| 项 | 值 |
|---|---|
| 时长 | 正片时长 + 0.50s |
| 首帧 | 就是 `cover.png`（PSNR 42dB，肉眼一致） |
| 口播 | 音频整体后移 0.5s，不脱轨（前 0.45s 静音，实测 -91dB） |
| 失败 | `COVER_PREPEND_FAILED`，并把 `video_body.mp4` 恢复成 `video.mp4`，不产半成品 |

**所以：抖音/视频号都不需要再上传封面**——平台默认取首帧，首帧已经是封面。
抖音上传页的「设置封面」控件一直不稳定，这条链路不再依赖它（也不再新建上传能力）。
横版（B站）不受影响，仍走自己的 `cover_16_9.png`。

回归：`scripts/test_cover_prepend.py`（已挂 `verify_all --fast`）。

**封面文案必须活在 3:4 里**（2026-11-18）：抖音个人主页/作品列表把 9:16 封面按 3:4 居中
裁剪显示，1080x1920 只剩 y∈[240,1680]。封面文案块底部内边距从 210 收到 **260**
（块底 y=1660），最低文字实测 y=1640，留 40px 余量。回归 `scripts/test_cover_34_safe.py`
（静态判据 + 真渲染长文案），已挂 `verify_all --fast`。

### 2.2 干跑看计划（不调模型、不写库）

```bash
$PY -m src.pipeline.runner --material materials/20261118_openai_chile.json --dry-run
```

### 2.3 重渲已有作品 ⚠️ 最容易踩

**同一物料 + 同一账号再跑不会重渲**：`run_id` 按 `content_hash` 复用，显式传新
`--run-id` 也照样被跳过：

```
=== 幂等跳过（uq_run_content）===
  同内容已出片: run_id=openai-chile-1 status=DONE
```

要真重渲，回到旧 run 上重置起点（只重做画面，LLM/TTS 复用）：

```bash
DISPATCH_MODE=output_only $PY -m src.pipeline.runner \
    --material materials/20261118_openai_chile.json \
    --run-id openai-chile-1 --resume --from-step render_vertical
```

| 想做什么 | 加什么 |
|---|---|
| 只重渲画面（改模板/字体/尺寸/封面后） | `--from-step render_vertical` |
| 只重做发布（不重渲） | `--from-step publish --publish <平台>` |
| 连脚本一起重写 | 换物料文件，或删掉 `draft_script.json` 再跑 |

> `--from-step` 之后所有步骤都会跑（含 approval/publish）。只想出片务必带
> `DISPATCH_MODE=output_only`；即使漏了，发布仍会被审批/台账拦住。

### 2.4 把视频发给飞书

```bash
$PY scripts/send_video_verified.py \
    --video output/20261118/openai-chile-1/video.mp4 \
    --video output/20261118/openai-chile-1/video_16_9.mp4 \
    --label openai-chile-1
```

- **禁止**用 `MEDIA:` 发视频：macOS `/tmp` 是软链，飞书上传会静默失败
- 竖版和横版**两个文件都要发**，只发一个算没交付

---

## 3. 物料（`materials/*.json`）

### 3.1 字段

```json
{
  "title": "OpenAI出海，这次先签政府",
  "body": "正文素材……",
  "source_url": "https://example.com/a",
  "account": "douyin-tech-main",
  "tags": ["OpenAI", "智利", "AI政策"]
}
```

| 字段 | 要求 |
|---|---|
| `title` | **必填**，≤20 字（超限报 `TITLE_TOO_LONG`，不静默截断） |
| `body` 或 `source_url` | **二选一必填**，都没有直接 `ValueError` |
| `tags` | 可选，**≤6 个**（超了报 `TAGS_OVER_LIMIT`）；写了就是硬约束 |
| `account` | 可选，默认 `douyin-tech-main` |
| `images` | 可选，参考图 |

### 3.2 硬约束（`src/pipeline/material_binding.py`）

物料里的 `title` **原样绑定成片标题**（不允许模型改写）；`tags` = 物料 tags + 生成 tags
去重后取前 6，**物料在前**。生成后自证，审计落 `material_binding.json`。

### 3.3 物料报错码

| 报错 | 含义 | 处置 |
|---|---|---|
| `TITLE_TOO_LONG` | title > 20 字 | 改物料，别指望截断 |
| `TAGS_OVER_LIMIT` | tags > 6 | 删到 6 个以内 |
| `ValueError`（缺字段） | 既无 `body` 又无 `source_url` | 补一个，代码里没有占位兜底 |

### 3.4 速报转物料（量产链路）

```bash
$PY scripts/digest_to_material.py --list
```

---

## 4. 审批

- 出片后 `dispatch` 发审批卡到飞书，**点「✅ 批准」即 approve**（卡片回调直接落
  `approval.json`）
- 审批步默认 **600 秒超时自动 reject**；超时了重新跑 `--from-step approval`，别手改 json
- 发布前 `approval.json` 必须是 `{"decision": "approve"}`，否则 `发布拦截（门禁2 审批）`
- 晚到的卡片点击会被 `_adopt_late_decision` 按 mtime 补记，不会白点

---

## 5. 发布

### 5.1 一条命令发布

```bash
$PY -m src.pipeline.runner --material materials/20261118_openai_chile.json \
    --run-id openai-chile-1 --resume --publish douyin,bilibili,channels
```

- ⚠️ **`--publish` 默认真发**（内部 `dry_run=false`）
- 只覆盖本次运行的 `publish.enabled` 门禁；**审批 + 台账照旧生效**
- 平台按自己方向选片：抖音/视频号 `video.mp4`，B站 `video_16_9.mp4`
  （唯一来源 `src/utils/accounts.py::PLATFORM_ASPECT`）

### 5.2 预检 / 补发

```bash
# 只预检，不真发、不占台账、不改卡片
$PY -m src.pipeline.runner --material materials/20261118_openai_chile.json \
    --run-id openai-chile-1 --resume --publish channels --publish-dry-run

# 停了发布步之后再补发（publish 步 DONE 会被断点跳过，必须 --from-step）
$PY -m src.pipeline.runner --material materials/20261118_openai_chile.json \
    --run-id openai-chile-1 --resume --from-step publish --publish channels

# 上传+填文案+勾原创，停在发表前（验选择器用；后台会留一条未发表记录）
$PY -m src.pipeline.runner --material materials/20261118_openai_chile.json \
    --run-id openai-chile-1 --resume --from-step publish --publish channels --stop-before-publish
```

### 5.3 三平台差异（必须知道）

| 平台 | 成片 | 账号 | 特别注意 |
|---|---|---|---|
| 抖音 | `video.mp4` | `douyin-tech-main` | **必须 CDP 9333 真机有头**；标题填「作品标题」独立框（≤30 字），正文不含标题和话题，话题与正文一次原子插入 |
| B站 | `video_16_9.mp4` | `bilibili-tech-main` | 横版；简介 `正文\n\n#标签`；首评置顶失败只告警不回执 |
| 视频号 | `video.mp4` | `channels-tech-main` | **必须 CDP 9334 常驻有头**（无头一律失败）；登录态常过期，见 §7.3 |

通用：`publish.aspect` 必须留空（写了就强制覆盖，会短路上面的平台映射）。
封面：竖版首帧即封面（§2.1.1），**三平台都不做单独封面上传**；横版用 `cover_16_9.png`。

竖版安全边距：模板 100px（9.3%），因为抖音信息流会裁掉左右各约 9%（原 88px 裁完贴边）。
`scripts/audit_safe_area.py` 防回退，已挂 `verify_all`。

### 5.4 结果口径（只认这三态）

| 台账 status | 含义 | 回执卡 |
|---|---|---|
| `published` + `post_id`（视频号形如 `export/UzFf…`） | 平台回读命中，有凭证 | ✅ 已发布 |
| `unverified` | 点了发表但拿不到可回读凭证 | ⚠️ 已提交但未验证 |
| `failed` | 确认没发出去 | ⚠️ …失败：原因 |

一个 run **只有一张卡**：后续单平台补发也是回写这张卡，不会另发一张。

### 5.5 简介 / 标题规范

| 平台 | 格式 |
|---|---|
| 视频号 | `正文`（provider 内部拼 `#标签`，调用方传 `include_tags=False`） |
| 抖音 | 标题走「作品标题」框；正文只放正文；话题独立成段 |
| B站 | `正文\n\n#标签`，标题走 title 字段 |

抖音回读不符直接拒发：`TITLE_NOT_LANDED` / `CAPTION_MISMATCH`。

---

## 6. 台账与回执

```bash
# 看某平台记录
$PY -m src.publish_ledger --list channels

# 发错了要重发：先放行（只允许 failed / deleted）
$PY -m src.publish_ledger --release <publish_key>

# 平台侧已删除
$PY -m src.publish_ledger --deleted <publish_key>

# 补凭证（拿到作品 id 后）
$PY -m src.publish_ledger --attach <publish_key> <作品id> --mark-published
```

- 台账状态只放行 `failed` / `deleted`，`published` 必须有 `post_id`，否则 exit 3
- 台账在 `data/pipeline_state.db`（`data/publish_ledger.db` 是空壳，别查错）
- 时间戳是本机 local 时间，手写 UPDATE 用 `datetime('now','localtime')`

---

## 7. 排障速查

### 7.1 `verify_all` 红项

| 红项 | 原因 | 处置 |
|---|---|---|
| `python_deps FAIL` | 用了裸 `python3`（conda） | 换 `$PY` |
| `渠道无头巡检` | 有人把 `headless=True` 写回来 | 看 `scripts/audit_channels_headful.py` 输出 |
| `RUNBOOK 命令自检` | 文档里的命令/参数已失效 | 改文档，或 `scripts/verify_runbook.py --verbose` 定位 |
| `静默失败扫描` | 新增 `except: pass` | 加 `logger.debug` 留痕 |
| `抖音话题真生效` | 抖音话题实现被改坏 | 找开发，别自己改 provider |

### 7.2 常见失败码

| 错误码 | 处置 |
|---|---|
| `LOGIN_EXPIRED` / `CHANNELS_NOT_LOGGED_IN` | 重新登录（§7.3），再 `--from-step publish` |
| `CDP_UNAVAILABLE` | 对应端口的 Chrome 没起；抖音 9333 / 视频号 9334 |
| `PROFILE_BUSY` | 9334 已被常驻实例占用：直接复用发布，别新开 |
| `TITLE_NOT_LANDED` | 标题没落进「作品标题」框 → 查选择器，找开发 |
| `CAPTION_MISMATCH` | 正文回读不符（话题错位）→ 拒发属正常保护 |
| 台账 exit 3 | 同内容已发过：确认要重发就 `--release <key>` |

### 7.3 视频号登录（最常做的人工步骤）

```bash
$PY scripts/channels_login.py --check

# 过期就重抓二维码，图发到群里让人扫
$PY scripts/channels_login.py --qr-out /Users/a1/Downloads/qr/channels_qr.png

# 只读探针：CDP / 有无头 / 登录态 / 发布页控件（不上传不发布）
$PY scripts/probe_channels.py
```

扫码有效后回 `--from-step publish` 补发。登录态只认 `verify_login()` 三态，
快照文件存在性、URL 含 login、宽松 markers 都是**假判据**。

### 7.4 僵尸 RUNNING

```bash
$PY scripts/reap_stale_runs.py              # 只列
$PY scripts/reap_stale_runs.py --apply      # 标记 ABANDONED（不删产物）
$PY scripts/reap_stale_runs.py --minutes 30 # 换阈值
```

---

## 8. 验收（改动后必须跑）

```bash
bash scripts/verify_all.sh --fast          # 全量门禁（约 40 秒）
$PY scripts/verify_runbook.py              # 本文档命令自检
$PY scripts/test_task_contracts.py         # 任务契约回归（PASS 计数在输出末尾）
$PY scripts/test_channels_stability.py     # 视频号稳定性
```

---

## 9. 禁止事项

1. 不直连 `/platform/post/create`（视频号会重定向回首页，必须先点「发表视频」）
2. 不用 `MEDIA:` 发视频，走 `send_video_verified.py`
3. 不手改 `approval.json` / `publish.json` / 台账来「让流程过去」
4. 不用无头浏览器发布（抖音、视频号都必须有头）
5. 不把 `publish.aspect` 写死（除手工强推某版成片）
6. 老作品不回填（历史约定），只保证新发布正确

---

## 10. 文件地图（只列要用的）

| 路径 | 用途 |
|---|---|
| `materials/*.json` | 物料 |
| `output/<日期>/<run-id>/` | 成片与全部中间产物 |
| `data/pipeline_state.db` | 运行状态 + 台账（唯一真相） |
| `src/pipeline/runner.py` | 唯一入口 |
| `src/pipeline/material_binding.py` | 物料硬约束 |
| `src/providers/{douyin,bilibili,channels_cdp}.py` | 三平台实现 |
| `scripts/verify_runbook.py` | 本文档自检 |
| `scripts/probe_channels.py` | 视频号只读探针 |
| `scripts/send_video_verified.py` | 视频投递 |

---

## 附录 A 视觉模板 / 素材库（开发向，只在改视觉时看）

- 默认模板 `tech_story`（2026-11-17 起），横竖版同模板，横版封面 = 横版首帧
- 素材放 `renderer/public/assets/`，新增素材不改代码；`COVER_FRAME` 控制封面抽帧
- BGM 音量永远低于口播；分镜「锐评」`creator_take` **必填且有门禁**
- 细节见 `docs/RUNBOOK-20261118-full.md` §5.5

## 附录 B 课文古风模板 `textbook_story`

- 触发：物料带 `template: textbook_story`（教育号默认关闭）
- 必填字段与时长剑门见完整版 §5.6；时长超阈直接失败，不裁剪
- 该分支独立维护在 `feature/textbook-story`，**未合主干前不要在生产物料里用**
