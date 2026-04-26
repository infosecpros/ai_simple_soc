#!/usr/bin/env python3
"""
Local Memory — SQLite-база для долгосрочной памяти агента.
Вдохновлено Alaya: эпизодическая, семантическая и неявная память.
"""

import json
import logging
import os
import sqlite3
import time
from typing import Optional, List, Dict

from config.settings import get_config

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    intent TEXT,
    tokens_used INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    retrieval_strength REAL DEFAULT 1.0,
    storage_strength REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    source TEXT DEFAULT 'extracted',
    category TEXT DEFAULT 'general',
    confidence REAL DEFAULT 0.5,
    created_at REAL NOT NULL,
    last_accessed REAL,
    access_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    preference TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    description TEXT,
    severity TEXT DEFAULT 'medium',
    mitre_tactics TEXT,
    indicators TEXT,
    resolution TEXT,
    created_at REAL NOT NULL,
    resolved_at REAL
);

CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_episodes_created ON episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);
CREATE INDEX IF NOT EXISTS idx_preferences_domain ON preferences(domain);
"""


class LocalMemory:
    """
    In-process SQLite память для агента.
    - Эпизоды: история диалогов
    - Знания: извлечённые факты
    - Предпочтения: поведенческие паттерны
    - Инциденты: залогированные события безопасности
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            path = os.path.expanduser(db_path)
            max_ep = 500
            decay = 0.1
            forget = True
        else:
            cfg = get_config().memory
            path = os.path.expanduser(cfg.db_path)
            max_ep = cfg.max_episodes
            decay = cfg.decay_rate
            forget = cfg.enable_forgetting
        os.makedirs(os.path.dirname(path), exist_ok=True)

        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA_SQL)
        self._max_episodes = max_ep
        self._decay_rate = decay
        self._enable_forgetting = forget

        logger.info(f"✅ LocalMemory: {path} (max={self._max_episodes})")

    # ---- Эпизоды ----

    def store_episode(self, session_id: str, role: str, content: str,
                      intent: Optional[str] = None):
        self._db.execute(
            """INSERT INTO episodes (session_id, role, content, intent, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, role, content, intent, time.time()),
        )
        self._db.commit()
        self._maybe_prune()

    def get_session_episodes(self, session_id: str, limit: int = 20) -> List[Dict]:
        rows = self._db.execute(
            """SELECT id, role, content, intent, created_at
               FROM episodes WHERE session_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_episodes(self, query: str, limit: int = 10) -> List[Dict]:
        """Поиск по эпизодам (LIKE-based, для Alaya нужна векторная БД)"""
        pattern = f"%{query}%"
        rows = self._db.execute(
            """SELECT id, session_id, role, content, intent, created_at,
                      retrieval_strength
               FROM episodes
               WHERE content LIKE ? OR intent LIKE ?
               ORDER BY retrieval_strength DESC, created_at DESC
               LIMIT ?""",
            (pattern, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Знания ----

    def store_knowledge(self, key: str, value: str, category: str = "general",
                        confidence: float = 0.5):
        self._db.execute(
            """INSERT INTO knowledge (key, value, category, confidence, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 confidence = MAX(knowledge.confidence, excluded.confidence),
                 last_accessed = ?,
                 access_count = knowledge.access_count + 1""",
            (key.lower(), value, category, confidence, time.time(), time.time()),
        )
        self._db.commit()

    def get_knowledge(self, key: str) -> Optional[Dict]:
        row = self._db.execute(
            """UPDATE knowledge SET last_accessed = ?, access_count = access_count + 1
               WHERE key = ? RETURNING *""",
            (time.time(), key.lower()),
        ).fetchone()
        return dict(row) if row else None

    def search_knowledge(self, query: str, limit: int = 10) -> List[Dict]:
        pattern = f"%{query}%"
        rows = self._db.execute(
            """SELECT * FROM knowledge
               WHERE key LIKE ? OR value LIKE ? OR category LIKE ?
               ORDER BY confidence DESC, access_count DESC
               LIMIT ?""",
            (pattern, pattern, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def knowledge_by_category(self, category: str) -> List[Dict]:
        rows = self._db.execute(
            "SELECT * FROM knowledge WHERE category = ? ORDER BY confidence DESC",
            (category,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Предпочтения ----

    def store_preference(self, domain: str, preference: str, weight: float = 1.0):
        # upsert — проверяем существование
        existing = self._db.execute(
            "SELECT id, weight FROM preferences WHERE domain = ? AND preference = ?",
            (domain, preference),
        ).fetchone()
        if existing:
            new_weight = existing["weight"] + weight
            self._db.execute(
                "UPDATE preferences SET weight = ?, updated_at = ? WHERE id = ?",
                (new_weight, time.time(), existing["id"]),
            )
        else:
            self._db.execute(
                """INSERT INTO preferences (domain, preference, weight, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (domain, preference, weight, time.time(), time.time()),
            )
        self._db.commit()

    def get_preferences(self, domain: Optional[str] = None) -> List[Dict]:
        if domain:
            rows = self._db.execute(
                "SELECT * FROM preferences WHERE domain = ? ORDER BY weight DESC",
                (domain,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM preferences ORDER BY weight DESC LIMIT 50"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- Инциденты ----

    def store_incident(self, summary: str, description: str = "",
                       severity: str = "medium",
                       mitre_tactics: Optional[List[str]] = None,
                       indicators: Optional[List[str]] = None):
        self._db.execute(
            """INSERT INTO incidents (summary, description, severity, mitre_tactics,
               indicators, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (summary, description, severity,
             json.dumps(mitre_tactics or []),
             json.dumps(indicators or []),
             time.time()),
        )
        self._db.commit()

    def resolve_incident(self, incident_id: int, resolution: str):
        self._db.execute(
            "UPDATE incidents SET resolution = ?, resolved_at = ? WHERE id = ?",
            (resolution, time.time(), incident_id),
        )
        self._db.commit()

    def search_incidents(self, query: str, limit: int = 10) -> List[Dict]:
        pattern = f"%{query}%"
        rows = self._db.execute(
            """SELECT * FROM incidents
               WHERE summary LIKE ? OR description LIKE ? OR mitre_tactics LIKE ? OR indicators LIKE ?
               ORDER BY CASE severity
                 WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                 WHEN 'medium' THEN 2 WHEN 'low' THEN 3
                 ELSE 4 END, created_at DESC
               LIMIT ?""",
            (pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Обслуживание ----

    def _maybe_prune(self):
        """Удаление старых эпизодов"""
        count = self._db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        if count > self._max_episodes:
            self._db.execute(
                """DELETE FROM episodes WHERE id IN (
                   SELECT id FROM episodes ORDER BY created_at ASC LIMIT ?
                )""",
                (count - self._max_episodes,),
            )
            self._db.commit()
            logger.info(f"Pruned {count - self._max_episodes} old episodes")

    def run_forgetting(self):
        """
        Забывание по Bjork: снижение retrieval_strength для редко используемых эпизодов.
        Хранилище (storage_strength) остаётся — забывается доступ, не содержание.
        """
        if not self._enable_forgetting:
            return

        self._db.execute(
            """UPDATE episodes
               SET retrieval_strength = MAX(0.01, retrieval_strength - ?)
               WHERE created_at < ? AND retrieval_strength > 0.01""",
            (self._decay_rate, time.time() - 86400 * 7),  # > 7 дней
        )
        self._db.commit()

    def stats(self) -> Dict:
        return {
            "episodes": self._db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0],
            "knowledge": self._db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0],
            "preferences": self._db.execute("SELECT COUNT(*) FROM preferences").fetchone()[0],
            "incidents": self._db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],
            "db_path": get_config().memory.db_path,
        }

    def close(self):
        self._db.close()
        logger.info("LocalMemory closed")


_memory: Optional[LocalMemory] = None


def get_memory() -> LocalMemory:
    global _memory
    if _memory is None:
        _memory = LocalMemory()
    return _memory
