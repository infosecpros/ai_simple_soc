#!/usr/bin/env python3
"""
Local Memory — Async SQLite (aiosqlite) база для долгосрочной памяти агента.
Вдохновлено Alaya: эпизодическая, семантическая и неявная память.
"""

import json
import logging
import os
import time
from typing import Optional, List, Dict

import aiosqlite

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
    In-process SQLite память для агента (async, aiosqlite).
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

        self._db_path = path
        self._db: Optional[aiosqlite.Connection] = None
        self._max_episodes = max_ep
        self._decay_rate = decay
        self._enable_forgetting = forget
        self._initialized = False

    async def initialize(self) -> None:
        """Асинхронная инициализация БД (вызывается после __init__)"""
        if self._initialized:
            return

        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        self._initialized = True
        logger.info("✅ LocalMemory (async): %s (max=%s)", self._db_path, self._max_episodes)

    async def _ensure_db(self) -> aiosqlite.Connection:
        """Гарантирует что БД инициализирована"""
        if self._db is None:
            await self.initialize()
        assert self._db is not None
        return self._db

    # ---- Эпизоды ----

    async def store_episode(self, session_id: str, role: str, content: str,
                            intent: Optional[str] = None):
        db = await self._ensure_db()
        await db.execute(
            """INSERT INTO episodes (session_id, role, content, intent, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, role, content, intent, time.time()),
        )
        await db.commit()
        await self._maybe_prune()

    async def get_session_episodes(self, session_id: str, limit: int = 20) -> List[Dict]:
        db = await self._ensure_db()
        cursor = await db.execute(
            """SELECT id, role, content, intent, created_at
               FROM episodes WHERE session_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def search_episodes(self, query: str, limit: int = 10) -> List[Dict]:
        """Поиск по эпизодам (LIKE-based, для Alaya нужна векторная БД)"""
        db = await self._ensure_db()
        pattern = f"%{query}%"
        cursor = await db.execute(
            """SELECT id, session_id, role, content, intent, created_at,
                      retrieval_strength
               FROM episodes
               WHERE content LIKE ? OR intent LIKE ?
               ORDER BY retrieval_strength DESC, created_at DESC
               LIMIT ?""",
            (pattern, pattern, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ---- Знания ----

    async def store_knowledge(self, key: str, value: str, category: str = "general",
                              confidence: float = 0.5):
        db = await self._ensure_db()
        await db.execute(
            """INSERT INTO knowledge (key, value, category, confidence, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 confidence = MAX(knowledge.confidence, excluded.confidence),
                 last_accessed = ?,
                 access_count = knowledge.access_count + 1""",
            (key.lower(), value, category, confidence, time.time(), time.time()),
        )
        await db.commit()

    async def get_knowledge(self, key: str) -> Optional[Dict]:
        db = await self._ensure_db()
        cursor = await db.execute(
            """UPDATE knowledge SET last_accessed = ?, access_count = access_count + 1
               WHERE key = ? RETURNING *""",
            (time.time(), key.lower()),
        )
        rows = list(await cursor.fetchall())
        if rows:
            return dict(rows[0])
        return None

    async def search_knowledge(self, query: str, limit: int = 10) -> List[Dict]:
        db = await self._ensure_db()
        pattern = f"%{query}%"
        cursor = await db.execute(
            """SELECT * FROM knowledge
               WHERE key LIKE ? OR value LIKE ? OR category LIKE ?
               ORDER BY confidence DESC, access_count DESC
               LIMIT ?""",
            (pattern, pattern, pattern, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def knowledge_by_category(self, category: str) -> List[Dict]:
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT * FROM knowledge WHERE category = ? ORDER BY confidence DESC",
            (category,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ---- Предпочтения ----

    async def store_preference(self, domain: str, preference: str, weight: float = 1.0):
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT id, weight FROM preferences WHERE domain = ? AND preference = ?",
            (domain, preference),
        )
        existing = await cursor.fetchone()

        if existing:
            new_weight = existing["weight"] + weight
            await db.execute(
                "UPDATE preferences SET weight = ?, updated_at = ? WHERE id = ?",
                (new_weight, time.time(), existing["id"]),
            )
        else:
            await db.execute(
                """INSERT INTO preferences (domain, preference, weight, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (domain, preference, weight, time.time(), time.time()),
            )
        await db.commit()

    async def get_preferences(self, domain: Optional[str] = None) -> List[Dict]:
        db = await self._ensure_db()
        if domain:
            cursor = await db.execute(
                "SELECT * FROM preferences WHERE domain = ? ORDER BY weight DESC",
                (domain,),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM preferences ORDER BY weight DESC LIMIT 50"
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ---- Инциденты ----

    async def store_incident(self, summary: str, description: str = "",
                             severity: str = "medium",
                             mitre_tactics: Optional[List[str]] = None,
                             indicators: Optional[List[str]] = None):
        db = await self._ensure_db()
        await db.execute(
            """INSERT INTO incidents (summary, description, severity, mitre_tactics,
               indicators, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (summary, description, severity,
             json.dumps(mitre_tactics or []),
             json.dumps(indicators or []),
             time.time()),
        )
        await db.commit()

    async def resolve_incident(self, incident_id: int, resolution: str):
        db = await self._ensure_db()
        await db.execute(
            "UPDATE incidents SET resolution = ?, resolved_at = ? WHERE id = ?",
            (resolution, time.time(), incident_id),
        )
        await db.commit()

    async def search_incidents(self, query: str, limit: int = 10) -> List[Dict]:
        db = await self._ensure_db()
        pattern = f"%{query}%"
        cursor = await db.execute(
            """SELECT * FROM incidents
               WHERE summary LIKE ? OR description LIKE ? OR mitre_tactics LIKE ? OR indicators LIKE ?
               ORDER BY CASE severity
                 WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                 WHEN 'medium' THEN 2 WHEN 'low' THEN 3
                 ELSE 4 END, created_at DESC
               LIMIT ?""",
            (pattern, pattern, pattern, pattern, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ---- Обслуживание ----

    async def _maybe_prune(self):
        """Удаление старых эпизодов"""
        db = await self._ensure_db()
        cursor = await db.execute("SELECT COUNT(*) FROM episodes")
        rows = await cursor.fetchall()
        count = rows[0][0] if rows else 0
        if count > self._max_episodes:
            await db.execute(
                """DELETE FROM episodes WHERE id IN (
                   SELECT id FROM episodes ORDER BY created_at ASC LIMIT ?
                )""",
                (count - self._max_episodes,),
            )
            await db.commit()
            logger.info("Pruned %d old episodes", count - self._max_episodes)

    async def run_forgetting(self):
        """
        Забывание по Bjork: снижение retrieval_strength для редко используемых эпизодов.
        Хранилище (storage_strength) остаётся — забывается доступ, не содержание.
        """
        if not self._enable_forgetting:
            return

        db = await self._ensure_db()
        await db.execute(
            """UPDATE episodes
               SET retrieval_strength = MAX(0.01, retrieval_strength - ?)
               WHERE created_at < ? AND retrieval_strength > 0.01""",
            (self._decay_rate, time.time() - 86400 * 7),  # > 7 дней
        )
        await db.commit()

    async def stats(self) -> Dict:
        db = await self._ensure_db()
        ep_cursor = await db.execute("SELECT COUNT(*) FROM episodes")
        kn_cursor = await db.execute("SELECT COUNT(*) FROM knowledge")
        pr_cursor = await db.execute("SELECT COUNT(*) FROM preferences")
        inc_cursor = await db.execute("SELECT COUNT(*) FROM incidents")

        ep_rows = list(await ep_cursor.fetchall())
        kn_rows = list(await kn_cursor.fetchall())
        pr_rows = list(await pr_cursor.fetchall())
        inc_rows = list(await inc_cursor.fetchall())
        return {
            "episodes": ep_rows[0][0] if ep_rows else 0,
            "knowledge": kn_rows[0][0] if kn_rows else 0,
            "preferences": pr_rows[0][0] if pr_rows else 0,
            "incidents": inc_rows[0][0] if inc_rows else 0,
            "db_path": self._db_path,
        }

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None
        self._initialized = False
        logger.info("LocalMemory closed")


_memory: Optional[LocalMemory] = None


def get_memory() -> LocalMemory:
    global _memory
    if _memory is None:
        _memory = LocalMemory()
    return _memory
