#!/usr/bin/env python3
"""
Pydantic модели для ответов API.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime

from pydantic import BaseModel, Field


class ToolInfo(BaseModel):
    name: str
    description: str = ""
    server: str = ""
    risk_level: str = "low"


class ToolsResponse(BaseModel):
    tools: List[ToolInfo]
    total: int


class QueryResponse(BaseModel):
    query: str
    response: str
    intent: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    tools_used: List[str] = Field(default_factory=list)
    session_id: str = ""
    duration_ms: float = 0.0


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "soc-ai-agent-v10"
    version: str = "0.10.0"
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    llm_available: bool = False
    mcp_connected: bool = False
    memory_engine: str = "none"
    circuit_breakers: List[Dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: str
    code: str = "internal_error"
    details: Optional[str] = None
