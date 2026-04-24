#!/usr/bin/env python3
"""
Pydantic модели для валидации входящих запросов.
"""

from typing import Optional, List, Literal, Dict, Any
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class QueryRequest(BaseModel):
    """POST /query — запрос к агенту"""
    query: str = Field(..., min_length=1, max_length=4096)
    session_id: Optional[str] = Field(None, max_length=128)

    @field_validator("query")
    @classmethod
    def strip_query(cls, v: str) -> str:
        return v.strip()

    @field_validator("query")
    @classmethod
    def check_prompt_injection(cls, v: str) -> str:
        """Базовая проверка на prompt injection"""
        dangerous = [
            "ignore previous instructions",
            "ignore all instructions",
            "forget everything",
            "you are now",
            "system prompt",
            "ignore all previous",
        ]
        lower = v.lower()
        for pattern in dangerous:
            if pattern in lower:
                raise ValueError(f"Запрос содержит потенциально опасный паттерн: '{pattern}'")
        return v


class ChatMessage(BaseModel):
    """Сообщение в чате"""
    role: Literal["user", "assistant", "system"]
    content: str = Field(..., min_length=1, max_length=16384)


class ChatRequest(BaseModel):
    """POST /chat — запрос с историей"""
    messages: List[ChatMessage] = Field(..., min_length=1)
    stream: bool = Field(False)


class ToolCallRequest(BaseModel):
    """Запрос на вызов конкретного инструмента"""
    tool: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    """Событие аудита (внутренняя модель)"""
    action: str
    agent_id: str
    query: str
    intent: Optional[str] = None
    tools_used: List[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    ip_address: Optional[str] = None
