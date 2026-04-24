#!/usr/bin/env python3
"""
Audit Service — логирование всех действий агента.
"""

import json
import logging
import os
from typing import Optional
from datetime import datetime, timezone

from config.settings import get_config

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self):
        cfg = get_config().security
        self._enabled = cfg.audit_log_enabled
        path = os.path.expanduser(cfg.audit_log_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._file = path

    def log(
        self,
        action: str,
        agent_id: str = "soc_agent_v10",
        query: str = "",
        intent: Optional[str] = None,
        tools_used: Optional[list] = None,
        success: bool = True,
        error: Optional[str] = None,
        ip_address: Optional[str] = None,
        duration_ms: float = 0.0,
    ):
        if not self._enabled:
            return

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "agent_id": agent_id,
            "query": query[:200] if query else "",
            "intent": intent,
            "tools_used": tools_used or [],
            "success": success,
            "error": error,
            "ip_address": ip_address,
            "duration_ms": round(duration_ms, 2),
        }

        try:
            with open(self._file, "a") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Audit write failed: {e}")


_audit: Optional[AuditService] = None


def get_audit() -> AuditService:
    global _audit
    if _audit is None:
        _audit = AuditService()
    return _audit
