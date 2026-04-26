#!/usr/bin/env python3
"""
LLM-интеграция для SOC AI Agent
Использует pydantic-ai для анализа запросов и принятия решений
"""

import json
import logging
import os
from typing import Dict, Any, List, Optional, Literal, TypeVar, Generic
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Generic тип для результатов LLM
T = TypeVar("T", bound=BaseModel)


class LLMContext(BaseModel, Generic[T]):
    """Контекст для LLM с типизированным результатом"""
    session_id: str = ""
    history: List[Dict[str, str]] = Field(default_factory=list)
    tools_available: List[str] = Field(default_factory=list)
    result_type: Optional[type[T]] = None

    model_config = {"arbitrary_types_allowed": True}


# ============================================================
# Модели данных для LLM
# ============================================================

class IntentType(str, Enum):
    """Типы намерений пользователя"""
    ALERT_TRIAGE = "alert_triage"
    THREAT_HUNTING = "threat_hunting"
    VULNERABILITY_ASSESSMENT = "vulnerability_assessment"
    HARDENING_ASSESSMENT = "hardening_assessment"
    INCIDENT_RESPONSE = "incident_response"
    COMPLIANCE_CHECK = "compliance_check"
    IOC_CHECK = "ioc_check"
    AGENT_STATUS = "agent_status"
    REPORT_GENERATION = "report_generation"
    GENERAL_QUERY = "general_query"
    ACTIVE_RESPONSE = "active_response"


class SecurityTool(BaseModel):
    """Инструмент безопасности"""
    name: str = Field(description="Имя инструмента")
    description: str = Field(description="Описание инструмента")
    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        default="low",
        description="Уровень риска инструмента"
    )
    reversible: bool = Field(
        default=True,
        description="Обратимо ли действие"
    )


class AnalysisResult(BaseModel):
    """Результат анализа запроса LLM"""
    intent: IntentType = Field(description="Определенное намерение пользователя")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Уверенность в определении намерения"
    )
    reasoning: str = Field(description="Обоснование выбора намерения")
    suggested_tools: List[str] = Field(
        default_factory=list,
        description="Предлагаемые инструменты для выполнения"
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Параметры для инструментов"
    )
    requires_confirmation: bool = Field(
        default=False,
        description="Требует ли подтверждения от пользователя"
    )
    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        default="low",
        description="Уровень риска запроса"
    )


class ToolExecutionPlan(BaseModel):
    """План выполнения инструментов"""
    tool_calls: List[Dict[str, Any]] = Field(
        description="Список вызовов инструментов"
    )
    description: str = Field(description="Описание плана")
    estimated_impact: str = Field(
        default="",
        description="Оценка влияния выполнения"
    )


class AlertSummary(BaseModel):
    """Сводка алертов для LLM"""
    total_alerts: int = Field(description="Общее количество алертов")
    critical_count: int = Field(description="Количество критических")
    high_count: int = Field(description="Количество высоких")
    medium_count: int = Field(description="Количество средних")
    low_count: int = Field(description="Количество низких")
    top_rules: List[str] = Field(description="Топ правил")
    time_range: str = Field(description="Временной диапазон")


# ============================================================
# Контекст для агента
# ============================================================

@dataclass
class AgentContext:
    """Контекст выполнения агента"""
    available_tools: List[Dict[str, Any]]
    conversation_history: List[Dict[str, Any]]
    session_id: str
    user_preferences: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# SOC AI Agent с LLM
# ============================================================

class SOCLLMAgent:
    """SOC AI Agent с интеграцией LLM через pydantic-ai"""
    
    def __init__(self):
        """Инициализация LLM агента"""
        # Инициализация pydantic-ai агента
        self._init_llm_agent()
        
        # Кэш инструментов
        self._tools_cache: List[Dict[str, Any]] = []
        
        # История
        self.session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.conversation_history: List[Dict[str, Any]] = []
    
    def _init_llm_agent(self):
        """Инициализация LLM агента через pydantic-ai"""
        
        # Системный промпт для SOC аналитика
        system_prompt = """Ты - опытный SOC аналитик с более чем 15-летним опытом работы.
Твоя задача - помогать пользователю (другому SOC аналитику) в анализе безопасности.

Твои компетенции:
1. **Триаж алертов** - классификация и приоритизация событий безопасности
2. **Threat Hunting** - проактивный поиск угроз
3. **Анализ уязвимостей** - оценка и приоритизация CVE
4. **Оценка безопасности** - анализ конфигураций и hardening
5. **Реагирование на инциденты** - рекомендации по контрмерам
6. **Проверка соответствия** - PCI-DSS, NIST, CIS Benchmarks
7. **Работа с IOC** - проверка индикаторов компрометации

Правила работы:
- Всегда объясняй свои решения
- Предупреждай о рисках при опасных действиях
- Если запрос неясен - запроси уточнение
- Используй предыдущий контекст для улучшения ответов
- Соблюдай принципы безопасности: сначала проверь, потом действуй

Для каждого запроса ты должен:
1. Определить намерение пользователя
2. Выбрать подходящие инструменты
3. Обосновать свое решение
4. Оценить риски
5. Предложить план действий"""

        # Настройка DeepSeek модели
        deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
        
        if deepseek_api_key:
            # Создаем провайдер и модель DeepSeek
            deepseek_provider = DeepSeekProvider(
                api_key=deepseek_api_key
            )
            model = OpenAIChatModel(
                'deepseek-chat',
                provider=deepseek_provider
            )
            logger.info("✅ Используется DeepSeek модель")
        else:
            # Используем fallback модель (без LLM)
            logger.warning("⚠️ DEEPSEEK_API_KEY не найден, будет использован fallback анализ")
            model = None
        
        # Создаем агента
        if model:
            self._agent: Optional[Agent] = Agent(
                model,
                system_prompt=system_prompt,
                tools=[]  # Инструменты регистрируются отдельно
            )
        else:
            self._agent = None
        
        # Регистрируем инструменты агента
        if self._agent:
            self._register_tools()
    
    def _register_tools(self):
        """Регистрация инструментов для LLM агента"""
        agent = self._agent
        if agent is None:
            logger.warning("LLM агент не инициализирован, инструменты не зарегистрированы")
            return
        
        @agent.tool_plain
        async def get_available_tools() -> str:
            """Получить список доступных инструментов безопасности"""
            if not self._tools_cache:
                return "Инструменты не загружены"
            
            tools_summary = []
            for tool in self._tools_cache:
                tools_summary.append(f"- {tool['name']}: {tool.get('description', '')[:100]}")
            
            return "\n".join(tools_summary)
        
        @agent.tool_plain
        async def search_similar_incidents(query: str) -> str:
            """
            Поиск похожих инцидентов в истории
            Args:
                query: Описание инцидента для поиска
            """
            return "История инцидентов пока недоступна"
        
        @agent.tool_plain
        async def validate_tool_risk(tool_name: str, parameters: str) -> str:
            """
            Проверить риск использования инструмента
            Args:
                tool_name: Имя инструмента
                parameters: Параметры в JSON
            """
            risk_map = {
                "wazuh_block_ip": "low (обратимо)",
                "wazuh_isolate_host": "medium (обратимо)",
                "wazuh_kill_process": "medium (необратимо)",
                "wazuh_disable_user": "high (обратимо)",
                "wazuh_active_response": "high (необратимо)",
                "wazuh_restart": "critical (необратимо)"
            }
            risk = risk_map.get(tool_name, "low")
            return f"Риск инструмента {tool_name}: {risk}"
    
    def update_tools_cache(self, tools: List[Dict[str, Any]]):
        """Обновление кэша инструментов"""
        self._tools_cache = tools
        logger.info(f"✅ Обновлен кэш инструментов: {len(tools)} инструментов")
    
    async def analyze_query(
        self,
        query: str,
        context: Optional[AgentContext] = None
    ) -> AnalysisResult:
        """
        Анализ запроса пользователя через LLM
        
        Args:
            query: Запрос пользователя
            context: Контекст выполнения
            
        Returns:
            Результат анализа
        """
        try:
            # Создаем контекст если не передан
            if context is None:
                context = AgentContext(
                    available_tools=self._tools_cache,
                    conversation_history=self.conversation_history[-5:],  # Последние 5 сообщений
                    session_id=self.session_id
                )
            
            # Добавляем запрос в историю
            self.conversation_history.append({
                "role": "user",
                "query": query,
                "timestamp": datetime.now().isoformat()
            })
            
            # Запускаем агента
            if self._agent is not None:
                result = await self._agent.run(query)

                # Безопасное извлечение результата из pydantic-ai
                analysis_result = None
                result_output = getattr(result, 'output', None)
                if isinstance(result_output, AnalysisResult):
                    analysis_result = result_output

                if analysis_result is None:
                    response_text = str(result)
                    analysis_result = AnalysisResult(
                        intent=self._fallback_intent_analysis(query),
                        confidence=0.5,
                        reasoning=response_text[:500],
                        suggested_tools=[],
                        parameters={},
                        requires_confirmation=False,
                        risk_level="low",
                    )
                
                # Сохраняем результат в историю
                self.conversation_history.append({
                    "role": "assistant",
                    "intent": analysis_result.intent.value,
                    "confidence": analysis_result.confidence,
                    "reasoning": analysis_result.reasoning,
                    "timestamp": datetime.now().isoformat()
                })
                
                logger.info(
                    f"Анализ запроса: {query[:50]}... -> "
                    f"намерение: {analysis_result.intent.value} "
                    f"(уверенность: {analysis_result.confidence:.2f})"
                )
                
                return analysis_result
            else:
                # LLM не доступен, используем fallback
                raise Exception("LLM агент не инициализирован")
            
        except Exception as e:
            logger.error(f"Ошибка анализа запроса через LLM: {e}")
            
            # Fallback: возвращаем базовый анализ
            return AnalysisResult(
                intent=self._fallback_intent_analysis(query),
                confidence=0.5,
                reasoning=f"Автоматический анализ (LLM недоступен: {e})",
                suggested_tools=[],
                parameters={}
            )
    
    def _fallback_intent_analysis(self, query: str) -> IntentType:
        """
        Fallback анализ намерения (без LLM)
        
        Args:
            query: Запрос пользователя
            
        Returns:
            Тип намерения
        """
        query_lower = query.lower()
        
        if any(word in query_lower for word in ["алерт", "alert", "инцидент", "событие"]):
            return IntentType.ALERT_TRIAGE
        elif any(word in query_lower for word in ["hunt", "поиск", "угроз", "threat"]):
            return IntentType.THREAT_HUNTING
        elif any(word in query_lower for word in ["уязвим", "vuln", "cve"]):
            return IntentType.VULNERABILITY_ASSESSMENT
        elif any(word in query_lower for word in ["hardening", "безопасн", "конфиг"]):
            return IntentType.HARDENING_ASSESSMENT
        elif any(word in query_lower for word in ["отчет", "report", "статистик"]):
            return IntentType.REPORT_GENERATION
        elif any(word in query_lower for word in ["агент", "agent", "статус"]):
            return IntentType.AGENT_STATUS
        elif any(word in query_lower for word in ["блок", "block", "изолир"]):
            return IntentType.ACTIVE_RESPONSE
        elif any(word in query_lower for word in ["ioc", "индикатор", "репутац"]):
            return IntentType.IOC_CHECK
        elif any(word in query_lower for word in ["комплаенс", "compliance", "pci"]):
            return IntentType.COMPLIANCE_CHECK
        else:
            return IntentType.GENERAL_QUERY
    
    async def generate_tool_plan(
        self,
        analysis: AnalysisResult,
        query: str
    ) -> ToolExecutionPlan:
        """
        Генерация плана выполнения инструментов
        
        Args:
            analysis: Результат анализа
            query: Исходный запрос
            
        Returns:
            План выполнения
        """
        # Маппинг намерений на инструменты
        intent_tool_map = {
            IntentType.ALERT_TRIAGE: {
                "tools": ["get_wazuh_alerts", "get_wazuh_alert_summary", "analyze_alert_patterns"],
                "description": "Триаж алертов безопасности",
                "parameters": {"limit": 10, "compact": True}
            },
            IntentType.THREAT_HUNTING: {
                "tools": ["search_security_events", "analyze_security_threat", "get_top_security_threats"],
                "description": "Поиск угроз безопасности",
                "parameters": {"time_range": "24h", "limit": 20}
            },
            IntentType.VULNERABILITY_ASSESSMENT: {
                "tools": ["get_wazuh_critical_vulnerabilities", "get_wazuh_vulnerabilities", "vulnerability_summary"],
                "description": "Оценка уязвимостей",
                "parameters": {"limit": 20, "compact": True}
            },
            IntentType.HARDENING_ASSESSMENT: {
                "tools": ["perform_risk_assessment", "check_agent_health", "get_agent_configuration"],
                "description": "Оценка безопасности конфигураций",
                "parameters": {}
            },
            IntentType.INCIDENT_RESPONSE: {
                "tools": ["analyze_security_threat", "search_security_events", "check_ioc_reputation"],
                "description": "Реагирование на инцидент",
                "parameters": {"time_range": "1h"}
            },
            IntentType.COMPLIANCE_CHECK: {
                "tools": ["run_compliance_check", "perform_risk_assessment"],
                "description": "Проверка соответствия стандартам",
                "parameters": {"framework": "PCI-DSS"}
            },
            IntentType.IOC_CHECK: {
                "tools": ["check_ioc_reputation", "analyze_security_threat"],
                "description": "Проверка индикаторов компрометации",
                "parameters": {}
            },
            IntentType.AGENT_STATUS: {
                "tools": ["get_wazuh_agents", "get_wazuh_running_agents", "check_agent_health"],
                "description": "Проверка статуса агентов",
                "parameters": {"limit": 50}
            },
            IntentType.REPORT_GENERATION: {
                "tools": ["generate_security_report", "get_wazuh_statistics"],
                "description": "Генерация отчета",
                "parameters": {"report_type": "daily", "include_recommendations": True}
            },
            IntentType.ACTIVE_RESPONSE: {
                "tools": ["analyze_security_threat"],
                "description": "Подготовка к активному реагированию",
                "parameters": {}
            },
            IntentType.GENERAL_QUERY: {
                "tools": ["get_wazuh_alert_summary", "get_wazuh_statistics"],
                "description": "Общий запрос информации",
                "parameters": {"time_range": "24h"}
            }
        }
        
        # Получаем план для намерения
        plan_config = intent_tool_map.get(
            analysis.intent,
            intent_tool_map[IntentType.GENERAL_QUERY]
        )
        
        # Создаем вызовы инструментов
        tool_calls: list[Dict[str, Any]] = []
        for tool_name in plan_config["tools"]:
            # Проверяем, есть ли инструмент в кэше
            if not any(t["name"] == tool_name for t in self._tools_cache):
                continue
            
            plan_params = plan_config.get("parameters", {})
            params: dict[str, Any] = {}
            if isinstance(plan_params, dict):
                params.update(plan_params)
            
            # Добавляем специфичные параметры
            if analysis.intent == IntentType.IOC_CHECK and analysis.parameters:
                params.update(analysis.parameters)
            
            if analysis.intent == IntentType.ACTIVE_RESPONSE:
                params.update({
                    "indicator": query,
                    "indicator_type": "ip"
                })
            
            tool_calls.append({
                "tool": tool_name,
                "parameters": params,
                "order": str(len(tool_calls) + 1),
            })
        
        return ToolExecutionPlan(
            tool_calls=tool_calls,
            description=str(plan_config["description"]),
            estimated_impact=analysis.reasoning
        )
    
    async def generate_response(
        self,
        query: str,
        tool_results: List[Dict[str, Any]],
        analysis: AnalysisResult
    ) -> str:
        """
        Генерация ответа пользователю на основе результатов инструментов
        
        Args:
            query: Исходный запрос
            tool_results: Результаты выполнения инструментов
            analysis: Результат анализа
            
        Returns:
            Ответ пользователю
        """
        try:
            # Формируем контекст для LLM
            context = f"""
Запрос пользователя: {query}
Определенное намерение: {analysis.intent.value} (уверенность: {analysis.confidence:.2f})
Обоснование: {analysis.reasoning}

Результаты выполнения инструментов:
{json.dumps(tool_results, indent=2, ensure_ascii=False)[:2000]}
"""
            
            # Генерируем ответ через LLM
            if self._agent is not None:
                response = await self._agent.run(
                    f"На основе следующих данных сформируй понятный ответ для SOC аналитика:\n\n{context}",
                )
                
                response_out = getattr(response, 'output', None)
                if response_out is not None:
                    return str(response_out)
            
            return self._format_fallback_response(analysis, tool_results)
            
        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}")
            return self._format_fallback_response(analysis, tool_results)
    
    def _format_fallback_response(
        self,
        analysis: AnalysisResult,
        tool_results: List[Dict[str, Any]]
    ) -> str:
        """
        Форматирование ответа без LLM
        
        Args:
            analysis: Результат анализа
            tool_results: Результаты инструментов
            
        Returns:
            Отформатированный ответ
        """
        # Собираем все текстовые результаты
        all_texts = []
        for result in tool_results:
            content = result.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        all_texts.append(item["text"])
        
        response_parts = [f"📋 Результаты {analysis.intent.value}:"]
        
        if all_texts:
            response_parts.append("\n".join(all_texts[:3]))  # Первые 3 результата
        else:
            response_parts.append("Информация получена, но требуется дополнительный анализ.")
        
        if analysis.requires_confirmation:
            response_parts.append("\n⚠️ Требуется ваше подтверждение для выполнения действия.")
        
        return "\n\n".join(response_parts)


# ============================================================
# Тестирование
# ============================================================

async def test_llm_agent():
    """Тестирование LLM агента"""
    
    print("=" * 60)
    print("🧪 Тестирование LLM агента")
    print("=" * 60)
    
    # Создаем агента
    agent = SOCLLMAgent()
    
    # Добавляем тестовые инструменты
    agent.update_tools_cache([
        {"name": "get_wazuh_alerts", "description": "Получение алертов Wazuh"},
        {"name": "search_security_events", "description": "Поиск событий безопасности"},
        {"name": "check_ioc_reputation", "description": "Проверка репутации IOC"},
        {"name": "wazuh_isolate_host", "description": "Изоляция хоста"},
    ])
    
    # Тестовые запросы
    test_queries = [
        "Покажи последние алерты",
        "Найди подозрительную активность за последние 24 часа",
        "Проверь репутацию IP 192.168.1.100",
        "Заблокируй подозрительный IP адрес"
    ]
    
    for query in test_queries:
        print(f"\n{'='*40}")
        print(f"👤 Запрос: {query}")
        
        # Анализируем запрос
        analysis = await agent.analyze_query(query)
        
        print("🤖 Анализ:")
        print(f"  Намерение: {analysis.intent.value}")
        print(f"  Уверенность: {analysis.confidence:.2f}")
        print(f"  Обоснование: {analysis.reasoning[:200]}...")
        print(f"  Риск: {analysis.risk_level}")
        print(f"  Требует подтверждения: {analysis.requires_confirmation}")
        
        # Генерируем план
        plan = await agent.generate_tool_plan(analysis, query)
        
        print("\n📋 План выполнения:")
        print(f"  Описание: {plan.description}")
        for call in plan.tool_calls:
            print(f"  {call['order']}. {call['tool']} {call['parameters']}")
    
    print("\n" + "=" * 60)
    print("✅ Тестирование завершено")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_llm_agent())