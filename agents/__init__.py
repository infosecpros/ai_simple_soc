"""
SOC AI Agent v10 — Мульти-агентная архитектура.

Агенты:
- TriageAgent — быстрая классификация
- InvestigatorAgent — глубокий анализ
- ResponderAgent — действия с подтверждением
- ReporterAgent — генерация отчётов
- Orchestrator — маршрутизация запросов
"""

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from agents.triage_agent import TriageAgent
from agents.investigator_agent import InvestigatorAgent
from agents.responder_agent import ResponderAgent, ApprovalRequest
from agents.reporter_agent import ReporterAgent
from agents.orchestrator import Orchestrator

__all__ = [
    "BaseAgent",
    "AgentContext",
    "AgentResult",
    "TriageAgent",
    "InvestigatorAgent",
    "ResponderAgent",
    "ApprovalRequest",
    "ReporterAgent",
    "Orchestrator",
]
