#!/usr/bin/env python3
"""
Кастомные исключения для MCPClient и SOC Agent.
"""

from typing import Optional


class SOCAgentError(Exception):
    """Базовое исключение для всех ошибок агента"""
    def __init__(self, message: str, code: str = "internal_error", details: Optional[dict] = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


class MCPConnectionError(SOCAgentError):
    """Ошибка подключения к MCP-серверу"""
    def __init__(self, server_url: str, cause: Optional[Exception] = None):
        super().__init__(
            message=f"Не удалось подключиться к MCP серверу {server_url}: {cause}",
            code="mcp_connection_error",
            details={"server_url": server_url, "cause": str(cause) if cause else None},
        )


class MCPToolNotFoundError(SOCAgentError):
    """Инструмент не найден на MCP-сервере"""
    def __init__(self, tool_name: str, server_name: str):
        super().__init__(
            message=f"Инструмент '{tool_name}' не найден на сервере {server_name}",
            code="mcp_tool_not_found",
            details={"tool_name": tool_name, "server_name": server_name},
        )


class MCPToolCallError(SOCAgentError):
    """Ошибка при вызове инструмента MCP"""
    def __init__(self, tool_name: str, http_status: int, error_text: str):
        super().__init__(
            message=f"Ошибка при вызове инструмента '{tool_name}': HTTP {http_status} — {error_text[:200]}",
            code="mcp_tool_call_error",
            details={"tool_name": tool_name, "http_status": http_status, "error_text": error_text[:500]},
        )


class MCPTimeoutError(SOCAgentError):
    """Таймаут при вызове MCP"""
    def __init__(self, tool_name: str, timeout_seconds: float):
        super().__init__(
            message=f"Таймаут ({timeout_seconds}с) при вызове инструмента '{tool_name}'",
            code="mcp_timeout",
            details={"tool_name": tool_name, "timeout_seconds": timeout_seconds},
        )


class LLMAnalysisError(SOCAgentError):
    """Ошибка анализа через LLM"""
    def __init__(self, cause: Optional[Exception] = None):
        super().__init__(
            message=f"Ошибка LLM анализа: {cause}",
            code="llm_analysis_error",
            details={"cause": str(cause) if cause else None},
        )


class QueryValidationError(SOCAgentError):
    """Ошибка валидации запроса пользователя"""
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="query_validation_error",
        )


class RateLimitExceededError(SOCAgentError):
    """Превышен лимит запросов"""
    def __init__(self, retry_after_seconds: float):
        super().__init__(
            message=f"Превышен лимит запросов. Повторите через {retry_after_seconds:.0f}с",
            code="rate_limit_exceeded",
            details={"retry_after_seconds": retry_after_seconds},
        )
