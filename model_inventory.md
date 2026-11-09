# model_inventory.md — 模型调用点与环境盘点

> **产出任务**：T0.2（spec_enhancement.md §5 P0）
> **盘点时间**：2026-11-09 10:48（BJT）
> **基准分支**：`feat/pipeline-spec-enhancement`（基于 `60697fd`）
> **盘点方式**：全仓静态扫描 + 网关真实调用 + 进程/配置实测，**无一项靠记忆推断**

---

## 表 1 · 模型调用点（全仓仅 4 处，2 个文件）

| # | 文件:行号 | 用途 | 槽位建议 | `max_tokens` | JSON 模式 | 超时配置 | 截断/空值检查 |
|---|---|---|---|---|---|---|---|
| 1 | `src/hotspot_extractor.py:161` | 扩写 `DraftScriptPayload`（选题→15 字段脚本） | `llm.main` | **未设置**（走 provider 默认） | ✅ `response_format={"type":"json_object"}` | ❌ 无 | ❌ 无（`content.strip()` 后直接 `json.loads`） |
| 2 | `src/safety/comment_funnel.py:151`（`level2_classify`） | 评论恶意性分类（A/B/C/D） | `llm.fast` | `1024`（硬编码） | ❌ 无 | ❌ 无 | ❌ 无（异常兜底曾返回 `"D"`） |
| 3 | `src/safety/comment_funnel.py:214`（`level3_generate_reply`） | 生成拟稿回复 | `llm.fast` | `1024`（硬编码） | ❌ 无 | ❌ 无 | ❌ 无 |
| 4 | `src/safety/comment_funnel.py:253`（`generate_seed_comment`） | 生成种子评论 | `llm.fast` | `1024`（硬编码） | ❌ 无 | ❌ 无 | ❌ 无 |

**扫描命令**（可复现）：
```bash
grep -rn "max_tokens\|LLM_MODEL\|LLM_BASE_URL\|ChatCompletion\|client\.chat\|base_url" \
  --include=*.py src scripts | grep -v node_modules     # → 13 行命中，去重后 4 个调用点
```

**核对结论**：
- 全仓**没有任何一处配置了 LLM 超时**（`timeout=` 零命中）→ QA#3「超时预算不足」根因在盘点上就被证实。
- 4 处调用点**全部自定 `max_tokens` 或不设**，与 `contract.model_switch.forbid_caller_max_tokens` 直接冲突 → 由 **T1.5** 收口到 `src/llm/client.py`。

---

## 表 2 · 配置一致性（`.env` ↔ 网关实况）

| 键 | `.env` 实际值 | 代码默认值（兜底） | 网关是否存在 | 判定 |
|---|---|---|---|---|
| `LLM_BASE_URL` | `http://127.0.0.1:8317/v1` | `hotspot_extractor`：`https://api.deepseek.com/v1`<br>`comment_funnel`：`http://127.0.0.1:8317/v1` | — | ⚠️ 两处默认值**不一致**，缺键时行为分叉 |
| `LLM_MODEL_MAIN` | `deepseek-v4.1-flash` | `deepseek-chat` | ✅ 存在 | ✅ 当前可用 |
| `LLM_MODEL_FAST` | `deepseek-v4.1-flash` | **`gemini-3.8-flash`** | ❌ **不存在** | 🔴 **QA#2 复现路径**：`.env` 缺 `LLM_MODEL_FAST` 即回落到已失效模型 |

**网关实测**（真实 HTTP 调用）：
```
GET http://127.0.0.1:8317/v1/models   → 200，2 个模型
  - opencode-go/deepseek-v4.1-flash
  - deepseek-v4.1-flash
```
> 未带 Key 时返回 `401`，说明网关进程存活且鉴权生效。

**处置**：默认值一律删除，改为缺失即报错（**T4.2 去硬编码** + **T1.9 fail-fast**）；`LLM_MODEL_FAST` 的 `gemini-3.8-flash` 默认值是**必须立即删除的死模型引用**。

---

## 表 3 · 定时任务（`~/.hermes/profiles/*/cron/jobs.json`）

| Profile | Job | 触发 | `no_agent` | 模型 pin | 风险 |
|---|---|---|---|---|---|
| `guanjia` | `guoguo_daily_course_reminder` | cron | `False`（**依赖 LLM**） | ❌ **UNPINNED** | 🔴 QA#1：配置漂移即被引擎拒跑 |
| `xiao-he` | `daily-token-digest` | cron | `False`（**依赖 LLM**） | ❌ **UNPINNED** | 🔴 同上 |
| `xiao-hua` | `monitor_gemini4_availability` | interval | `True`（纯脚本） | — | ✅ 无模型依赖 |
| `xiao-kai` | `cache-hit-watch` | interval | `True` | — | ✅ |
| `xiao-pin` | `tech-trends-and-breaking-2h` | cron | `True` | — | ✅ |
| `xiao-pin` | `social_comment_guard_unified` | cron | `True` | — | ✅ |
| `xiao-pin` | `tech-trends-and-breaking-night-closi…` | cron | `True` | — | ✅ |

**核对结论**：7 个 job **全部未 pin 模型**；其中 **2 个依赖 LLM** 的 job 存在「全局模型漂移 → 静默拒跑」风险（QA#1 原样复现条件仍在）→ 由 **T3.4** 落地（pin 或 `no_agent` 化）。

---

## 表 4 · 环境开关

| 开关 | 实测值 | 判定 |
|---|---|---|
| `platform_policies.yaml` → `xiaohongshu.freeze_until` | `2026-11-06` | ✅ **已到期 3 天**（今日 11-09），`status: active_readonly`，冷冻期实际已解除但**配置未清理** |
| `xiaohongshu.interaction_policy.reply_mode` | `manual_approval`，`allow_direct_api_reply: false` | ✅ 与「严禁脚本直接回复」一致 |
| CDP 调试端口 `127.0.0.1:9222` | **LISTEN**（`Google`，PID 97845） | ✅ 已启动，可被 `xiaohongshu_cdp` 复用 |
| `REMOTION_DIR` | 代码零引用 | 🔴 死键（T1.9 处置） |
| Remotion 浏览器 | 系统 Chrome，`bootstrap.py --dry-run` 20/20 PASS | ✅ T0.4 已解除（system-fallback） |

---

## 盘点总结（4 条必改项）

| # | 问题 | 证据 | 承接任务 |
|---|---|---|---|
| 1 | 4 处调用点自定/缺 `max_tokens`，零超时 | 表 1 | **T1.4 / T1.5** |
| 2 | `LLM_MODEL_FAST` 默认值指向已失效的 `gemini-3.8-flash` | 表 2 | **T4.2**（+ T1.9 fail-fast） |
| 3 | 2 个 LLM 依赖 job 未 pin 模型 | 表 3 | **T3.4** |
| 4 | 冷冻期已到期但配置未清理；`REMOTION_DIR` 死键 | 表 4 | **T1.9 / T2.5** |

> 本文件为 T0.2 交付物，后续 T1.4 标定 token 预算时以表 1 为基准逐项回填实测值。
