# SPEC-20261113 · 抖音发布改走常驻 Chrome（CDP 接管）

| 项 | 内容 |
|---|---|
| 提出 | 小品（产品经理，使用侧） |
| 实现 owner | 小何（技术） |
| 验收 owner | 小品（同一套复现命令回归） |
| 提出时间 | 2026-11-13 23:2x |
| 优先级 | P1（抖音发布连续 LOGIN_REQUIRED，阻塞每日分发矩阵） |
| 关联 run | `llamacpp-v1-1`（2026-11-13 22:52，抖音失败，用户已批准「A 做好后再发」） |

## 1. 背景与现状（已实测）

1. 2026-11-13 `966d251` 已把抖音发布从无头改为**有头**（`src/providers/douyin.py:65-67` 默认 `headless_publish=False`，`executors.py:474` 默认 `headless=False`）。**无头问题已不复存在。**
2. 但 provider 每次仍 `p.chromium.launch()` 起一个**全新临时 profile**，仅靠 `data/browser_profiles/douyin-tech/storage_state.json` 这一份**快照**维持登录（`douyin.py:72-100`）。
3. 该快照 cookie 的 `expires` 写着 2027-01-09，但服务端会话已被判失效：
   - `llamacpp-v1-1` 发布 → `LOGIN_REQUIRED`（`/tmp/render_llamacpp.log` 收尾 `[publish] 执行失败：发布失败: douyin`，EXIT=64）；
   - 常驻 Chrome 9333（`--user-data-dir=…/data/browser_profiles/douyin-cdp`）打开 `creator-micro/content/manage`，正文是「扫码登录 / 验证码登录 / 密码登录」= 未登录；
   - `scripts/sync_douyin_storage.py` dry-run 仍能导出 49 条 cookie（含 `sessionid`），**证明「有 cookie」不等于「会话有效」**，靠快照同步救不回来。
4. 视频号已经跑通「常驻 Chrome + CDP 接管」模式（`src/providers/channels_session.py` + `src/providers/channels_cdp.py`，端口 9334），抖音缺同构能力。

**结论**：登录态必须由**人工登录一次、浏览器 profile 持久保留**来维护，不能让流水线每次用快照重建临时环境。

## 2. 需求（做什么）

**R1｜账号级开关（默认关闭，向后兼容）**
`config/accounts.yaml` 的 `douyin-tech-main` 增加可选项：
```yaml
cdp_url: "http://127.0.0.1:9333"   # 留空/缺省 = 走现有 launch + storage_state 逻辑（老行为不变）
```
（`pipeline.yaml` 已声明 `DOUYIN_CDP_URL`，可继续作为端点覆盖来源；优先级：账号配置 > 环境变量 > 内置默认。）

**R2｜provider 支持 CDP 接管**
`src/providers/douyin.py` 的 `_get_browser_context()` 增加分支：当 `cdp_url` 生效时
`p.chromium.connect_over_cdp(cdp_url)` → 取 `browser.contexts[0]`（真实 profile，持久登录态）→ 后续上传/填标题/可见性/AIGC/存草稿逻辑**一行不改**继续走。
`check_login_status()` 同步复用该分支。

**R3｜发布前探活，失败不占台账**
连通 9333 的探活必须在 `check_and_claim` **之前**（参考 `src/providers/douyin.py` 现状与 `scripts/publish_douyin_cdp.py:122` 的 `_probe_cdp`）。
探活失败 → 抛可识别错误 `CDP_UNAVAILABLE`，**台账不产生 `claimed`/`unverified` 脏记录**（当前 `llamacpp-v1-1` 就是被一条 `unverified` 挡住重发，需人工 release）。

**R4｜页面命中登录页 → 可读错误**
CDP 会话打开上传页后若出现「扫码登录 / 验证码登录」：返回 `LOGIN_REQUIRED`，错误文案带 `profile=douyin-cdp`、`cdp=127.0.0.1:9333`，并提示「需人工在该 Chrome 窗口扫码」。

**R5｜单实例互斥**
同一时刻只允许一个发布流程占用 9333 会话；并发（并行会话/多 run）时排队或明确失败（`CDP_BUSY`），**不准两个流程抢同一 tab**。

**R6｜既有能力不许退化（回归线）**
可见性 `public_publish` 勾「公开可见」fail-closed、AIGC 声明勾选、回读判据（作品列表标题命中）、`post_id=readback_*` 落台账——全部保持现状语义。

## 3. 不做（Out of scope）

- 不改 B站 / 视频号发布链路；不改审批卡与审批门禁；不改台账状态机语义（只要求前置失败不落脏记录）。
- 不新增第三方依赖（playwright 已够）。
- 不要求自动化扫码/短信验证（那是人工动作：抖音遇短信验证由用户甩 6 位码当场填）。

## 4. 前置人工步骤（由小品/用户执行，非代码）

1. 在 9333 窗口扫码登录 `creator.douyin.com`（profile `data/browser_profiles/douyin-cdp`），一次登录长期保留；
2. 登录后先跑 `python3.14 scripts/publish_douyin_cdp.py --run-id <run> --dry-run` 验证 CDP 会话可用（当前 EXIT=0，`BEFORE 未识别` 即未登录）。

## 5. 验收标准（小品按同一套命令回归，缺一条不算通过）

| # | 命令/场景 | 通过判据 |
|---|---|---|
| A1 | 9333 已登录，`--publish douyin` 跑一次真发布 | `publish.json` 里 douyin `status=ok`，台账 `post_id=readback_*`，创作者后台作品列表**标题命中** |
| A2 | 关闭 9333（kill Chrome）再跑同一 run | 非 0 退出 + 错误含 `CDP_UNAVAILABLE`；`publish_ledger` 里**没有**新增 douyin 记录（无 claimed/unverified 脏行）|
| A3 | `cdp_url` 置空 | 回退旧 launch 逻辑仍可跑（行为与今天一致，允许 LOGIN_REQUIRED）|
| A4 | 回归三项 | 公开可见已勾选（`public_publish`）、AIGC 已声明、回读标题命中（日志有 `VERIFIED_OK` 等价输出）|
| A5 | 并发：同时起两个发布 | 一个成功、另一个明确 `CDP_BUSY`，无重复投稿 |

## 6. 影响面

| 文件 | 改动 |
|---|---|
| `src/providers/douyin.py` | `_get_browser_context` 增 CDP 分支 + 探活 + `CDP_UNAVAILABLE`/`CDP_BUSY` 错误 + `check_login_status` 复用 |
| `config/accounts.yaml` | `douyin-tech-main` 增 `cdp_url` |
| `src/pipeline/executors.py` | 预计无需改（provider 内部切换）；若需把 `CDP_BUSY` 映射成 `skipped` 则仅一处分支 |
| 测试 | 建议补 `scripts/test_douyin_headless.py` 同级用例：无 CDP 时回退、CDP 不可达时不占台账 |

**风险**：CDP 会话被人工操作打断（人在那个窗口点了别的页面）→ 需 R5 互斥 + 明确错误；真机 profile 含真实登录态，凭据不可外泄（沿用现有 `chmod 600` 约束）。

## 7. 上线后我要做的事

A 验收通过后，立刻对 `llamacpp-v1-1` 补发抖音（先 `publish_ledger --release 031a1804a83ed8d6edb43e2e1f29f99c` 清掉那条 `unverified` 脏记录），以平台后台作品列表回读为唯一成功判据，回执一行 ≤100 字。
