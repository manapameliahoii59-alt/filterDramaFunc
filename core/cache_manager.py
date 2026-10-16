"""
core/cache_manager.py
本地持久化去重缓存模块 (SQLite)
对已筛选剧目建立本地缓存，避免重复请求常读接口与重复调用大模型
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "screening_cache.db"


class ScreeningCacheManager:
    """剧目初筛本地持久化缓存管理器"""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS drama_screening_cache (
                    book_name TEXT PRIMARY KEY,
                    book_id TEXT,
                    abstract TEXT,
                    episode_amount INTEGER,
                    category_text TEXT,
                    dialogue_text TEXT,
                    is_ppt INTEGER,
                    ppt_ratio REAL,
                    stage1_score REAL,
                    final_score REAL,
                    tier TEXT,
                    ai_review TEXT,
                    genre_summary TEXT,
                    diagnostic_message TEXT,
                    raw_material_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_book_id ON drama_screening_cache(book_id);")
            conn.commit()

    def get(self, book_name: str, max_age_days: int = 14) -> Optional[Dict[str, Any]]:
        """按剧名查询缓存，若超出有效期则视为未命中"""
        name_clean = book_name.strip()
        if not name_clean:
            return None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM drama_screening_cache WHERE book_name = ?;",
                (name_clean,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            data = dict(row)
            # 校验缓存过期时间
            updated_at_str = data.get("updated_at")
            if updated_at_str:
                try:
                    updated_dt = datetime.strptime(updated_at_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
                    if datetime.now() - updated_dt > timedelta(days=max_age_days):
                        return None
                except Exception:
                    pass

            return data

    def save(self, data: Dict[str, Any]):
        """持久化保存/更新剧目初筛结果"""
        book_name = (data.get("book_name") or data.get("title") or "").strip()
        if not book_name:
            return

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO drama_screening_cache (
                    book_name, book_id, abstract, episode_amount, category_text,
                    dialogue_text, is_ppt, ppt_ratio, stage1_score, final_score,
                    tier, ai_review, genre_summary, diagnostic_message, raw_material_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(book_name) DO UPDATE SET
                    book_id = excluded.book_id,
                    abstract = excluded.abstract,
                    episode_amount = excluded.episode_amount,
                    category_text = excluded.category_text,
                    dialogue_text = excluded.dialogue_text,
                    is_ppt = excluded.is_ppt,
                    ppt_ratio = excluded.ppt_ratio,
                    stage1_score = excluded.stage1_score,
                    final_score = excluded.final_score,
                    tier = excluded.tier,
                    ai_review = excluded.ai_review,
                    genre_summary = excluded.genre_summary,
                    diagnostic_message = excluded.diagnostic_message,
                    raw_material_json = excluded.raw_material_json,
                    updated_at = CURRENT_TIMESTAMP;
            """, (
                book_name,
                data.get("book_id"),
                data.get("abstract"),
                data.get("episode_amount", 0),
                data.get("category_text"),
                data.get("dialogue_text"),
                1 if data.get("is_ppt") else 0,
                float(data.get("ppt_ratio", 0.0)),
                float(data.get("stage1_score", 0.0)),
                float(data.get("final_score", 0.0)),
                data.get("tier"),
                data.get("ai_review"),
                data.get("genre_summary"),
                data.get("diagnostic_message"),
                json.dumps(data.get("raw_material", {}), ensure_ascii=False),
            ))
            conn.commit()
