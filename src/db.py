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
            conn.commit()

    def create_task(self, task_id, source_url, entity_key, theme, target_account_id, draft_script):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT OR REPLACE INTO content_tasks 
            (task_id, source_url, entity_key, theme, target_account_id, status, draft_script_json, fact_summary_150, resource_keyword, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                task_id,
                source_url,
                entity_key,
                theme,
                target_account_id,
                "SCRIPTED",
                json.dumps(draft_script, ensure_ascii=False),
                draft_script.get("fact_summary_150", ""),
                draft_script.get("resource_keyword", "")
            ))
            conn.commit()

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
