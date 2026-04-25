#!/usr/bin/env python3
"""
Triage Agent — быстрая классификация и приоритизация.
Первый агент в цепочке, работает без LLM для скорости.
"""

from typing import Dict, Any, List, Optional
import json
from datetime import datetime, timedelta

import structlog

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from llm_agent import AnalysisResult, ToolExecutionPlan, IntentType
from config.settings import get_config

logger = structlog.get_logger()


class TriageAgent(BaseAgent):
    """
    TriageAgent — быстрая классификация алертов и запросов.
    
    Специализация:
    - Быстрое определение намерения (keyword-based, без LLM)
    - Приоритизация алертов по severity
    - False-positive detection
    - Маршрутизация к нужному специализированному агенту
    
    Fast path: не требует LLM для большинства запросов.
    """
    
    # Маппинг intent → ключевые слова
    INTENT_KEYWORDS = {
        "alert_triage": [
            "алерт", "alert", "событие", "инцидент", "sigid", "siganat",
            "проверь алерт", "критический","критич","событие","событий","security", "wazuh", "security",
        ],
        "agent_status": [
            "агент", "agent", "статус", "активн", "запущен",
            "количество агент", "сколько агент",
        ],
        "general_query": [
            "инструмент", "что ты умеешь", "возможност", "помощь",
            "help", "привет", "здравствуй", "кто ты",
        ],
    }
    
    # Маппинг severity ключевых слов
    SEVERITY_KEYWORDS = {
        "critical": ["critical", "критический","критич","событие","событий","security", "крит", "emergency", "15", "14"],
        "high": ["high", "высокий", "высок", "12", "13", "11", "10"],
        "medium": ["medium", "средний", "средн", "7", "8", "9"],
        "low": ["low", "низкий", "низк", "info", "инфо"],
    }

    def __init__(self):
        super().__init__(
            name="triage",
            description="Быстрая классификация и приоритизация запросов и алертов"
        )
        self._intent_tool_map = self._build_intent_tool_map()

    def _build_intent_tool_map(self) -> Dict[str, Dict]:
        """
        Строит маппинг intent → инструменты из настроек.
        Если в настройках нет — использует встроенный маппинг.
        """
        try:
            cfg = get_config()
            if hasattr(cfg, 'agent') and hasattr(cfg.agent, 'intent_tool_map'):
                return cfg.agent.intent_tool_map
        except Exception:
            pass
        
        # Встроенный маппинг (fallback)
        return {
            "alert_triage": {
                "tools": ["get_wazuh_alerts", "get_wazuh_alert_summary"],
                "description": "Триаж алертов безопасности"
            },
            "agent_status": {
                "tools": ["get_wazuh_agents", "get_wazuh_running_agents", "check_agent_health"],
                "description": "Проверка статуса агентов"
            },
            "general_query": {
                "tools": ["get_wazuh_alert_summary", "get_wazuh_statistics"],
                "description": "Общий запрос"
            },
        }

    def get_handled_intents(self) -> List[str]:
        return ["alert_triage", "agent_status", "general_query"]

    def get_required_tools(self) -> List[str]:
        tools = set()
        for cfg in self._intent_tool_map.values():
            tools.update(cfg.get("tools", []))
        return list(tools)

    def _fallback_intent(self, query: str) -> IntentType:
        """Keyword-based определение намерения"""
        q = query.lower()
        
        for intent_str, keywords in self.INTENT_KEYWORDS.items():
            if any(kw in q for kw in keywords):
                return IntentType(intent_str)
        
        return IntentType.GENERAL_QUERY

    def _detect_severity(self, query: str) -> str:
        """Определение ожидаемого severity из запроса"""
        q = query.lower()
        for severity, keywords in self.SEVERITY_KEYWORDS.items():
            if any(kw in q for kw in keywords):
                return severity
        return "medium"

    def _generate_plan(self, analysis: AnalysisResult) -> ToolExecutionPlan:
        """
        Генерация плана без LLM.
        Жёсткий маппинг intent → инструменты.
        """
        intent_str = analysis.intent.value
        plan_config = self._intent_tool_map.get(
            intent_str,
            self._intent_tool_map["general_query"]
        )
        
        tool_calls: list[Dict[str, Any]] = []
        available_tools = self._context.available_tools if self._context else []
        available_names = [t.get("name", "") for t in available_tools]
        
        for tool_name in plan_config["tools"]:
            if available_names and tool_name not in available_names:
                continue
            
            params: dict[str, Any] = {
                "limit": 50,
                "severity": self._detect_severity(
                    self._context.query if self._context else ""
                ),
            }
            
            tool_calls.append({
                "tool": tool_name,
                "parameters": params,
                "order": str(len(tool_calls) + 1),
            })
        
        # Если нет инструментов — минимальный набор
        if not tool_calls:
            tool_calls = [
                {"tool": "get_wazuh_alert_summary", "parameters": {}, "order": 1},
            ]
        
        logger.info("plan_generated", tools=len(tool_calls), intent=intent_str)
        return ToolExecutionPlan(
            tool_calls=tool_calls,
            description=plan_config["description"]
        )

    async def analyze(self, query: str, context: AgentContext) -> AnalysisResult:
        """
        Анализ запроса.
        
        TriageAgent старается не вызывать LLM:
        - Быстрые запросы (приветствие, статус) — сразу keyword-based
        - Сложные запросы — через LLM
        """
        self._context = context
        
        # Пробуем быстрый анализ (без LLM)
        fast_intent = self._fallback_intent(query)
        
        if fast_intent != IntentType.GENERAL_QUERY or not context.llm_agent:
            return AnalysisResult(
                intent=fast_intent,
                confidence=0.85 if fast_intent != IntentType.GENERAL_QUERY else 0.6,
                reasoning=f"Быстрый анализ TriageAgent: {fast_intent.value}",
                suggested_tools=self._intent_tool_map.get(fast_intent.value, {}).get("tools", []),
                parameters={"severity": self._detect_severity(query)}
            )
        
        # Если быстрый не подошёл — через LLM
        if context.llm_agent:
            return await context.llm_agent.analyze_query(query)
        
        return AnalysisResult(
            intent=IntentType.GENERAL_QUERY,
            confidence=0.5,
            reasoning="Анализ TriageAgent (fallback)",
            suggested_tools=[],
            parameters={}
        )

    async def execute(self, analysis: AnalysisResult) -> List[Dict[str, Any]]:
        """
        Выполнение инструментов MCP.
        
        TriageAgent делает быстрые, короткие запросы — алерт сводки,
        статус агентов. Не делает глубоких анализов.
        """
        plan = self._generate_plan(analysis)
        results = []
        
        context = self._context
        for call in plan.tool_calls:
            tool_name = call["tool"]
            params = call["parameters"]
            
            # Пробуем каждый MCP сервер
            for server_name in ["wazuh-mcp", "own-mcp"]:
                if context is None or server_name not in context.mcp_servers:
                    continue
                    
                cb = (context.circuit_breakers or {}).get(server_name)
                mcp = (context.mcp_servers or {}).get(server_name)
                
                if cb is None or mcp is None:
                    continue
                
                try:
                    async def _call():
                        return await mcp.call_tool(tool_name, params)
                    result = await cb.call(_call)
                    results.append(result)
                    break
                except Exception as e:
                    logger.warning("tool_failed",
                        tool=tool_name, server=server_name, error=str(e))
                    continue
        
        if not results:
            results.append({"error": f"Инструмент {tool_name} недоступен", "code": "mcp_unavailable"})
        
        return results

    async def respond(self, query: str, analysis: AnalysisResult, results: List[Dict[str, Any]]) -> str:
        """
        Быстрый ответ для алертов и статусов.
        
        TriageAgent отвечает без LLM — шаблонный ответ.
        """
        intent_str = analysis.intent.value
        
        if intent_str == "agent_status":
            return self._format_agent_status(results)
        elif intent_str == "alert_triage":
            return self._format_alert_summary(results)
        else:
            return self._format_general(results)

    def _format_agent_status(self, results: List[Dict[str, Any]]) -> str:
        """Форматирование статуса агентов"""
        parts = ["📊 **Статус агентов:**"]
        
        for r in results:
            content = r.get("content", [])
            for item in content if isinstance(content, list) else [content]:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(f"\n{item['text'][:1000]}")
        
        return "\n".join(parts[:5])

    def _format_alert_summary(self, results: List[Dict[str, Any]]) -> str:
        """Форматирование сводки алертов"""
        parts = ["🔍 **Сводка алертов:**"]
        
        for r in results:
            content = r.get("content", [])
            for item in content if isinstance(content, list) else [content]:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item["text"][:1000]
                    parts.append(f"\n{text}")
        
        return "\n".join(parts[:5])

    def _format_general(self, results: List[Dict[str, Any]]) -> str:
        """Форматирование общего ответа"""
        parts = ["📋 **Результаты:**"]
        
        for r in results:
            content = r.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(f"\n{item['text'][:500]}")
        
        return "\n".join(parts[:3])
