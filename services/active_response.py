#!/usr/bin/env python3
"""
Active Response — безопасное выполнение опасных операций.
Требует подтверждения и логирует всё.
"""

import logging
from typing import Optional, Dict, Any
from enum import Enum
from datetime import datetime

from config.settings import get_config
from services.audit_service import get_audit

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


ACTIVE_RESPONSE_TOOLS = {
    "wazuh_block_ip": {"risk": RiskLevel.LOW, "reversible": True},
    "wazuh_isolate_host": {"risk": RiskLevel.MEDIUM, "reversible": True},
    "wazuh_kill_process": {"risk": RiskLevel.MEDIUM, "reversible": False},
    "wazuh_disable_user": {"risk": RiskLevel.HIGH, "reversible": True},
    "wazuh_active_response": {"risk": RiskLevel.HIGH, "reversible": False},
    "wazuh_restart_service": {"risk": RiskLevel.CRITICAL, "reversible": True},
    "wazuh_restart": {"risk": RiskLevel.CRITICAL, "reversible": False},
}

# Простая in-memory очередь подтверждений
_pending_confirmations: Dict[str, Dict[str, Any]] = {}


def requires_confirmation(tool_name: str) -> bool:
    """Нужно ли подтверждение для инструмента"""
    cfg = get_config().security
    return cfg.active_response_require_confirmation


def requires_2fa(tool_name: str) -> bool:
    """Нужна ли 2FA (только для высокорисковых)"""
    info = ACTIVE_RESPONSE_TOOLS.get(tool_name, {"risk": RiskLevel.LOW})
    cfg = get_config().security
    if not cfg.active_response_require_2fa:
        return False
    return info["risk"] in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def create_confirmation(tool_name: str, parameters: dict, ip: str = "") -> str:
    """Создать запрос на подтверждение. Возвращает token."""
    import uuid
    token = str(uuid.uuid4())[:8]
    _pending_confirmations[token] = {
        "tool": tool_name,
        "parameters": parameters,
        "ip": ip,
        "created_at": datetime.utcnow().isoformat(),
        "confirmed": False,
    }
    logger.info(f"🔐 Создано подтверждение {token} для {tool_name}")
    return token


def confirm_action(token: str, ip: str = "") -> bool:
    """Подтвердить действие по token"""
    pending = _pending_confirmations.get(token)
    if not pending:
        return False
    if pending["ip"] and pending["ip"] != ip:
        return False
    pending["confirmed"] = True
    logger.info(f"✅ Подтверждено действие {token}")
    return True


def get_rollback_action(tool_name: str, parameters: dict) -> Optional[dict]:
    """
    Получить rollback действие для инструмента.
    Возвращает словарь с tool и parameters для отката, или None.
    """
    info = ACTIVE_RESPONSE_TOOLS.get(tool_name)
    if not info or not info["reversible"]:
        return None

    rollback_map = {
        "wazuh_block_ip": {"tool": "wazuh_unblock_ip", "params": parameters},
        "wazuh_isolate_host": {"tool": "wazuh_restore_host", "params": parameters},
        "wazuh_disable_user": {"tool": "wazuh_enable_user", "params": parameters},
        "wazuh_restart_service": {"tool": "wazuh_restore_service", "params": parameters},
    }

    mapped = rollback_map.get(tool_name)
    if mapped:
        return {"tool": mapped["tool"], "parameters": mapped["params"]}
    return None


async def execute_with_safety(
    tool_name: str,
    parameters: dict,
    mcp_client,
    ip: str = "",
) -> dict:
    """
    Безопасное выполнение active response.
    1. Проверяет риск
    2. Требует подтверждение (если нужно)
    3. Выполняет
    4. Создаёт запись в audit
    5. Готовит rollback
    """
    audit = get_audit()
    info = ACTIVE_RESPONSE_TOOLS.get(tool_name, {"risk": RiskLevel.LOW})

    # Проверка подтверждения
    if requires_confirmation(tool_name):
        token = create_confirmation(tool_name, parameters, ip)
        return {
            "status": "requires_confirmation",
            "message": f"Требуется подтверждение для {tool_name} (уровень риска: {info['risk']})",
            "confirmation_token": token,
            "risk_level": info["risk"].value,
            "rollback": get_rollback_action(tool_name, parameters),
        }

    # Выполняем
    try:
        result = await mcp_client.call_tool(tool_name, parameters)
        success = "error" not in result

        audit.log(
            action=f"active_response:{tool_name}",
            query=str(parameters),
            tools_used=[tool_name],
            success=success,
            error=result.get("error") if not success else None,
            ip_address=ip,
        )

        result["rollback"] = get_rollback_action(tool_name, parameters)
        result["risk_level"] = info["risk"].value

        if not success:
            # Автоматический rollback при ошибке (если обратимо)
            rollback = get_rollback_action(tool_name, parameters)
            if rollback:
                logger.info(f"🔄 Автоматический rollback для {tool_name}")
                try:
                    await mcp_client.call_tool(rollback["tool"], rollback["parameters"])
                    result["rollback_executed"] = True
                except Exception as rb_e:
                    logger.error(f"Rollback failed: {rb_e}")
                    result["rollback_executed"] = False

        return result

    except Exception as e:
        audit.log(
            action=f"active_response:{tool_name}",
            query=str(parameters),
            tools_used=[tool_name],
            success=False,
            error=str(e),
            ip_address=ip,
        )
        return {"error": str(e), "risk_level": info["risk"].value}
