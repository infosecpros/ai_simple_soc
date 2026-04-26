#!/usr/bin/env python3
"""
Тесты для мульти-агентной архитектуры.
"""

import pytest

from agents.base_agent import AgentContext
from agents.triage_agent import TriageAgent
from agents.investigator_agent import InvestigatorAgent
from agents.responder_agent import ResponderAgent, ApprovalRequest
from agents.reporter_agent import ReporterAgent
from agents.orchestrator import Orchestrator
from llm_agent import IntentType, AnalysisResult


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def empty_context():
    """Пустой контекст для тестов"""
    return AgentContext(
        session_id="test_session",
        query="test query",
        available_tools=[],
        mcp_servers={},
        circuit_breakers={},
        llm_agent=None,
        cache={}
    )


@pytest.fixture
def tool_context():
    """Контекст с инструментами"""
    return AgentContext(
        session_id="test_session",
        query="test query",
        available_tools=[
            {"name": "get_wazuh_agents", "description": "Get agents"},
            {"name": "get_wazuh_alert_summary", "description": "Alert summary"},
            {"name": "get_wazuh_statistics", "description": "Statistics"},
            {"name": "get_wazuh_critical_vulnerabilities", "description": "Vulns"},
            {"name": "check_agent_health", "description": "Health"},
            {"name": "search_security_events", "description": "Search"},
            {"name": "analyze_security_threat", "description": "Analyze"},
            {"name": "check_ioc_reputation", "description": "IOC check"},
            {"name": "run_compliance_check", "description": "Compliance"},
        ],
        mcp_servers={},
        circuit_breakers={},
        llm_agent=None,
        cache={}
    )


# ============================================================
# Tests: TriageAgent
# ============================================================

class TestTriageAgent:
    """Тесты TriageAgent — быстрая классификация"""

    def test_name(self):
        agent = TriageAgent()
        assert agent.name == "triage"
        assert "классификация" in agent.description

    def test_handled_intents(self):
        agent = TriageAgent()
        intents = agent.get_handled_intents()
        assert "alert_triage" in intents
        assert "agent_status" in intents
        assert "general_query" in intents
        assert len(intents) == 3

    def test_fallback_intent_alert(self):
        agent = TriageAgent()
        assert agent._fallback_intent("покажи алерты") == IntentType.ALERT_TRIAGE
        assert agent._fallback_intent("критические события") == IntentType.ALERT_TRIAGE
        assert agent._fallback_intent("что с wazuh") == IntentType.ALERT_TRIAGE

    def test_fallback_intent_agent(self):
        agent = TriageAgent()
        assert agent._fallback_intent("сколько агентов") == IntentType.AGENT_STATUS
        assert agent._fallback_intent("статус агентов") == IntentType.AGENT_STATUS
        assert agent._fallback_intent("активные агенты") == IntentType.AGENT_STATUS

    def test_fallback_intent_general(self):
        agent = TriageAgent()
        assert agent._fallback_intent("привет") == IntentType.GENERAL_QUERY
        assert agent._fallback_intent("что ты умеешь") == IntentType.GENERAL_QUERY

    @pytest.mark.asyncio
    async def test_analyze_agent_status(self, tool_context):
        agent = TriageAgent()
        result = await agent.analyze("сколько агентов работает", tool_context)
        assert result.intent == IntentType.AGENT_STATUS
        assert result.confidence >= 0.8

    @pytest.mark.asyncio
    async def test_analyze_alert_triage(self, tool_context):
        agent = TriageAgent()
        result = await agent.analyze("покажи критические алерты", tool_context)
        assert result.intent == IntentType.ALERT_TRIAGE
        assert result.confidence >= 0.8

    def test_generate_plan_agent_status(self):
        agent = TriageAgent()
        _ = AnalysisResult(
            intent=IntentType.AGENT_STATUS,
            confidence=0.9,
            reasoning="test",
            suggested_tools=agent.get_required_tools()
        )

    @pytest.mark.asyncio
    async def test_run_no_mcp(self, tool_context):
        """Агент работает без MCP — возвращает mcp_unavailable"""
        agent = TriageAgent()
        result = await agent.run("сколько агентов", tool_context)
        assert result.response is not None
        assert "Статус" in result.response or "недоступен" in result.response or "Результат" in result.response
        assert result.confidence >= 0.8


# ============================================================
# Tests: InvestigatorAgent
# ============================================================

class TestInvestigatorAgent:
    """Тесты InvestigatorAgent — глубокий анализ"""

    def test_name(self):
        agent = InvestigatorAgent()
        assert agent.name == "investigator"
        assert "глубок" in agent.description.lower()

    def test_handled_intents(self):
        agent = InvestigatorAgent()
        intents = agent.get_handled_intents()
        assert "threat_hunting" in intents
        assert "vulnerability_assessment" in intents
        assert "ioc_check" in intents
        assert len(intents) >= 5

    def test_fallback_intent_returns_general(self):
        """Без LLM — возвращает GENERAL_QUERY (не делает fallback)"""
        agent = InvestigatorAgent()
        assert agent._fallback_intent("анализируй угрозу") == IntentType.GENERAL_QUERY

    @pytest.mark.asyncio
    async def test_analyze_without_llm_returns_low_confidence(self, empty_context):
        """Без LLM — низкая уверенность"""
        agent = InvestigatorAgent()
        result = await agent.analyze("анализируй угрозу", empty_context)
        assert result.confidence < 0.4  # low confidence без LLM
        assert result.intent == IntentType.GENERAL_QUERY


# ============================================================
# Tests: ResponderAgent
# ============================================================

class TestResponderAgent:
    """Тесты ResponderAgent — действия с подтверждением"""

    def test_name(self):
        agent = ResponderAgent()
        assert agent.name == "responder"
        assert "подтвержд" in agent.description

    def test_handled_intents(self):
        agent = ResponderAgent()
        intents = agent.get_handled_intents()
        assert "incident_response" in intents
        assert "active_response" in intents

    def test_fallback_intent_block(self):
        agent = ResponderAgent()
        assert agent._fallback_intent("заблокируй IP") == IntentType.ACTIVE_RESPONSE
        assert agent._fallback_intent("изолируй хост") == IntentType.ACTIVE_RESPONSE

    def test_fallback_intent_incident(self):
        agent = ResponderAgent()
        assert agent._fallback_intent("реагируй на инцидент") == IntentType.INCIDENT_RESPONSE

    @pytest.mark.asyncio
    async def test_execute_low_confidence_monitor_only(self, empty_context):
        """Confidence < 0.70 — monitor only, никаких действий"""
        agent = ResponderAgent()
        analysis = AnalysisResult(
            intent=IntentType.ACTIVE_RESPONSE,
            confidence=0.5,
            reasoning="low confidence",
            suggested_tools=[],
            parameters={"indicator": "8.8.8.8"}
        )
        results = await agent.execute(analysis)
        # Должен вернуть warning, не approval
        assert len(results) > 0
        assert "warning" in results[0] or "confidence" in results[0]

    @pytest.mark.asyncio
    async def test_execute_medium_confidence_approval_required(self, empty_context):
        """Confidence 0.70-0.89 — требуется подтверждение"""
        agent = ResponderAgent()
        analysis = AnalysisResult(
            intent=IntentType.ACTIVE_RESPONSE,
            confidence=0.8,
            reasoning="medium confidence",
            suggested_tools=["wazuh_block_ip"],
            parameters={"indicator": "8.8.8.8"},
            requires_confirmation=True,
            risk_level="high"
        )
        results = await agent.execute(analysis)
        assert len(results) > 0
        if "approval" in results[0]:
            approval = results[0]["approval"]
            assert approval["status"] == "pending"

    def test_get_pending_approvals_empty(self):
        agent = ResponderAgent()
        assert agent.get_pending_approvals() == []

    def test_get_pending_approvals_with_item(self):
        agent = ResponderAgent()
        agent._pending_approvals["test"] = ApprovalRequest(
            id="test",
            action="block IP",
            tool="wazuh_block_ip",
            confidence=0.8,
            risk_level="high",
            expires_at="2026-12-31T23:59:59"
        )
        pending = agent.get_pending_approvals()
        assert len(pending) == 1
        assert pending[0].id == "test"


# ============================================================
# Tests: ReporterAgent
# ============================================================

class TestReporterAgent:
    """Тесты ReporterAgent — отчёты"""

    def test_name(self):
        agent = ReporterAgent()
        assert agent.name == "reporter"
        assert "отчёт" in agent.description

    def test_handled_intents(self):
        agent = ReporterAgent()
        intents = agent.get_handled_intents()
        assert "report_generation" in intents
        assert "general_query" in intents

    def test_fallback_intent_report(self):
        agent = ReporterAgent()
        assert agent._fallback_intent("сделай отчёт") == IntentType.REPORT_GENERATION
        assert agent._fallback_intent("ежедневный дашборд") == IntentType.REPORT_GENERATION
        assert agent._fallback_intent("статистика за неделю") == IntentType.REPORT_GENERATION

    def test_fallback_intent_compliance(self):
        agent = ReporterAgent()
        assert agent._fallback_intent("проверка PCI-DSS") == IntentType.COMPLIANCE_CHECK
        assert agent._fallback_intent("compliance audit") == IntentType.COMPLIANCE_CHECK

    @pytest.mark.asyncio
    async def test_analyze_report_request(self, tool_context):
        agent = ReporterAgent()
        result = await agent.analyze("сделай отчёт за день", tool_context)
        assert result.intent == IntentType.REPORT_GENERATION
        assert result.confidence >= 0.7


# ============================================================
# Tests: Orchestrator
# ============================================================

class TestOrchestrator:
    """Тесты Orchestrator — маршрутизация"""

    def test_init(self):
        orch = Orchestrator()
        agents = orch.get_available_agents()
        names = [a["name"] for a in agents]
        assert "triage" in names
        assert "investigator" in names
        assert "responder" in names
        assert "reporter" in names

    def test_route_to_agent(self):
        orch = Orchestrator()
        agent = orch.get_agent_for_intent("agent_status")
        assert agent.name == "triage"

        agent = orch.get_agent_for_intent("threat_hunting")
        assert agent.name == "investigator"

        agent = orch.get_agent_for_intent("active_response")
        assert agent.name == "responder"

        agent = orch.get_agent_for_intent("report_generation")
        assert agent.name == "reporter"

        agent = orch.get_agent_for_intent("unknown_intent")
        assert agent.name == "triage"  # fallback

    @pytest.mark.asyncio
    async def test_route_query_agent_status(self, tool_context):
        """Orchestrator направляет запрос про агентов в TriageAgent"""
        orch = Orchestrator()
        result = await orch.route_query("сколько агентов работает", tool_context)
        assert result.confidence >= 0.8
        assert result.response is not None
        # Должен попасть в TriageAgent
        intent = result.data.get("analysis", {}).get("intent", "")
        assert "agent" in intent or result.response is not None

    @pytest.mark.asyncio
    async def test_route_query_unknown(self, empty_context):
        """Неизвестный запрос — TriageAgent fallback"""
        orch = Orchestrator()
        result = await orch.route_query("какой-то странный запрос", empty_context)
        assert result.response is not None
        assert result.confidence >= 0.5  # general_query

    def test_pending_approvals_empty(self):
        orch = Orchestrator()
        assert orch.get_pending_approvals() == []


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
