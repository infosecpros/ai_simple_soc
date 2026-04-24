#!/usr/bin/env python3
"""
SOC AI Agent v3 - Улучшенная версия с оптимизацией запросов к LLM,
контекстом диалога, правильным выбором инструментов и кэшированием.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field

import aiohttp
from aiohttp import ClientTimeout
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

from llm_agent import (
    SOCLLMAgent, AnalysisResult, ToolExecutionPlan,
    IntentType, AgentContext
)

load_dotenv()

logger = logging.getLogger(__name__)


# ============================================================
# Модели данных
# ============================================================

@dataclass
class ConversationTurn:
    """Один поворот диалога"""
    query: str
    intent: str
    response: str
    tools_used: List[str]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class DialogContext:
    """Контекст всего диалога"""
    session_id: str
    turns: List[ConversationTurn] = field(default_factory=list)
    last_intent: Optional[str] = None
    last_parameters: Dict[str, Any] = field(default_factory=dict)

    def add_turn(self, turn: ConversationTurn):
        self.turns.append(turn)
        self.last_intent = turn.intent
        if len(self.turns) > 10:
            self.turns.pop(0)

    def get_recent_context(self, n: int = 3) -> str:
        recent = self.turns[-n:] if len(self.turns) >= n else self.turns
        lines = []
        for t in recent:
            lines.append(f"Пользователь: {t.query}")
            lines.append(f"Ассистент: {t.response[:100]}...")
        return "\n".join(lines)


# ============================================================
# Кэш вызовов LLM
# ============================================================

class LLMCache:
    """Кэш для результатов LLM, чтобы не делать повторные вызовы"""

    def __init__(self, max_size: int = 50):
        self._cache: Dict[str, Any] = {}
        self._max_size = max_size

    def _make_key(self, query: str, context: str = "") -> str:
        return f"{query.strip().lower()}|{context}"

    def get(self, query: str, context: str = "") -> Optional[Any]:
        return self._cache.get(self._make_key(query, context))

    def set(self, query: str, value: Any, context: str = ""):
        key = self._make_key(query, context)
        if len(self._cache) >= self._max_size:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[key] = value

    def clear(self):
        self._cache.clear()


# ============================================================
# MCP Клиент
# ============================================================

from services.exceptions import (
    MCPConnectionError,
    MCPToolNotFoundError,
    MCPToolCallError,
    MCPTimeoutError,
    SOCAgentError,
)


class MCPClient:
    """Клиент для подключения к MCP-серверу"""

    def __init__(self, server_url: str, name: str = "mcp"):
        self.server_url = server_url.rstrip('/')
        self.name = name
        self._session: Optional[aiohttp.ClientSession] = None
        self._tools: List[Dict[str, Any]] = []
        self._initialized = False

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Создает сессию если её нет"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def connect(self) -> bool:
        """Подключение к MCP серверу с обработкой ошибок"""
        try:
            session = await self._ensure_session()
            async with session.get(f"{self.server_url}/health", timeout=ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    logger.info(f"Подключен к MCP серверу: {self.server_url}")
                    self._initialized = True
                    await self._load_tools()
                    return True
                raise MCPConnectionError(self.server_url, Exception(f"HTTP {resp.status}"))
        except asyncio.TimeoutError:
            logger.warning(f"MCP сервер {self.server_url} не отвечает (таймаут 5с)")
        except aiohttp.ClientError as e:
            logger.warning(f"MCP сервер {self.server_url} недоступен: {e}")
        except MCPConnectionError:
            raise
        except Exception as e:
            logger.warning(f"MCP сервер {self.server_url} ошибка: {e}")
        finally:
            # Если не удалось подключиться — закрываем сессию
            if not self._initialized:
                await self.close()

        self._initialized = False
        return False

    async def _load_tools(self):
        """Загрузка списка инструментов с MCP-сервера"""
        try:
            session = await self._ensure_session()
            async with session.get(f"{self.server_url}/tools", timeout=ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._tools = data.get("tools", [])
                    logger.info(f"Загружено {len(self._tools)} инструментов")
                else:
                    logger.warning(f"MCP tools вернул HTTP {resp.status}")
        except asyncio.TimeoutError:
            logger.error(f"Таймаут загрузки инструментов с {self.server_url}")
        except aiohttp.ClientError as e:
            logger.error(f"Ошибка загрузки инструментов: {e}")
        except Exception as e:
            logger.error(f"Неизвестная ошибка загрузки инструментов: {e}")

    def get_tools_list(self) -> List[Dict[str, Any]]:
        return self._tools

    def get_tool_names(self) -> List[str]:
        return [t.get("name", "") for t in self._tools]

    async def call_tool(self, tool_name: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Вызов инструмента MCP с детальной обработкой ошибок"""
        if not self._initialized:
            raise MCPConnectionError(self.server_url, Exception("Клиент не инициализирован"))

        # Проверяем, что инструмент существует
        tool_names = self.get_tool_names()
        if tool_names and tool_name not in tool_names:
            raise MCPToolNotFoundError(tool_name, self.name)

        try:
            session = await self._ensure_session()
            payload = {
                "tool": tool_name,
                "parameters": parameters or {}
            }

            async with session.post(
                f"{self.server_url}/call",
                json=payload,
                timeout=ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    error_text = await resp.text()
                    raise MCPToolCallError(tool_name, resp.status, error_text)

        except asyncio.TimeoutError:
            raise MCPTimeoutError(tool_name, 30.0)
        except aiohttp.ClientError as e:
            raise MCPConnectionError(self.server_url, e)

    async def close(self):
        """Гарантированное закрытие сессии"""
        if self._session:
            try:
                if not self._session.closed:
                    await self._session.close()
            except Exception:
                pass
            self._session = None
        self._initialized = False


# ============================================================
# SOC AI Agent v3 - Основной класс
# ============================================================

class SOCAgentV3:
    """
    SOC AI Agent v3 - Улучшенная версия
    
    Ключевые улучшения:
    1. Кэширование LLM запросов (не делает повторные вызовы)
    2. Контекст диалога (помнит предыдущие запросы)
    3. Правильный выбор инструментов под намерение
    4. Ограничение количества вызовов LLM до 2-3
    5. Безопасность - не логирует пароли/ключи
    6. Быстрые ответы на простые запросы
    """

    def __init__(self):
        self.llm_agent = SOCLLMAgent()
        
        self.mcp_servers: Dict[str, MCPClient] = {}
        self._llm_cache = LLMCache(max_size=50)
        
        self.dialog_context = DialogContext(
            session_id=f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        
        self._tool_results_cache: Dict[str, Tuple[Any, float]] = {}
        self._cache_ttl = 30.0
        self.default_tools = []
        
        logger.info("🚀 Инициализация SOC AI Agent v3")

    async def initialize(self):
        """Инициализация агента и подключение к MCP серверам"""
        
        wazuh_url = os.getenv("WAZUH_MCP_URL", "http://127.0.0.1:3000/mcp")
        wazuh_client = MCPClient(wazuh_url, name="wazuh-mcp")
        
        if await wazuh_client.connect():
            self.mcp_servers["wazuh-mcp"] = wazuh_client
            tools = wazuh_client.get_tools_list()
            self.default_tools = tools
            self.llm_agent.update_tools_cache(tools)
            logger.info(f"✅ Подключен Wazuh-MCP: {len(tools)} инструментов")
        else:
            logger.warning("⚠️ Wazuh-MCP недоступен, инструменты не загружены")

        own_url = os.getenv("OWN_MCP_URL", "http://127.0.0.1:8000/mcp")
        own_client = MCPClient(own_url, name="own-mcp")
        
        if await own_client.connect():
            self.mcp_servers["own-mcp"] = own_client
            logger.info("✅ Подключен собственный MCP сервер")
        
        logger.info("✅ SOC AI Agent v3 готов к работе")

    async def close(self):
        """Закрытие всех соединений"""
        for name, client in self.mcp_servers.items():
            await client.close()
        self.mcp_servers.clear()
        self._tool_results_cache.clear()
        self._llm_cache.clear()
        logger.info("👋 SOC AI Agent v3 завершил работу")

    async def process_query(self, query: str) -> Dict[str, Any]:
        """
        Обработка запроса пользователя с оптимизациями
        
        1. Проверка на "да/нет" - используем контекст
        2. Проверка кэша LLM
        3. Один анализ через LLM
        4. Быстрый план (без LLM, по маппингу)
        5. Выполнение инструментов с кэшем
        6. Один вызов LLM для ответа
        """
        
        if self._is_affirmative_response(query):
            return await self._handle_affirmative()
        
        context_key = self.dialog_context.get_recent_context(1)
        cached = self._llm_cache.get(query, context_key)
        
        if cached:
            logger.info("⚡ Использован кэшированный анализ LLM")
            analysis = cached
        else:
            analysis = await self._analyze_smart(query)
            self._llm_cache.set(query, analysis, context_key)

        plan = self._generate_plan_fast(analysis, query)
        results = await self._execute_tools_with_cache(plan.tool_calls)
        response = await self._generate_quick_response(query, results, analysis)

        turn = ConversationTurn(
            query=self._sanitize_log(query),
            intent=analysis.intent.value,
            response=response,
            tools_used=[t.get("tool", "") for t in plan.tool_calls]
        )
        self.dialog_context.add_turn(turn)

        return {
            "query": query,
            "response": self._sanitize_response(response),
            "intent": analysis.intent.value,
            "confidence": analysis.confidence,
            "reasoning": analysis.reasoning,
            "tools_used": [t.get("tool", "") for t in plan.tool_calls],
            "session_id": self.dialog_context.session_id
        }
    
    # ============================================================
    # Вспомогательные методы
    # ============================================================
    
    def _is_affirmative_response(self, query: str) -> bool:
        """Проверяет, является ли запрос утвердительным ответом"""
        q = query.strip().lower()
        affirmative = ["да", "yes", "ага", "ок", "ok", "okay", "хорошо", 
                       "го", "давай", "согласен", "подтверждаю"]
        return q in affirmative and self.dialog_context.last_intent is not None
    
    async def _handle_affirmative(self) -> Dict[str, Any]:
        """Обработка утвердительного ответа - повторяем последний запрос"""
        if self.dialog_context.turns:
            last_turn = self.dialog_context.turns[-1]
            logger.info(f"↩️ Повторный запрос (утвердительный ответ): {last_turn.intent}")
            return {
                "query": last_turn.query,
                "response": f"✅ Подтверждено. Результаты по запросу уже были показаны.\n\n{last_turn.response}",
                "intent": last_turn.intent,
                "confidence": 0.95,
                "reasoning": "Повторный запрос на основе предыдущего контекста",
                "tools_used": last_turn.tools_used,
                "session_id": self.dialog_context.session_id
            }
        return {"error": "Нет предыдущего запроса"}
    
    def _sanitize_log(self, text: str) -> str:
        """Очищает текст от потенциально опасных данных (пароли, ключи)"""
        sanitized = text
        # Маскируем подозрительные паттерны
        import re
        sanitized = re.sub(r'(пароль|password|passwd|pwd)[:\s=]+[^\s,;]+', r'\1: ***', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'(api[_-]?key|apikey|token|secret)[:\s=]+[^\s,;]+', r'\1: ***', sanitized, flags=re.IGNORECASE)
        return sanitized
    
    def _sanitize_response(self, text: str) -> str:
        """Очищает ответ от потенциально опасных данных"""
        return self._sanitize_log(text)
    
    # ============================================================
    # Анализ запроса через LLM (1 вызов)
    # ============================================================
    
    async def _analyze_smart(self, query: str) -> AnalysisResult:
        """
        Анализ запроса с контекстом диалога.
        Делает ТОЛЬКО 1 вызов к LLM с полным контекстом.
        """
        logger.info(f"🔍 Анализ запроса: {self._sanitize_log(query)[:80]}...")
        
        # Добавляем контекст диалога к запросу
        context = ""
        if self.dialog_context.turns:
            last = self.dialog_context.turns[-1]
            context = f"\nПредыдущий контекст:\n- Запрос: {self._sanitize_log(last.query)}\n- Намерение: {last.intent}"
        
        enhanced_query = f"{query}{context}"
        
        try:
            analysis = await self.llm_agent.analyze_query(enhanced_query)
            logger.info(f"📊 Намерение: {analysis.intent.value} (уверенность: {analysis.confidence:.2f})")
            return analysis
        except Exception as e:
            logger.error(f"Ошибка анализа: {e}")
            return AnalysisResult(
                intent=self._fallback_intent(query),
                confidence=0.6,
                reasoning=f"Автоматический анализ: {e}",
                suggested_tools=[],
                parameters={}
            )
    
    def _fallback_intent(self, query: str) -> IntentType:
        """Определение намерения без LLM"""
        q = query.lower()
        
        # Сначала проверяем по ключевым словам
        intent_map = {
            IntentType.VULNERABILITY_ASSESSMENT: ["уязвим", "vuln", "cve", "уязвимость"],
            IntentType.HARDENING_ASSESSMENT: ["sca", "hardening", "cis", "conform", "security", "конфиг"],
            IntentType.ALERT_TRIAGE: ["алерт", "alert", "событие", "инцидент", "sigid", "siganat"],
            IntentType.THREAT_HUNTING: ["hunt", "поиск", "угроз", "threat"],
            IntentType.AGENT_STATUS: ["агент", "agent", "статус", "активн"],
            IntentType.COMPLIANCE_CHECK: ["комплаенс", "compliance", "pci", "gdpr"],
            IntentType.IOC_CHECK: ["ioc", "индикатор", "репутац", "ip"],
            IntentType.REPORT_GENERATION: ["отчет", "report", "дашборд"],
        }
        
        # Проверяем по всем ключевым словам
        for intent, keywords in intent_map.items():
            if any(kw in q for kw in keywords):
                logger.info(f"→ Быстрое определение: {intent.value}")
                return intent
        
        # Если контекст есть, используем его
        if self.dialog_context.last_intent:
            try:
                return IntentType(self.dialog_context.last_intent)
            except ValueError:
                pass
        
        return IntentType.GENERAL_QUERY
    
    # ============================================================
    # План выполнения (без LLM, мгновенно)
    # ============================================================
    
    def _generate_plan_fast(self, analysis: AnalysisResult, query: str) -> ToolExecutionPlan:
        """
        Генерация плана без вызова LLM.
        Использует жесткий маппинг намерение → инструменты.
        """
        
        # Точный маппинг намерений на инструменты из MCP
        intent_tool_map = {
            IntentType.VULNERABILITY_ASSESSMENT: {
                "tools": ["get_wazuh_critical_vulnerabilities", "get_wazuh_vulnerabilities", "vulnerability_summary"],
                "description": "Оценка уязвимостей"
            },
            IntentType.HARDENING_ASSESSMENT: {
                "tools": ["perform_risk_assessment", "check_agent_health", "get_agent_configuration"],
                "description": "Оценка безопасности конфигураций"
            },
            IntentType.ALERT_TRIAGE: {
                "tools": ["get_wazuh_alerts", "get_wazuh_alert_summary", "analyze_alert_patterns"],
                "description": "Триаж алертов безопасности"
            },
            IntentType.THREAT_HUNTING: {
                "tools": ["search_security_events", "analyze_security_threat", "get_top_security_threats"],
                "description": "Поиск угроз безопасности"
            },
            IntentType.INCIDENT_RESPONSE: {
                "tools": ["analyze_security_threat", "search_security_events", "check_ioc_reputation"],
                "description": "Реагирование на инцидент"
            },
            IntentType.COMPLIANCE_CHECK: {
                "tools": ["run_compliance_check", "perform_risk_assessment"],
                "description": "Проверка соответствия стандартам"
            },
            IntentType.IOC_CHECK: {
                "tools": ["check_ioc_reputation", "search_security_events"],
                "description": "Проверка индикаторов компрометации"
            },
            IntentType.AGENT_STATUS: {
                "tools": ["get_wazuh_agents", "get_wazuh_running_agents", "check_agent_health"],
                "description": "Проверка статуса агентов"
            },
            IntentType.REPORT_GENERATION: {
                "tools": ["generate_security_report", "get_wazuh_statistics"],
                "description": "Генерация отчета"
            },
            IntentType.ACTIVE_RESPONSE: {
                "tools": ["analyze_security_threat", "wazuh_block_ip", "wazuh_isolate_host"],
                "description": "Активное реагирование"
            },
            IntentType.GENERAL_QUERY: {
                "tools": ["get_wazuh_alert_summary", "get_wazuh_statistics"],
                "description": "Общий анализ"
            }
        }
        
        plan_config = intent_tool_map.get(
            analysis.intent,
            intent_tool_map[IntentType.GENERAL_QUERY]
        )
        
        tool_calls = []
        for tool_name in plan_config["tools"]:
            if not any(t.get("name") == tool_name for t in self.default_tools):
                logger.debug(f"Инструмент {tool_name} не найден в MCP, пропускаем")
                continue
            
            params = {}
            if analysis.intent == IntentType.IOC_CHECK:
                params["indicator"] = query
            elif analysis.intent == IntentType.AGENT_STATUS:
                params["limit"] = 50
            
            tool_calls.append({
                "tool": tool_name,
                "parameters": params,
                "order": len(tool_calls) + 1
            })
        
        # Если нет инструментов - используем минимальный набор
        if not tool_calls:
            tool_calls = [
                {"tool": "get_wazuh_alert_summary", "parameters": {}, "order": 1},
                {"tool": "get_wazuh_statistics", "parameters": {}, "order": 2}
            ]
        
        logger.info(f"📋 План: {len(tool_calls)} инструментов")
        return ToolExecutionPlan(
            tool_calls=tool_calls,
            description=plan_config["description"]
        )
    
    # ============================================================
    # Выполнение инструментов с кэшем
    # ============================================================
    
    async def _execute_tools_with_cache(self, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Выполняет инструменты с кэшированием результатов"""
        results = []
        
        for call in tool_calls:
            tool_name = call["tool"]
            params = call["parameters"]
            
            # Проверяем кэш
            cache_key = f"{tool_name}:{json.dumps(params, sort_keys=True)}"
            now = datetime.now().timestamp()
            
            if cache_key in self._tool_results_cache:
                cached_result, cached_time = self._tool_results_cache[cache_key]
                if now - cached_time < self._cache_ttl:
                    logger.info(f"⚡ Кэширован результат {tool_name}")
                    results.append(cached_result)
                    continue
            
            # Выполняем инструмент
            logger.info(f"🛠 {tool_name} {params}")
            result = await self._call_mcp_tool(tool_name, params)
            
            # Сохраняем в кэш
            self._tool_results_cache[cache_key] = (result, now)
            results.append(result)
        
        return results
    
    async def _call_mcp_tool(self, tool_name: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Вызов инструмента через MCP сервер с кастомными исключениями"""
        
        # Пробуем wazuh-mcp
        if "wazuh-mcp" in self.mcp_servers:
            try:
                return await self.mcp_servers["wazuh-mcp"].call_tool(tool_name, parameters)
            except (MCPToolNotFoundError, MCPConnectionError, MCPTimeoutError) as e:
                logger.warning(f"Ошибка wazuh-mcp при вызове {tool_name}: {e} (code={e.code})")
                # Пробуем own-mcp как fallback
                pass
        
        # Пробуем own-mcp
        if "own-mcp" in self.mcp_servers:
            try:
                return await self.mcp_servers["own-mcp"].call_tool(tool_name, parameters)
            except (MCPToolNotFoundError, MCPConnectionError, MCPTimeoutError) as e:
                logger.warning(f"Ошибка own-mcp при вызове {tool_name}: {e} (code={e.code})")
        
        return {"error": f"Инструмент {tool_name} недоступен", "code": "mcp_unavailable"}
    
    # ============================================================
    # Генерация ответа (1 вызов LLM)
    # ============================================================
    
    async def _generate_quick_response(self, query: str, results: List[Dict[str, Any]], analysis: AnalysisResult) -> str:
        """Генерация ответа - 1 вызов LLM со всеми данными сразу"""
        
        # Формируем контекст для LLM
        query_context = self.dialog_context.get_recent_context(2)
        
        context_data = f"""
История диалога:
{query_context}

Текущий запрос: {query}
Тип запроса: {analysis.intent.value}
Обоснование: {analysis.reasoning}

Результаты инструментов:
{json.dumps(results, indent=2, ensure_ascii=False)[:3000]}
"""
        
        try:
            response = await self.llm_agent.generate_response(
                query=query,
                tool_results=results,
                analysis=analysis
            )
            return response or "Результаты получены."
            
        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}")
            return self._format_simple_response(results, analysis)
    
    def _format_simple_response(self, results: List[Dict[str, Any]], analysis: AnalysisResult) -> str:
        """Форматирование ответа без LLM"""
        parts = [f"📊 Результаты анализа ({analysis.intent.value}):"]
        
        for r in results:
            content = r.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item["text"]
                        if len(text) > 500:
                            text = text[:500] + "..."
                        parts.append(f"\n{text}")
        
        return "\n".join(parts[:5])


# ============================================================
# Асинхронная обертка для синхронного вызова
# ============================================================

class SOCAgentSyncV3:
    """Синхронная обертка для SOCAgentV3"""
    
    def __init__(self):
        self._agent = SOCAgentV3()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def initialize(self):
        """Синхронная инициализация"""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._agent.initialize())
        except Exception as e:
            logger.warning(f"Асинхронная инициализация не удалась, создаем свой event loop: {e}")
            if self._loop and self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
    
    def process_query(self, query: str) -> Dict[str, Any]:
        """Синхронная обработка запроса"""
        if not self._loop or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        
        return self._loop.run_until_complete(self._agent.process_query(query))


# ============================================================
# Тестирование
# ============================================================

async def test_agent_v3():
    """Тестирование SOC AI Agent v3"""
    import time
    
    print("=" * 60)
    print("🧪 Тестирование SOC AI Agent v3")
    print("=" * 60)
    
    agent = SOCAgentV3()
    await agent.initialize()
    
    if not agent.default_tools:
        print("⚠️ MCP сервер недоступен. Добавляем тестовые инструменты...")
        agent.default_tools = [
            {"name": "get_wazuh_alert_summary", "description": "Сводка алертов"},
            {"name": "get_wazuh_statistics", "description": "Статистика"},
            {"name": "get_wazuh_agents", "description": "Агенты"},
            {"name": "get_wazuh_critical_vulnerabilities", "description": "Критические уязвимости"},
            {"name": "get_wazuh_vulnerabilities", "description": "Уязвимости"},
            {"name": "check_agent_health", "description": "Здоровье агентов"},
            {"name": "get_wazuh_running_agents", "description": "Запущенные агенты"},
            {"name": "get_agent_configuration", "description": "Конфигурация"},
        ]
    
    test_queries = [
        "проверь, сколько активных агентов Wazuh сейчас есть",
        "какие инструменты тебе доступны",
        "покажи уязвимости с агента 001",
        "проверь данные sca агента 001",
        "да",
        "проведи анализ события которое произошло 77 раз за сутки",
    ]
    
    for i, query in enumerate(test_queries):
        print(f"\n{'='*40}")
        print(f"👤 [{i+1}] Запрос: {query}")
        
        start = time.time()
        result = await agent.process_query(query)
        elapsed = time.time() - start
        
        print(f"🤖 Ответ за {elapsed:.1f}с:")
        print(f"  Намерение: {result.get('intent')}")
        print(f"  Уверенность: {result.get('confidence')}")
        tools_used = result.get('tools_used') or []
        response_text = result.get('response') or ''
        print(f"  Инструменты: {tools_used}")
        print(f"  Ответ: {response_text[:200]}...")
    
    print(f"\n{'='*60}")
    print(f"✅ Тестирование завершено. Сессия: {agent.dialog_context.session_id}")
    print(f"   Всего поворотов диалога: {len(agent.dialog_context.turns)}")


def main():
    """Точка входа для запуска агента"""
    import time
    import asyncio
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("🚀 SOC AI Agent v3")
    print("=" * 60)
    print("Введите 'тест' для запуска тестов")
    print("Введите 'exit' для выхода")
    print("=" * 60)
    
    sync_agent = SOCAgentSyncV3()
    sync_agent.initialize()
    
    while True:
        try:
            query = input(f"\n{datetime.now().strftime('%H:%M:%S')} 👤 > ")
            if query.lower() in ['exit', 'quit', 'exit()', 'q']:
                break
            if query.lower() == 'тест':
                asyncio.run(test_agent_v3())
                continue
            
            start = time.time()
            result = sync_agent.process_query(query)
            elapsed = time.time() - start
            
            print(f"\n🤖 [{elapsed:.1f}с] {result.get('response')}")
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}")
    
    print("\n👋 До свидания!")


if __name__ == "__main__":
    main()

