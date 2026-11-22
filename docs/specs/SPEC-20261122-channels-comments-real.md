# SPEC-20261122 · 视频号真评论抓取（方案 B）执行规格

> 归属：`小何`（技术）｜来源：2026-11-22 评论报告错位事故复盘
> 前置：方案 A 已落地（`fix/channels-comment-stopgap`，fail-closed 解析 + 作品级未读提醒 + 清 78 条垃圾缓存）
> 本文档是**可直接执行**的规格：每个任务带依赖 / 命令 / 验收命令 / 预期输出 / 回滚，末尾有状态台账与 GATE。

## 0. 目标与边界

| 项 | 内容 |
|---|---|
| 目标 | 拿到视频号评论的**真实** `用户 / 正文 / 时间 / 所属作品 / 是否已回复`，恢复「建议回复」能力 |
| 不做 | 不改抖音 / B站 评论链路；不自动直发回复（仍走人机协同）；不引入常驻服务 / launchd |
| 硬约束 | 宁缺勿假：拿不到字段必须显式缺失，禁止再从版式 DOM 猜；所有失败路径 fail-closed |
| 回滚 | 每个任务独立提交；出问题 `git revert <sha>` 即回到方案 A 状态（方案 A 不依赖 B 的任何代码） |

## 1. 未知量（先探针，不写代码）

| 编号 | 未知 | 探针手段 | 通过判据 |
|---|---|---|---|
| U1 | `/cgi-bin/mmbiz-bin/post/comment_list` 的正确入参 | 从页面 XHR 抓真实请求（hook `fetch`/`XMLHttpRequest`），而非猜参数 | 拿到一次 `errCode=0` 且 `commentList` 非空 |
| U2 | 评论面板是否走独立 iframe / 微前端 | 点开「评论管理」某条作品，枚举 frame 与 XHR | 能定位到承载评论列表的 frame |
| U3 | 登录态是否够权限 | 同一 session 下接口返回 `errCode` 分布 | 非 `300800` 类鉴权错误 |
| U4 | 是否已回复字段 | 看 `commentList[i]` 的 `replyList` / `isAuthorReplied` | 字段存在且与后台 UI 一致 |

## 2. 任务分解

### T1 抓真实请求（只读，30–45min）

- **依赖**：无
- **做法**：CDP 连接 9334 → 打开 `/platform/interaction/comment` → 注入 `window.__reqs=[]`，包裹 `fetch` 与 `XMLHttpRequest.prototype.open/send` → 点开第一条作品卡片 → dump 全部请求 URL + 关键入参 → 落盘 `data/channels_comment_probe_<ts>.json`
- **命令**：
  ```bash
  python3.14 scripts/probe_channels_comment_api.py --dump data/channels_comment_probe.json
  ```
- **验收**：dump 里出现至少 1 条含 `comment` 的请求，且打印 `params_of_interest`
- **预期输出**：`{"requests": N, "comment_like": ["/cgi-bin/mmbiz-bin/post/comment_list?..."], "params": {...}}`
- **回滚**：删脚本，无副作用（不点击任何写操作）

### T2 还原入参并打通接口（1–1.5h）

- **依赖**：T1 拿到真实 URL + 入参
- **做法**：`src/providers/channels_comments.py` 增 `fetch_comment_list(page, object_id, size, cur)`，用页面上下文 `fetch` 复用 cookie/签名；**参数只在 T1 实测过的形状上调用**，不做形状枚举
- **命令**：`python3.14 scripts/probe_channels_comment_api.py --call <object_id>`
- **验收**：返回 `errCode=0` 且 `len(commentList)>0`；字段含 `nickname / content / createTime / replyList`
- **预期输出**：`{"ok": true, "count": 2, "first": {"nickname": "...", "content": "...", "replied": false}}`
- **回滚**：删函数；解析层仍走方案 A 的 fail-closed

### T3 字段映射 + 归一化（1h）

- **依赖**：T2
- **做法**：`to_comment_record(item)` → `{comment_id, user, content, created_at, video_title, replied}`；`comment_id` 用 `objectId/commentId` 真值，**不再 `hash(text)`**；空 `content` 或空 `user` → 丢弃并计数
- **验收**：`scripts/test_channels_comment_api_parse.py` 用 T2 的真实响应样本（脱敏后入库当 fixture）断言 6 条映射
- **回滚**：`git revert`

### T4 接线（评论优先，作品级降级）（30min）

- **依赖**：T3
- **做法**：`poll_channels_comments.py` 顺序改为 `严格 DOM → comment_list 接口 → 作品级提醒`；接口成功时恢复「建议回复」生成（`reply_mode=manual_approval`），失败仍走作品级提醒
- **验收**：真机跑一轮，日志出现 `comment_list 命中 N 条`，飞书卡片有真实粉丝/正文；断网/接口失败时自动降级且不报错崩
- **回滚**：`ALLOW_CHANNELS_COMMENT_API=0` 环境变量一键退回方案 A

### T5 门禁 + 巡检（30min）

- **依赖**：T4
- **做法**：`verify_all.sh` 增 `视频号评论接口契约`；`audit_silent_failure` 增一条：接口失败但卡片仍输出「粉丝/评论」字段 → 报错
- **验收**：`bash scripts/verify_all.sh --fast` PASS；故意让接口返回 `errCode=300800`，卡片必须降级为作品级
- **回滚**：摘掉门禁行

## 3. 排期

| 顺位 | 任务 | 预估 | 累计 | 前置条件 |
|---|---|---|---|---|
| 1 | T1 抓真实请求 | 45min | 0.75h | 9334 登录态有效（当前有效） |
| 2 | T2 打通接口 | 1.5h | 2.25h | T1 出参 |
| 3 | T3 字段映射 + 单测 | 1h | 3.25h | T2 |
| 4 | T4 接线 + 降级开关 | 30min | 3.75h | T3 |
| 5 | T5 门禁 | 30min | 4.25h | T4 |

**止损点**：T1 结束时若拿不到任何 `comment` 请求（说明评论面板走 WebSocket 或纯服务端渲染），**立即停止并回报**，预估改为「Web 端不可达，需手机端/开放平台」两条替代路线，不再往下堆时间。

## 4. 替代路线（T1 止损时启用）

| 路线 | 成本 | 代价 |
|---|---|---|
| R1 手机端自动化（Appium/ADB） | 高（2–3 天） | 需真机 + 微信风控更严 |
| R2 微信开放平台「评论管理」能力 | 低（0.5 天） | 需企业主体权限，个人号大概率没有 |
| R3 只做作品级提醒 + 人工查看 | 已实现（方案 A） | 无明细，但零风险 |

## 5. 状态台账

| 任务 | 状态 | 提交 | 验收证据 |
|---|---|---|---|
| 方案 A 止血 | ✅ DONE | `fix/channels-comment-stopgap` | `test_channels_comment_parse.py` 18 PASS；真机 0 结构化候选 → 2 条作品级提醒 |
| T1 抓真实请求 | ⬜ TODO | — | — |
| T2 打通接口 | ⬜ TODO | — | — |
| T3 字段映射 | ⬜ TODO | — | — |
| T4 接线 | ⬜ TODO | — | — |
| T5 门禁 | ⬜ TODO | — | — |

## 6. GATE 检查点

- **GATE-B1**（T2 后）：接口必须返回 `errCode=0` 且 `commentList` 非空，否则走 §4 替代路线，**不许**用「猜参数 + 空结果」当通过
- **GATE-B2**（T4 后）：真机跑通一轮，飞书卡片里出现的粉丝名/正文必须能在后台同一条评论里逐字对上；对不上即 FAIL
- **GATE-B3**（T5 后）：`verify_all.sh --fast` PASS，且负向用例（接口 300800）会降级而不是编造
