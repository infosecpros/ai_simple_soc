#!/usr/bin/env python3
"""
Reporter Agent — генерация отчётов и документации.
Использует LLM для красивого форматирования, без LLM — шаблоны.
"""

from typing import Dict, Any, List, Optional
import json
from datetime import datetime

import structlog

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from llm_agent import AnalysisResult, IntentType

logger = structlog.get_logger()


class ReporterAgent(BaseAgent):
    """
    ReporterAgent — генерация отчётов и сводок.
    
    Специализация:
    - Report generation (ежедневные/еженедельные отчёты)
    - Compliance отчёты (PCI-DSS, NIST)
    - Executive summary для руководства
    - Technical summary для аналитиков
    
    Prefers LLM, но может работать и без него с шаблонными ответами.
    """

    INTENT_TOOLS = {
        "report_generation": {
            "tools": ["generate_security_report", "get_wazuh_statistics",
                      "get_wazuh_alerts"],
            "params": {"report_type": "summary"},
        },
        "compliance_check": {
            "tools": ["run_compliance_check", "perform_risk_assessment"],
            "params": {"framework": "PCI-DSS"},
        },
        "general_query": {
            "tools": ["get_wazuh_alert_summary", "get_wazuh_statistics"],
            "params": {},
        },
    }

    def __init__(self):
        super().__init__(
            name="reporter",
            description="Генерация отчётов и сводок безопасности"
        )

    def get_handled_intents(self) -> List[str]:
        return list(self.INTENT_TOOLS.keys())

    def get_required_tools(self) -> List[str]:
        tools = set()
        for cfg in self.INTENT_TOOLS.values():
            tools.update(cfg.get("tools", []))
        return list(tools)

    def _fallback_intent(self, query: str) -> IntentType:
        q = query.lower()
        if any(w in q for w in ["отчет", "report", "дашборд", "dashboard",
                                 "сводк", "summary", "статистик", "stat"]):
            return IntentType.REPORT_GENERATION
        if any(w in q for w in ["комплаенс", "compliance", "pci", "gdpr",
                                 "nist", "стандарт", "audit"]):
            return IntentType.COMPLIANCE_CHECK
        return IntentType.GENERAL_QUERY

    async def analyze(self, query: str, context: AgentContext) -> AnalysisResult:
        """Анализ — быстрый keyword-based или через LLM"""
        self._context = context
        
        fast_intent = self._fallback_intent(query)
        if fast_intent != IntentType.GENERAL_QUERY:
            return AnalysisResult(
                intent=fast_intent,
                confidence=0.8,
                reasoning=f"ReporterAgent keyword-based: {fast_intent.value}",
                suggested_tools=self.INTENT_TOOLS.get(fast_intent.value, {}).get("tools", []),
                parameters=self.INTENT_TOOLS.get(fast_intent.value, {}).get("params", {})
            )
        
        if context.llm_agent:
            return await context.llm_agent.analyze_query(query)
        
        return AnalysisResult(
            intent=IntentType.GENERAL_QUERY,
            confidence=0.5,
            reasoning="ReporterAgent fallback",
            suggested_tools=self.INTENT_TOOLS["general_query"]["tools"],
            parameters={}
        )

    async def execute(self, analysis: AnalysisResult) -> List[Dict[str, Any]]:
        """Выполнение отчётных инструментов"""
        intent_str = analysis.intent.value
        plan_config = self.INTENT_TOOLS.get(
            intent_str,
            self.INTENT_TOOLS["general_query"]
        )
        
        results = []
        for tool_name in plan_config["tools"]:
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
                                 tool=tool_name, error=str(e))
                    continue
        
        return results

    async def respond(self, query: str, analysis: AnalysisResult,
                      results: List[Dict[str, Any]]) -> str:
        """Генерация ответа — через LLM или шаблон"""
        if self._context and self._context.llm_agent:
            try:
                return await self._context.llm_agent.generate_response(
                    query=query, tool_results=results, analysis=analysis
                )
            except Exception:
                pass
        
        return self._format_report(results, analysis)

    def _format_report(self, results: List[Dict[str, Any]],
                       analysis: AnalysisResult) -> str:
        """Форматирование отчёта без LLM"""
        parts = [
            f"📋 **Отчёт: {analysis.intent.value}**",
            f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            ""
        ]
        
        for r in results:
            content = r.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(f"\n{item['text'][:1500]}")
        
        return "\n".join(parts[:10])
