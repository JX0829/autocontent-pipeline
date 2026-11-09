import sqlite3
import os
import json
from datetime import datetime

class PipelineDB:
    def __init__(self, db_path="./data/pipeline_state.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.init_db()

    def get_conn(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS content_tasks (
                task_id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                entity_key TEXT NOT NULL UNIQUE,
                theme TEXT NOT NULL,
                target_account_id TEXT NOT NULL,
                status TEXT NOT NULL,
                draft_script_json TEXT,
                aligned_render_json TEXT,
                video_path TEXT,
                cover_path TEXT,
                xhs_cards_dir TEXT,
                platform_draft_id TEXT,
                published_video_id TEXT,
                fact_summary_150 TEXT,
                resource_keyword TEXT,
                has_seed_comment INTEGER DEFAULT 0,
                seed_comment_text TEXT,
                seed_comment_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            # 兼容性迁移：检查字段是否存在，若无则动态补充
            cur.execute("PRAGMA table_info(content_tasks);")
            columns = [col[1] for col in cur.fetchall()]
            if "has_seed_comment" not in columns:
                cur.execute("ALTER TABLE content_tasks ADD COLUMN has_seed_comment INTEGER DEFAULT 0;")
            if "seed_comment_text" not in columns:
                cur.execute("ALTER TABLE content_tasks ADD COLUMN seed_comment_text TEXT;")
            if "seed_comment_at" not in columns:
                cur.execute("ALTER TABLE content_tasks ADD COLUMN seed_comment_at DATETIME;")
            if "video_landscape_path" not in columns:
                cur.execute("ALTER TABLE content_tasks ADD COLUMN video_landscape_path TEXT;")
            if "cover_landscape_path" not in columns:
                cur.execute("ALTER TABLE content_tasks ADD COLUMN cover_landscape_path TEXT;")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS blacklisted_entities (
                entity_key TEXT PRIMARY KEY,
                reason TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS comment_interactions (
                comment_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                published_video_id TEXT NOT NULL,
                raw_comment TEXT NOT NULL,
                sanitized_comment TEXT NOT NULL,
                classification TEXT NOT NULL,
                reply_content TEXT,
                status TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES content_tasks(task_id)
            );
            """)
            cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_comment_task_id ON comment_interactions(task_id);
            """)
            # T5.1：外部确认凭据（平台返回的 message_id / 子楼层 ID）。
            # 没有它就无法区分"生成好待发"和"真的发出去了"，REPLIED 只能靠猜。
            cols = {r[1] for r in cur.execute("PRAGMA table_info(comment_interactions)")}
            for col, decl in (("external_msg_id", "TEXT"),
                              ("replied_at", "DATETIME")):
                if col not in cols:
                    cur.execute(f"ALTER TABLE comment_interactions ADD COLUMN {col} {decl}")

            # ---------------- T2.2 四表（spec §4.3）----------------
            cur.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id       TEXT PRIMARY KEY,
                task_id      TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                account      TEXT NOT NULL,
                status       TEXT NOT NULL,
                run_root     TEXT NOT NULL,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_run_content ON pipeline_runs(content_hash, account);
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                task_id      TEXT NOT NULL,
                kind         TEXT NOT NULL,          -- vertical | landscape | cover | xhs
                path         TEXT NOT NULL,
                sha256       TEXT NOT NULL,
                frame_hash   TEXT,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (task_id, kind)
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS send_ledger (
                dedupe_key    TEXT PRIMARY KEY,
                task_id       TEXT NOT NULL,
                channel       TEXT NOT NULL,
                chat_id       TEXT NOT NULL,
                video_path    TEXT NOT NULL,
                feishu_msg_id TEXT,
                sent_at       DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS degraded_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     TEXT NOT NULL,
                step_id     TEXT NOT NULL,
                hook_id     TEXT,
                kind        TEXT NOT NULL,          -- empty_result | truncated | skipped | fallback
                detail      TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # v1.3 增补：pipeline_runs 是 run 级表（§4.3 无 step_id），
            # 断点续跑需要步骤级进度 → 用 steps_json 存 {step_id: status}，不新增第 5 张表。
            cur.execute("PRAGMA table_info(pipeline_runs);")
            run_cols = [col[1] for col in cur.fetchall()]
            if "steps_json" not in run_cols:
                cur.execute("ALTER TABLE pipeline_runs ADD COLUMN steps_json TEXT;")
            if "updated_at" not in run_cols:
                cur.execute("ALTER TABLE pipeline_runs ADD COLUMN updated_at DATETIME;")

            conn.commit()

    def create_task(self, task_id, source_url, entity_key, theme, target_account_id,
                    draft_script, status="INGESTED"):
        """落库新任务，返回是否发生冲突（True = 已存在同 entity_key/task_id，本次未写入）。

        T4.3 状态机归位：
          · 状态默认 `INGESTED`（物料刚进来），不再一上来就写 `SCRIPTED`
            —— 之前脚本还没生成就标 SCRIPTED，状态机无法表达"扩写中/扩写失败"
          · `INSERT OR REPLACE` 改为 `ON CONFLICT DO NOTHING`
            —— REPLACE 会**静默覆盖**已有任务（连带把已渲染产物、发布记录冲掉），
               冲突必须显式返回给调用方决定，不能由数据库替人做主
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO content_tasks
            (task_id, source_url, entity_key, theme, target_account_id, status, draft_script_json, fact_summary_150, resource_keyword, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT DO NOTHING
            """, (
                task_id,
                source_url,
                entity_key,
                theme,
                target_account_id,
                status,
                json.dumps(draft_script, ensure_ascii=False),
                draft_script.get("fact_summary_150", ""),
                draft_script.get("resource_keyword", "")
            ))
            conn.commit()
            return cur.rowcount == 0

    def mark_comment_replied(self, comment_id: str, external_msg_id: str) -> bool:
        """T5.1：只有拿到平台侧回执（message_id / 子楼层 ID）才允许把状态推进到 REPLIED。

        返回 True = 已确认；False = 缺回执，状态保持 QUEUED（拒绝无凭据的"已回复"）。
        """
        if not external_msg_id:
            return False
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE comment_interactions SET status='REPLIED', external_msg_id=?, "
                "replied_at=CURRENT_TIMESTAMP WHERE comment_id=?",
                (external_msg_id, comment_id))
            conn.commit()
            return cur.rowcount > 0

    def comment_already_handled(self, task_id: str, raw_comment: str) -> bool:
        """T5.1 判重：归一化后比对，且 **IGNORED 也参与判重**。

        原实现只看未回复的评论，导致已判为 IGNORED 的 C/D 类每轮重复进模型。
        """
        from src.utils.normalize import normalize_text

        target = normalize_text(raw_comment)
        with self.get_conn() as conn:
            rows = conn.execute(
                "select raw_comment from comment_interactions where task_id=?", (task_id,)
            ).fetchall()
        return any(normalize_text(r[0] or "") == target for r in rows)

    def update_task_status(self, task_id, status, **kwargs):
        with self.get_conn() as conn:
            cur = conn.cursor()
            updates = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
            vals = [status]
            for k, v in kwargs.items():
                updates.append(f"{k} = ?")
                vals.append(v)
            vals.append(task_id)
            cur.execute(f"UPDATE content_tasks SET {', '.join(updates)} WHERE task_id = ?", vals)
            conn.commit()

    def get_task(self, task_id):
        with self.get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM content_tasks WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    # ================================================================
    # T2.2：四表读写（spec §4.3）
    # ================================================================

    def claim_send_slot(self, dedupe_key, task_id, channel, chat_id, video_path):
        """抢占发送位。返回 True 表示「本次抢到」= 首次发送；False = 已发过（幂等闸门）。

        实现必须依赖主键冲突（INSERT ... ON CONFLICT DO NOTHING），
        不能先 SELECT 再 INSERT —— 那在并发下会双发。
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO send_ledger (dedupe_key, task_id, channel, chat_id, video_path)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(dedupe_key) DO NOTHING""",
                (dedupe_key, task_id, channel, chat_id, video_path),
            )
            conn.commit()
            return cur.rowcount == 1

    def mark_sent(self, dedupe_key, feishu_msg_id):
        """回填外部消息 ID（发送成功后确认用）。"""
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE send_ledger SET feishu_msg_id = ? WHERE dedupe_key = ?",
                (feishu_msg_id, dedupe_key),
            )
            conn.commit()

    def record_artifact(self, task_id, kind, path, sha256, frame_hash=None):
        """登记产物（同 (task_id, kind) 覆盖更新，保留最新一次）。"""
        with self.get_conn() as conn:
            conn.execute(
                """INSERT INTO artifacts (task_id, kind, path, sha256, frame_hash)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(task_id, kind) DO UPDATE SET
                     path=excluded.path, sha256=excluded.sha256, frame_hash=excluded.frame_hash""",
                (task_id, kind, str(path), sha256, frame_hash),
            )
            conn.commit()

    def find_run_by_content(self, content_hash, account):
        """按内容指纹查历史 run（uq_run_content 保证 (content_hash, account) 唯一）。"""
        with self.get_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM pipeline_runs WHERE content_hash = ? AND account = ?",
                (content_hash, account),
            ).fetchone()
            return dict(row) if row else None

    def log_degraded(self, task_id, step_id, hook_id, kind, detail):
        """降级留痕。kind: empty_result | truncated | skipped | fallback。"""
        with self.get_conn() as conn:
            conn.execute(
                """INSERT INTO degraded_events (task_id, step_id, hook_id, kind, detail)
                   VALUES (?, ?, ?, ?, ?)""",
                (task_id, step_id, hook_id,
                 kind, detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)),
            )
            conn.commit()

    # ---------------------------------------------------------------- run 生命周期

    def upsert_run(self, run_id, task_id, content_hash, account, run_root, status="RUNNING"):
        """建/更新 run。同 (content_hash, account) 已有 run 时复用其 run_id（不重复建）。"""
        existing = self.find_run_by_content(content_hash, account)
        if existing and existing["run_id"] != run_id:
            return existing["run_id"]
        with self.get_conn() as conn:
            conn.execute(
                """INSERT INTO pipeline_runs
                       (run_id, task_id, content_hash, account, status, run_root, steps_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT steps_json FROM pipeline_runs WHERE run_id = ?), '{}'),
                           CURRENT_TIMESTAMP)
                   ON CONFLICT(run_id) DO UPDATE SET
                     status=excluded.status, run_root=excluded.run_root, updated_at=CURRENT_TIMESTAMP""",
                (run_id, task_id, content_hash, account, status, str(run_root), run_id),
            )
            conn.commit()
        return run_id

    def set_step_status(self, run_id, step_id, status):
        """写步骤级进度到 steps_json（断点续跑依据）。"""
        with self.get_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT steps_json FROM pipeline_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return False
            try:
                steps = json.loads(row["steps_json"] or "{}")
            except json.JSONDecodeError:
                steps = {}
            steps[step_id] = status
            conn.execute(
                "UPDATE pipeline_runs SET steps_json = ?, updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
                (json.dumps(steps, ensure_ascii=False), run_id),
            )
            conn.commit()
            return True

    def update_run_status(self, run_id, status):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
                (status, run_id),
            )
            conn.commit()

    def get_run(self, run_id):
        with self.get_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["steps"] = json.loads(data.get("steps_json") or "{}")
            except json.JSONDecodeError:
                data["steps"] = {}
            return data
