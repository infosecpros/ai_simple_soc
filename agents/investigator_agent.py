#!/usr/bin/env python3
"""
Investigator Agent — глубокий анализ и сбор evidence.
Использует LLM для сложных запросов, работает async.
"""

from typing import Dict, Any, List, Optional
import json

import structlog

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from llm_agent import AnalysisResult, ToolExecutionPlan, IntentType
from config.settings import get_config

logger = structlog.get_logger()


class InvestigatorAgent(BaseAgent):
    """
    InvestigatorAgent — глубокий анализ угроз и инцидентов.
    
    Специализация:
    - Threat hunting (проактивный поиск)
    - Анализ уязвимостей (CVE, vulnerability assessment)
    - IOC проверка и корреляция
    - Сбор evidence и timeline reconstruction
    
    Всегда использует LLM для анализа. Если LLM недоступен — отказывает,
    не делает fallback на keyword-based (сложная логика).
    """

    # Маппинг intent → инструменты
    INTENT_TOOLS = {
        "threat_hunting": {
            "tools": ["search_security_events", "analyze_security_threat",
                      "get_top_security_threats", "perform_risk_assessment"],
            "params": {"time_range": "24h", "limit": 20},
        },
        "vulnerability_assessment": {
            "tools": ["get_wazuh_critical_vulnerabilities",
                      "get_wazuh_vulnerabilities", "vulnerability_summary"],
            "params": {"limit": 20, "compact": True},
        },
        "hardening_assessment": {
            "tools": ["perform_risk_assessment", "check_agent_health",
                      "get_agent_configuration"],
            "params": {},
        },
        "ioc_check": {
            "tools": ["check_ioc_reputation", "search_security_events",
                      "analyze_security_threat"],
            "params": {},
        },
        "compliance_check": {
            "tools": ["run_compliance_check", "perform_risk_assessment"],
            "params": {"framework": "PCI-DSS"},
        },
    }

    def __init__(self):
        super().__init__(
            name="investigator",
            description="Глубокий анализ угроз, уязвимостей и IOC"
        )

    def get_handled_intents(self) -> List[str]:
        return list(self.INTENT_TOOLS.keys())

    def get_required_tools(self) -> List[str]:
        tools = set()
        for cfg in self.INTENT_TOOLS.values():
            tools.update(cfg.get("tools", []))
        return list(tools)

    def _fallback_intent(self, query: str) -> IntentType:
        """
        InvestigatorAgent не делает keyword-based fallback.
        Сложные запросы требуют LLM.
        """
        return IntentType.GENERAL_QUERY

    async def analyze(self, query: str, context: AgentContext) -> AnalysisResult:
        """
        Анализ через LLM. Если LLM нет — честно отказываем.
        """
        self._context = context
        
        if not context.llm_agent:
            return AnalysisResult(
                intent=IntentType.GENERAL_QUERY,
                confidence=0.3,
                reasoning="InvestigatorAgent требует LLM для анализа. "
                          "Без LLM недоступен.",
                suggested_tools=[],
                parameters={}
            )
        
        return await context.llm_agent.analyze_query(query)

    async def execute(self, analysis: AnalysisResult) -> List[Dict[str, Any]]:
        """
        Выполнение инструментов для глубокого анализа.
        """
        if analysis.confidence < 0.4:
            logger.warning("low_confidence", confidence=analysis.confidence)
            return [{"warning": "Низкая уверенность в намерении, анализ не выполнен"}]
        
        intent_str = analysis.intent.value
        plan_config = self.INTENT_TOOLS.get(
            intent_str,
            {"tools": ["search_security_events"], "params": {}}
        )
        
        results = []
        available_tools = self._context.available_tools if self._context else []
        available_names = [t.get("name", "") for t in available_tools]
        
        for tool_name in plan_config["tools"]:
            if available_names and tool_name not in available_names:
                logger.debug("tool_not_available", tool=tool_name)
                continue
            
            params = {**plan_config["params"]}
            
            for server_name in ["wazuh-mcp", "own-mcp"]:
                if server_name not in (self._context.mcp_servers or {}):
                    continue
                    
                cb = (self._context.circuit_breakers or {}).get(server_name)
                mcp = (self._context.mcp_servers or {}).get(server_name)
                
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
        
        return results

    async def respond(self, query: str, analysis: AnalysisResult,
                      results: List[Dict[str, Any]]) -> str:
        """
        Генерация ответа через LLM.
        Если LLM нет — простой шаблон.
        """
        if self._context and self._context.llm_agent:
            try:
                return await self._context.llm_agent.generate_response(
                    query=query,
                    tool_results=results,
                    analysis=analysis
                )
            except Exception as e:
                logger.error("llm_response_failed", error=str(e))
        
        return self._format_response(results, analysis)

    def _format_response(self, results: List[Dict[str, Any]],
                         analysis: AnalysisResult) -> str:
        """Форматирование без LLM (упрощённый шаблон)"""
        parts = [f"🔬 **Результаты анализа ({analysis.intent.value}):**"]
        
        for r in results:
            content = r.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(f"\n{item['text'][:1000]}")
        
        return "\n".join(parts[:5])
