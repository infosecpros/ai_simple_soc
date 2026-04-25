#!/usr/bin/env python3
"""
LLM Cache — кэш для результатов LLM, чтобы не делать повторные вызовы.
"""

import logging
from typing import Any, Optional, Dict

from config.settings import get_config

logger = logging.getLogger(__name__)


class LLMCache:
    """Кэш для результатов LLM"""

    def __init__(self, max_size: int = 50):
        self._cache: Dict[str, Any] = {}
        self._max_size = max_size

    def _make_key(self, query: str, context: str = "") -> str:
        return f"{query.strip().lower()}|{context}"

    def get(self, query: str, context: str = "") -> Optional[Any]:
        return self._cache.get(self._make_key(query, context))

    def set(self, query: str, value: Any, context: str = ""):
        key = self._make_key(query, context)
        if len(self._cache) >= self._max_size:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[key] = value

    def clear(self):
        self._cache.clear()


class DialogContext:
    """Контекст всего диалога"""

    def __init__(self, session_id: str = ""):
        from datetime import datetime
        self.session_id = session_id or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.turns: list = []
        self.max_turns = get_config().agent.dialog_max_turns

    def add_turn(self, query: str, intent: str, response: str, tools_used: list):
        from datetime import datetime
        self.turns.append({
            "query": query,
            "intent": intent,
            "response": response[:200],
            "tools_used": tools_used,
            "timestamp": datetime.now().isoformat(),
        })
        if len(self.turns) > self.max_turns:
            self.turns.pop(0)

    def get_recent_context(self, n: int = 3) -> str:
        recent = self.turns[-n:] if len(self.turns) >= n else self.turns
        if not recent:
            return ""
        lines = []
        for t in recent:
            lines.append(f"Пользователь: {t['query']}")
            lines.append(f"Ассистент: {t['response']}")
        return "\n".join(lines)

    @property
    def last_intent(self) -> Optional[str]:
        if self.turns:
            return self.turns[-1].get("intent")
        return None
