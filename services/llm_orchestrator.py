#!/usr/bin/env python3
"""
LLM Orchestrator — мультиагентная оркестрация.
Вдохновлено CrewAI: специализированные агенты с ролями.
"""

import logging
from typing import Dict, Any, List

from memory.local_memory import get_memory

logger = logging.getLogger(__name__)


class AgentProfile:
    """Профиль специализированного агента"""
    def __init__(self, role: str, goal: str, backstory: str, tools: List[str]):
        self.role = role
        self.goal = goal
        self.backstory = backstory
        self.tools = tools

    def render_system_prompt(self) -> str:
        return f"""Role: {self.role}
Goal: {self.goal}
Backstory: {self.backstory}
Available tools: {', '.join(self.tools)}"""


# Специализированные профили
AGENT_PROFILES = {
    "triage": AgentProfile(
        role="Senior SOC Triage Analyst",
        goal="Quickly classify and prioritize security alerts",
        backstory="15 years experience in alert triage. Specializes in separating true positives from false positives.",
        tools=["get_wazuh_alerts", "get_wazuh_alert_summary", "analyze_alert_patterns"],
    ),
    "hunter": AgentProfile(
        role="Threat Hunter",
        goal="Proactively search for hidden threats and APT activity",
        backstory="Former military cyber operator. Expert in finding advanced persistent threats.",
        tools=["search_security_events", "analyze_security_threat", "get_top_security_threats", "check_ioc_reputation"],
    ),
    "vulnerability": AgentProfile(
        role="Vulnerability Assessment Expert",
        goal="Identify and prioritize vulnerabilities in the environment",
        backstory="Certified penetration tester with expertise in vulnerability management.",
        tools=["get_wazuh_critical_vulnerabilities", "get_wazuh_vulnerabilities", "vulnerability_summary"],
    ),
    "incident_responder": AgentProfile(
        role="Incident Response Commander",
        goal="Coordinate response to security incidents with minimal impact",
        backstory="Led incident response teams for Fortune 500 companies. Expert in containment and eradication.",
        tools=["analyze_security_threat", "search_security_events", "check_ioc_reputation",
               "wazuh_block_ip", "wazuh_isolate_host", "wazuh_kill_process"],
    ),
    "compliance": AgentProfile(
        role="Compliance Auditor",
        goal="Ensure compliance with security standards and regulations",
        backstory="Certified auditor with expertise in PCI-DSS, HIPAA, SOC2, and NIST frameworks.",
        tools=["run_compliance_check", "perform_risk_assessment", "check_agent_health"],
    ),
    "general": AgentProfile(
        role="Security Operations Assistant",
        goal="Help SOC analysts with general security operations tasks",
        backstory="Versatile security analyst with broad knowledge of security operations.",
        tools=["get_wazuh_alert_summary", "get_wazuh_statistics", "get_wazuh_agents"],
    ),
}


class LLMOrchestrator:
    """
    Оркестратор — выбирает профиль агента под задачу и делегирует выполнение.
    """

    def __init__(self, llm_agent):
        self._llm = llm_agent
        self._memory = get_memory()
        logger.info("LLMOrchestrator initialized")

    def select_agent(self, intent: str) -> AgentProfile:
        """Выбор агента под намерение"""
        intent_to_agent = {
            "alert_triage": "triage",
            "threat_hunting": "hunter",
            "vulnerability_assessment": "vulnerability",
            "hardening_assessment": "vulnerability",
            "incident_response": "incident_responder",
            "active_response": "incident_responder",
            "compliance_check": "compliance",
            "ioc_check": "hunter",
        }
        profile_key = intent_to_agent.get(intent, "general")
        profile = AGENT_PROFILES.get(profile_key, AGENT_PROFILES["general"])

        logger.info(f"Selected agent '{profile_key}' for intent '{intent}'")
        return profile

    async def analyze_with_context(
        self, query: str, agent: AgentProfile, context: str = ""
    ) -> Dict[str, Any]:
        """Анализ запроса с контекстом роли агента"""
        enhanced_query = f"""[{agent.role}]
{agent.backstory}

User request: {query}

Previous context:
{context}

Analyze this request and respond with your expert analysis."""

        memory_context = await self._search_relevant(query)
        if memory_context:
            enhanced_query += f"\n\nRelevant past knowledge:\n{memory_context}"

        result = await self._llm.analyze_query(enhanced_query)

        # Сохраняем в память
        await self._memory.store_episode(
            session_id="llm_orchestrator",
            role="assistant",
            content=result.reasoning,
            intent=result.intent.value,
        )

        return {
            "analysis": result,
            "agent_profile": agent,
            "memory_context_used": bool(memory_context),
        }

    async def _search_relevant(self, query: str) -> str:
        """Поиск релевантной информации в памяти"""
        try:
            episodes = await self._memory.search_episodes(query, limit=3)
            knowledge = await self._memory.search_knowledge(query, limit=3)
            incidents = await self._memory.search_incidents(query, limit=2)

            parts = []

            if episodes:
                ep_text = "\n".join(
                    f"[{e.get('intent', 'unknown')}] {e.get('content', '')[:200]}"
                    for e in episodes
                )
                parts.append(f"Past episodes:\n{ep_text}")

            if knowledge:
                kn_text = "\n".join(
                    f"{k['key']}: {k['value']}" for k in knowledge
                )
                parts.append(f"Known facts:\n{kn_text}")

            if incidents:
                inc_text = "\n".join(
                    f"[{i.get('severity', '?')}] {i['summary']}" for i in incidents
                )
                parts.append(f"Related incidents:\n{inc_text}")

            return "\n\n".join(parts)

        except Exception as e:
            logger.error(f"Memory search failed: {e}")
            return ""
