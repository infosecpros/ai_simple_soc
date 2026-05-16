#!/usr/bin/env python3
"""
Orchestrator — маршрутизирует запросы к нужным агентам.

Заменяет старый монолитный SOCAgentV3.process_query().
Координирует цепочку: Triage → Investigator/Responder/Reporter.
"""

from typing import Dict, Any, List
import time

import structlog

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from memory.local_memory import get_memory
from agents.triage_agent import TriageAgent
from agents.investigator_agent import InvestigatorAgent
from agents.responder_agent import ResponderAgent, ApprovalRequest
from agents.reporter_agent import ReporterAgent

logger = structlog.get_logger()


class Orchestrator:
    """
    Оркестратор запросов к специализированным агентам.
    
    1. Всегда начинаем с TriageAgent (быстрое определение намерения)
    2. Если TriageAgent не уверен (< 0.7) — подключаем LLM
    3. Маршрутизируем к нужному агенту:
       - alert_triage, agent_status, general_query → TriageAgent
       - threat_hunting, vuln, ioc, hardening → InvestigatorAgent
       - incident_response, active_response → ResponderAgent
       - report, compliance → ReporterAgent
    4. Обрабатываем подтверждения (approval workflow)
    """

    # Пороги уверенности
    CONFIDENCE_USE_LLM = 0.7  # если ниже — уточняем через LLM
    CONFIDENCE_DIRECT_ROUTE = 0.85  # если выше — сразу роутим

    def __init__(self, llm_agent=None, mcp_servers=None, circuit_breakers=None):
        self._llm_agent = llm_agent
        self._mcp_servers = mcp_servers or {}
        self._circuit_breakers = circuit_breakers or {}
        
        # Память
        self._memory = get_memory()
        
        # Агенты
        self._triage = TriageAgent()
        self._investigator = InvestigatorAgent()
        self._responder = ResponderAgent()
        self._reporter = ReporterAgent()
        
        self._agents: Dict[str, BaseAgent] = {
            "triage": self._triage,
            "investigator": self._investigator,
            "responder": self._responder,
            "reporter": self._reporter,
        }
        
        # Маппинг intent → агент
        self._intent_router: Dict[str, str] = {}
        for name, agent in self._agents.items():
            for intent in agent.get_handled_intents():
                self._intent_router[intent] = name
        
        self._logger = logger.bind(component="orchestrator")

    def get_agent_for_intent(self, intent: str) -> BaseAgent:
        """Маршрутизация intent → агент"""
        agent_name = self._intent_router.get(intent, "triage")
        return self._agents[agent_name]

    async def route_query(self, query: str, context: AgentContext) -> AgentResult:
        """
        Основной метод: маршрутизация запроса к агенту.
        
        1. TriageAgent.analyze() — быстрое определение
        2. Если уверенность низкая (< 0.7) — LLM уточнение
        3. Маршрутизация к целевому агенту
        4. Выполнение и ответ
        """
        start = time.time()
        
        # Убеждаемся что memory в контексте
        if context.memory is None:
            context.memory = self._memory
        
        # Шаг 1: Triage — быстрое определение намерения
        self._logger.info("routing_start", query=query[:80])
        triage_result = await self._triage.analyze(query, context)
        
        # Шаг 2: если неуверен — LLM уточнение
        if triage_result.confidence < self.CONFIDENCE_USE_LLM and context.llm_agent:
            self._logger.info("using_llm_for_intent",
                            confidence=triage_result.confidence,
                            intent=triage_result.intent.value)
            llm_result = await context.llm_agent.analyze_query(query)
            # Берём LLM только если он увереннее
            if llm_result.confidence > triage_result.confidence:
                analysis = llm_result
            else:
                analysis = triage_result
        else:
            analysis = triage_result
        
        # Шаг 3: Маршрутизация
        target_agent = self.get_agent_for_intent(analysis.intent.value)
        self._logger.info("routed_to_agent",
                         agent=target_agent.name,
                         intent=analysis.intent.value,
                         confidence=analysis.confidence)
        
        # Шаг 4: Выполнение
        result = await target_agent.run(query, context)
        
        elapsed = time.time() - start
        self._logger.info("route_complete",
                         agent=target_agent.name,
                         elapsed_seconds=round(elapsed, 2))
        
        return result

    def get_pending_approvals(self) -> List[ApprovalRequest]:
        """Получить все ожидающие подтверждения"""
        return self._responder.get_pending_approvals()

    async def approve_action(self, approval_id: str) -> Dict[str, Any]:
        """Подтверждение действия"""
        return await self._responder.approve_action(approval_id)

    def get_available_agents(self) -> List[Dict[str, str]]:
        """Список доступных агентов"""
        return [
            {"name": a.name, "description": a.description}
            for a in self._agents.values()
        ]
