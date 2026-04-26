#!/usr/bin/env python3
"""
Базовый класс для всех SOC агентов.
Каждый агент — специалист в своей области (Triage, Investigator, Responder, Reporter).
"""

from abc import ABC, abstractmethod
from pydantic import BaseModel, Field

from typing import Dict, Any, List, Optional, Literal
from datetime import datetime
from dataclasses import dataclass, field

from llm_agent import AnalysisResult, IntentType
import structlog

logger = structlog.get_logger()


@dataclass
class AgentContext:
    """Контекст выполнения агента"""
    session_id: str = ""
    query: str = ""
    dialog_history: List[Dict[str, Any]] = field(default_factory=lambda: [])
    available_tools: List[Dict[str, Any]] = field(default_factory=lambda: [])
    mcp_servers: 'Dict[str, Any]' = field(default_factory=lambda: {})
    circuit_breakers: 'Dict[str, Any]' = field(default_factory=lambda: {})
    llm_agent: Optional[Any] = None
    cache: 'Dict[str, Any]' = field(default_factory=lambda: {})
    parameters: 'Dict[str, Any]' = field(default_factory=lambda: {})


class AgentResult(BaseModel):
    """Результат работы агента"""
    response: str = Field(description="Ответ пользователю")
    data: Dict[str, Any] = Field(default_factory=dict, description="Структурированные данные")
    tools_used: List[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    requires_confirmation: bool = Field(default=False)
    risk_level: Literal["low", "medium", "high", "critical"] = Field(default="low")


class BaseAgent(ABC):
    """
    Абстрактный базовый класс SOC агента.
    
    Все агенты следуют одному паттерну:
    1. analyze() — понять что нужно сделать
    2. execute() — выполнить инструменты
    3. respond() — сформировать ответ
    
    Агент может работать автономно (без LLM) — для fast path запросов,
    или с LLM — для сложного анализа.
    """

    def __init__(self, name: str, description: str):
        self._name = name
        self._description = description
        self._context: Optional[AgentContext] = None
        self._logger = logger.bind(agent=name)

    @property
    def name(self) -> str:
        """Уникальное имя агента"""
        return self._name

    @property
    def description(self) -> str:
        """Описание специализации агента"""
        return self._description

    @abstractmethod
    def get_handled_intents(self) -> List[str]:
        """
        Список IntentType, которые этот агент обрабатывает.
        Используется Orchestrator для маршрутизации.
        """
        ...

    @abstractmethod
    def get_required_tools(self) -> List[str]:
        """
        Инструменты MCP, необходимые этому агенту.
        Если инструмент недоступен — агент работает с тем что есть.
        """
        ...

    async def analyze(self, query: str, context: AgentContext) -> AnalysisResult:
        """
        Анализ запроса — определение намерения и параметров.
        
        Может использовать LLM (через llm_agent из контекста) или
        быстрый keyword-based анализ.
        
        Args:
            query: Запрос пользователя
            context: Контекст выполнения
            
        Returns:
            AnalysisResult с намерением, уверенностью, обоснованием
        """
        self._context = context
        self._logger.info("analyze", query=query[:80])

        # По умолчанию — delegate в llm_agent если есть
        if context.llm_agent:
            return await context.llm_agent.analyze_query(query)
        
        # Если LLM нет — fallback на keyword-based
        return AnalysisResult(
            intent=self._fallback_intent(query),
            confidence=0.5,
            reasoning="Анализ без LLM (keyword-based)",
            suggested_tools=[],
            parameters={}
        )

    @abstractmethod
    def _fallback_intent(self, query: str) -> IntentType:
        """
        Keyword-based определение намерения (без LLM).
        Каждый агент знает свои ключевые слова.
        """
        ...

    @abstractmethod
    async def execute(self, analysis: AnalysisResult) -> List[Dict[str, Any]]:
        """
        Выполнение инструментов MCP.
        
        Использует circuit_breaker и mcp_servers из контекста.
        Кэширует результаты (через context.cache).
        
        Args:
            analysis: Результат анализа (содержит план инструментов)
            
        Returns:
            Список результатов вызовов инструментов
        """
        ...

    @abstractmethod
    async def respond(self, query: str, analysis: AnalysisResult, results: List[Dict[str, Any]]) -> str:
        """
        Генерация ответа пользователю.
        
        Может использовать LLM для формирования красивого ответа
        или простой шаблон для fast path.
        
        Args:
            query: Исходный запрос
            analysis: Результат анализа
            results: Результаты инструментов
            
        Returns:
            Текстовый ответ пользователю
        """
        ...

    async def run(self, query: str, context: AgentContext) -> AgentResult:
        """
        Полный цикл работы агента: analyze → execute → respond.
        
        Args:
            query: Запрос пользователя
            context: Контекст выполнения
            
        Returns:
            AgentResult с ответом и метаданными
        """
        start = datetime.now()
        
        # 1. Анализ
        analysis = await self.analyze(query, context)
        
        # 2. Выполнение
        results = await self.execute(analysis)
        
        # 3. Ответ
        response = await self.respond(query, analysis, results)
        
        # Берём имена инструментов из анализа (если есть план в suggested_tools)
        tools_used = analysis.suggested_tools or []
        
        elapsed = (datetime.now() - start).total_seconds()
        self._logger.info("run_complete",
            elapsed_seconds=elapsed,
            tools_used=len(tools_used),
            confidence=analysis.confidence
        )
        
        return AgentResult(
            response=response,
            data={"analysis": analysis.model_dump(), "results": results},
            tools_used=tools_used,
            confidence=analysis.confidence,
            requires_confirmation=analysis.requires_confirmation,
            risk_level=analysis.risk_level
        )
