#!/usr/bin/env python3
"""
Responder Agent — действия с подтверждением.
Human-in-the-loop для всех опасных операций.
"""

from typing import Dict, Any, List, Literal
from pydantic import BaseModel, Field
from datetime import datetime

import structlog

from agents.base_agent import BaseAgent, AgentContext
from llm_agent import IntentType, AnalysisResult

logger = structlog.get_logger()


class ApprovalRequest(BaseModel):
    """Запрос на подтверждение действия"""
    id: str = Field(description="Уникальный ID запроса")
    action: str = Field(description="Описание действия")
    tool: str = Field(description="Инструмент для выполнения")
    parameters: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    risk_level: Literal["low", "medium", "high", "critical"] = Field(default="medium")
    status: Literal["pending", "approved", "rejected", "expired"] = Field(default="pending")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    expires_at: str = Field(description="Срок действия запроса")


class ResponderAgent(BaseAgent):
    """
    ResponderAgent — выполнение активных действий.
    
    Специализация:
    - Incident response (реагирование на инциденты)
    - Active response (блокировка, изоляция)
    - Всегда требует подтверждения для confidence < 0.90
    - Confidence-based auto-approve (>0.90)
    
    Безопасность — главный приоритет.
    """

    # Конфиденс-пороги (как в Vigil-SOC)
    CONFIDENCE_AUTO_APPROVE = 0.90  # >0.90 — авто
    CONFIDENCE_MANUAL_APPROVE = 0.70  # 0.70-0.89 — ручное
    CONFIDENCE_MONITOR_ONLY = 0.70  # <0.70 — только мониторинг

    TOOLS = {
        "incident_response": ["analyze_security_threat", "search_security_events",
                              "check_ioc_reputation", "wazuh_block_ip",
                              "wazuh_isolate_host"],
        "active_response": ["wazuh_block_ip", "wazuh_isolate_host"],
    }

    def __init__(self):
        super().__init__(
            name="responder",
            description="Активные действия с подтверждением (incident response)"
        )
        self._pending_approvals: Dict[str, ApprovalRequest] = {}

    def get_handled_intents(self) -> List[str]:
        return ["incident_response", "active_response"]

    def get_required_tools(self) -> List[str]:
        tools = set()
        for cfg in self.TOOLS.values():
            tools.update(cfg)
        return list(tools)

    def _fallback_intent(self, query: str) -> IntentType:
        q = query.lower()
        if any(w in q for w in ["блок", "block", "изолир", "isolat", "отключ", "disable"]):
            return IntentType.ACTIVE_RESPONSE
        if any(w in q for w in ["инцидент", "incident", "реагир", "respond"]):
            return IntentType.INCIDENT_RESPONSE
        return IntentType.GENERAL_QUERY

    async def analyze(self, query: str, context: AgentContext) -> AnalysisResult:
        """Анализ через LLM или keyword-based"""
        self._context = context
        
        fast_intent = self._fallback_intent(query)
        if fast_intent != IntentType.GENERAL_QUERY and context.llm_agent:
            return await context.llm_agent.analyze_query(query)
        
        if fast_intent != IntentType.GENERAL_QUERY:
            return AnalysisResult(
                intent=fast_intent,
                confidence=0.7,
                reasoning=f"Keyword-based: {fast_intent.value}",
                suggested_tools=self.TOOLS.get(fast_intent.value, []),
                parameters={},
                requires_confirmation=fast_intent == IntentType.ACTIVE_RESPONSE,
                risk_level="high" if fast_intent == IntentType.ACTIVE_RESPONSE else "medium"
            )
        
        if context.llm_agent:
            return await context.llm_agent.analyze_query(query)
        
        return AnalysisResult(
            intent=IntentType.GENERAL_QUERY,
            confidence=0.5,
            reasoning="Не удалось определить намерение ResponderAgent",
            suggested_tools=[],
            parameters={}
        )

    async def execute(self, analysis: AnalysisResult) -> List[Dict[str, Any]]:
        """
        Выполнение с проверкой подтверждения.
        """
        if analysis.confidence < self.CONFIDENCE_MONITOR_ONLY:
            return [{
                "warning": "Низкая уверенность. Только мониторинг, действий не требуется.",
                "confidence": analysis.confidence,
                "required_confidence": self.CONFIDENCE_MANUAL_APPROVE,
            }]
        
        if analysis.intent == IntentType.ACTIVE_RESPONSE:
            # Создаём запрос на подтверждение
            tool_name = "wazuh_block_ip"
            params = analysis.parameters or {}
            
            approval = ApprovalRequest(
                id=f"approval_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                action=f"Блокировка: {params.get('indicator', 'неизвестно')}",
                tool=tool_name,
                parameters=params,
                confidence=analysis.confidence,
                risk_level="high" if analysis.confidence < self.CONFIDENCE_AUTO_APPROVE else "medium",
                created_at=datetime.now().isoformat(),
                expires_at=(datetime.now()).isoformat(),  # 5 мин
            )
            
            # Auto-approve если confidence > 0.90
            if analysis.confidence >= self.CONFIDENCE_AUTO_APPROVE:
                approval.status = "approved"
                logger.info("auto_approved", action=approval.action,
                           confidence=analysis.confidence)
                result = await self._execute_approved(approval)
                self._pending_approvals[approval.id] = approval
                return [{"approval": approval.model_dump(), "result": result}]
            
            # Иначе — в очередь
            approval.status = "pending"
            self._pending_approvals[approval.id] = approval
            logger.info("approval_required", action=approval.action,
                       confidence=analysis.confidence)
            
            return [{"approval": approval.model_dump()}]
        
        # Для incident_response — выполняем без подтверждения (read-only)
        return await self._execute_investigation(analysis)

    async def _execute_approved(self, approval: ApprovalRequest) -> Dict[str, Any]:
        """Выполнение подтверждённого действия"""
        result = await self._call_mcp(approval.tool, approval.parameters)
        return result

    async def _execute_investigation(self, analysis: AnalysisResult) -> List[Dict[str, Any]]:
        """Read-only investigation (incident_response без active_response)"""
        results = []
        tools = self.TOOLS.get("incident_response", [])[:3]  # первые 3 инструмента
        
        for tool_name in tools:
            result = await self._call_mcp(tool_name, analysis.parameters)
            if "error" not in result:
                results.append(result)
        
        return results

    async def respond(self, query: str, analysis: AnalysisResult,
                      results: List[Dict[str, Any]]) -> str:
        """Генерация ответа с учётом подтверждения"""
        
        # Если есть запрос на подтверждение
        for r in results:
            if "approval" in r:
                approval = r["approval"]
                if approval.get("status") == "approved":
                    return (f"✅ **Действие выполнено автоматически** "
                           f"(уверенность: {approval['confidence']:.2f})\n\n"
                           f"Действие: {approval['action']}\n"
                           f"ID запроса: {approval['id']}")
                else:
                    return (f"⚠️ **Требуется подтверждение**\n\n"
                           f"Действие: {approval['action']}\n"
                           f"Уверенность: {approval['confidence']:.2f}\n\n"
                           f"Для подтверждения отправьте:\n"
                           f"`/approve {approval['id']}`\n\n"
                           f"Или ответьте 'да' на этот запрос.")
        
        # Обычный ответ
        if self._context and self._context.llm_agent:
            try:
                return await self._context.llm_agent.generate_response(
                    query=query, tool_results=results, analysis=analysis
                )
            except Exception:
                pass
        
        return self._format_simple(results, analysis)

    def _format_simple(self, results: List[Dict[str, Any]],
                       analysis: AnalysisResult) -> str:
        parts = [f"🛡️ **Результаты ({analysis.intent.value}):**"]
        for r in results:
            content = r.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(f"\n{item['text'][:500]}")
        return "\n".join(parts[:3])

    def get_pending_approvals(self) -> List[ApprovalRequest]:
        """Получить все ожидающие подтверждения"""
        return [
            a for a in self._pending_approvals.values()
            if a.status == "pending"
        ]

    async def approve_action(self, approval_id: str) -> Dict[str, Any]:
        """Подтверждение действия пользователем"""
        approval = self._pending_approvals.get(approval_id)
        if not approval:
            return {"error": f"Запрос {approval_id} не найден"}
        
        if approval.status != "pending":
            return {"error": f"Запрос уже {approval.status}"}
        
        approval.status = "approved"
        result = await self._execute_approved(approval)
        return {"approval": approval.model_dump(), "result": result}
