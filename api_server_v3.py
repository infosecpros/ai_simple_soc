#!/usr/bin/env python3
"""
REST API сервер для SOC AI Agent v10 — мульти-агентная архитектура.

Эндпоинты:
  GET  /health       — Healthcheck
  GET  /tools        — Список инструментов
  POST /query        — Обработка запроса (через Orchestrator)
  POST /chat         — Chat с SSE
  GET  /approvals    — Очередь подтверждений (ResponderAgent)
  POST /approve/{id} — Подтверждение действия
  GET  /agents       — Список доступных агентов
  GET  /             — Web UI
"""

import asyncio
import json
import logging
import os
import signal
import sys
from typing import Optional, Any, Dict, List
from datetime import datetime

from aiohttp import web
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

from agents.orchestrator import Orchestrator
from agents.base_agent import AgentContext, AgentResult
from services.mcp_client import MCPClient
from services.circuit_breaker import CircuitBreaker
from services.exceptions import SOCAgentError
from config.settings import get_config

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Маппинг кодов ошибок на HTTP статусы
ERROR_HTTP_STATUS: Dict[str, int] = {
    "mcp_connection_error": 502,
    "mcp_tool_not_found": 400,
    "mcp_tool_call_error": 502,
    "mcp_timeout": 504,
    "llm_analysis_error": 503,
    "query_validation_error": 400,
    "rate_limit_exceeded": 429,
    "circuit_breaker_open": 503,
    "internal_error": 500,
}

# Таймаут на весь цикл обработки запроса (сек)
AGENT_TIMEOUT_SECONDS = 60.0


def _error_response(error: Exception) -> web.Response:
    """Преобразует исключение в HTTP ответ с правильным статусом"""
    if isinstance(error, SOCAgentError):
        status = ERROR_HTTP_STATUS.get(error.code, 500)
        details: Dict[str, Any] = error.details  # type: ignore[assignment]
        return web.json_response(
            {"error": str(error), "code": error.code, "details": details},
            status=status,
        )
    if isinstance(error, json.JSONDecodeError):
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if isinstance(error, asyncio.TimeoutError):
        return web.json_response({"error": "Request timeout"}, status=504)
    # Любая другая ошибка
    logger.exception("Internal error: %s", error)
    return web.json_response({"error": "Internal server error"}, status=500)


async def _run_with_timeout(coro: Any, timeout: float = AGENT_TIMEOUT_SECONDS) -> Any:
    """Запускает корутину с таймаутом"""
    try:
        result: Any = await asyncio.wait_for(coro, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        raise asyncio.TimeoutError(f"Request timed out after {timeout}s")


class SOCAgentAPIV3:
    """REST API сервер для SOC AI Agent v10 — мульти-агентная архитектура"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.orchestrator: Optional[Orchestrator] = None
        self.app = web.Application()
        self._mcp_servers: Dict[str, MCPClient] = {}
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._llm_agent: Optional[Any] = None
        self._available_tools: List[Dict[str, Any]] = []
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_get('/tools', self.get_tools)
        self.app.router.add_get('/agents', self.get_agents)
        self.app.router.add_get('/approvals', self.get_approvals)
        self.app.router.add_post('/approve/{approval_id}', self.approve_action)
        self.app.router.add_post('/query', self.process_query)
        self.app.router.add_post('/chat', self.chat_endpoint)
        self.app.router.add_get('/', self.web_ui)

    async def health_check(self, request: web.Request) -> web.Response:
        """Healthcheck"""
        mcp_status: Dict[str, str] = {}
        for name, mcp in self._mcp_servers.items():
            mcp_status[name] = "connected" if mcp.is_connected() else "disconnected"

        status: Dict[str, Any] = {
            "status": "ok",
            "service": "soc-ai-agent-v10",
            "version": "0.10.0",
            "architecture": "multi-agent",
            "agents": self.orchestrator.get_available_agents() if self.orchestrator else [],
            "mcp_servers": mcp_status,
            "llm_connected": self._llm_agent is not None,
            "tools_available": len(self._available_tools),
            "timestamp": datetime.now().isoformat(),
        }
        return web.json_response(status)

    async def get_tools(self, request: web.Request) -> web.Response:
        """Список инструментов из MCP-серверов"""
        if not self.orchestrator:
            return web.json_response({"error": "Agent not initialized"}, status=503)

        return web.json_response({
            "tools": self._available_tools,
            "count": len(self._available_tools),
        })

    async def get_agents(self, request: web.Request) -> web.Response:
        """Список всех агентов"""
        if not self.orchestrator:
            return web.json_response({"error": "Agent not initialized"}, status=503)

        agents = self.orchestrator.get_available_agents()
        return web.json_response({"agents": agents})

    async def get_approvals(self, request: web.Request) -> web.Response:
        """Очередь подтверждений ResponderAgent"""
        if not self.orchestrator:
            return web.json_response({"error": "Agent not initialized"}, status=503)

        approvals = self.orchestrator.get_pending_approvals()
        return web.json_response({
            "approvals": [a.model_dump() for a in approvals],
            "count": len(approvals),
        })

    async def approve_action(self, request: web.Request) -> web.Response:
        """Подтверждение действия"""
        if not self.orchestrator:
            return web.json_response({"error": "Agent not initialized"}, status=503)

        approval_id = request.match_info.get("approval_id")
        if not approval_id:
            return web.json_response({"error": "approval_id is required"}, status=400)

        result = await self.orchestrator.approve_action(approval_id)
        return web.json_response(result)

    async def process_query(self, request: web.Request) -> web.Response:
        """
        Обработка запроса через Orchestrator с реальными MCP/LLM.
        """
        try:
            data = await request.json()
            query = data.get("query", "").strip()

            if not query:
                return web.json_response({"error": "Query is required"}, status=400)

            logger.info("Received query: %s", query[:80])

            if not self.orchestrator:
                return web.json_response({"error": "Agent not initialized"}, status=503)

            context = AgentContext(
                session_id=f"api_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                query=query,
                available_tools=self._available_tools,
                mcp_servers=self._mcp_servers,
                circuit_breakers=self._circuit_breakers,
                llm_agent=self._llm_agent,
                cache={},
            )

            result: AgentResult = await _run_with_timeout(
                self.orchestrator.route_query(query, context),
                timeout=AGENT_TIMEOUT_SECONDS,
            )

            return web.json_response({
                "query": query,
                "response": result.response,
                "agent": result.data.get("analysis", {}).get("intent", "unknown") if result.data else "unknown",
                "confidence": result.confidence,
                "tools_used": result.tools_used,
                "requires_confirmation": result.requires_confirmation,
            })

        except (json.JSONDecodeError, SOCAgentError, asyncio.TimeoutError) as e:
            return _error_response(e)
        except Exception as e:
            logger.exception("Error processing query: %s", e)
            return _error_response(e)

    async def chat_endpoint(self, request: web.Request) -> web.StreamResponse:
        """Chat с SSE поддержкой"""
        try:
            data = await request.json()
            messages = data.get("messages", [])

            if not messages:
                return web.json_response({"error": "Messages are required"}, status=400)

            last_message = next(
                (msg for msg in reversed(messages) if msg.get("role") == "user"),
                None,
            )
            if not last_message:
                return web.json_response({"error": "No user message found"}, status=400)

            query = last_message.get("content", "").strip()

            accept = request.headers.get("Accept", "")
            if "text/event-stream" in accept:
                return await self._sse_chat(query, request)

            if not self.orchestrator:
                return web.json_response({"error": "Agent not initialized"}, status=503)

            context = AgentContext(
                session_id=f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                query=query,
                available_tools=self._available_tools,
                mcp_servers=self._mcp_servers,
                circuit_breakers=self._circuit_breakers,
                llm_agent=self._llm_agent,
                cache={},
            )

            result: AgentResult = await _run_with_timeout(
                self.orchestrator.route_query(query, context),
                timeout=AGENT_TIMEOUT_SECONDS,
            )

            return web.json_response({
                "response": result.response,
                "agent": result.data.get("analysis", {}).get("intent", "unknown") if result.data else "unknown",
            })

        except (json.JSONDecodeError, SOCAgentError, asyncio.TimeoutError) as e:
            return _error_response(e)
        except Exception as e:
            logger.exception("Chat error: %s", e)
            return _error_response(e)

    async def _sse_chat(self, query: str, request: web.Request) -> web.StreamResponse:
        """SSE стриминг ответа"""
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
            },
        )

        await response.prepare(request)

        try:
            await response.write(
                f"data: {json.dumps({'status': 'processing'})}\n\n".encode()
            )

            if not self.orchestrator:
                await response.write(
                    f"data: {json.dumps({'error': 'Agent not initialized'})}\n\n".encode()
                )
                await response.write_eof()
                return response

            context = AgentContext(
                session_id=f"sse_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                query=query,
                available_tools=self._available_tools,
                mcp_servers=self._mcp_servers,
                circuit_breakers=self._circuit_breakers,
                llm_agent=self._llm_agent,
                cache={},
            )
            result = await self.orchestrator.route_query(query, context)

            await response.write(f"data: {json.dumps(result.model_dump())}\n\n".encode())
            await response.write(
                f"data: {json.dumps({'status': 'complete'})}\n\n".encode()
            )

        except Exception as e:
            logger.error("SSE error: %s", e)
            await response.write(
                f"data: {json.dumps({'error': str(e)})}\n\n".encode()
            )
        finally:
            await response.write_eof()

        return response

    async def web_ui(self, request: web.Request) -> web.Response:
        """Простой Web UI"""
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>SOC AI Agent v10</title>
    <meta charset="utf-8">
    <style>
        body { font-family: -apple-system, system-ui, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
        .container { display: flex; flex-direction: column; gap: 20px; }
        .card { border: 1px solid #ddd; border-radius: 8px; padding: 20px; }
        h1 { color: #333; }
        .endpoint { background: #f5f5f5; padding: 8px 12px; border-radius: 4px; margin: 4px 0; }
        .endpoint code { font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <h1> SOC AI Agent v10</h1>
        <div class="card">
            <h3>Мульти-агентная архитектура</h3>
            <p>Агенты: Triage → Investigator / Responder / Reporter</p>
            <p>Orchestrator маршрутизирует запросы по намерению</p>
        </div>
        <div class="card">
            <h3>Эндпоинты API</h3>
            <div class="endpoint"><code>GET /health</code> — Healthcheck</div>
            <div class="endpoint"><code>GET /tools</code> — Список инструментов</div>
            <div class="endpoint"><code>GET /agents</code> — Список агентов</div>
            <div class="endpoint"><code>GET /approvals</code> — Очередь подтверждений</div>
            <div class="endpoint"><code>POST /approve/{id}</code> — Подтвердить действие</div>
            <div class="endpoint"><code>POST /query</code> — Обработать запрос</div>
            <div class="endpoint"><code>POST /chat</code> — Чат (SSE)</div>
        </div>
    </div>
</body>
</html>
        """
        return web.Response(text=html, content_type='text/html')

    async def _connect_mcp_servers(self) -> None:
        """Подключение к MCP-серверам"""
        mcp_cfg = get_config().mcp

        # Wazuh-MCP
        wazuh_client = MCPClient(mcp_cfg.url, name="wazuh-mcp")
        if await wazuh_client.connect():
            self._mcp_servers["wazuh-mcp"] = wazuh_client
            logger.info("Connected Wazuh-MCP: %s tools", len(wazuh_client.get_tools_list()))
        else:
            logger.warning("Wazuh-MCP unavailable")

        # Own-MCP
        own_client = MCPClient(mcp_cfg.own_url, name="own-mcp")
        if await own_client.connect():
            self._mcp_servers["own-mcp"] = own_client
            logger.info("Connected Own-MCP: %s tools", len(own_client.get_tools_list()))
        else:
            logger.warning("Own-MCP unavailable")

        # Собираем все инструменты
        all_tools: List[Dict[str, Any]] = []
        for mcp in self._mcp_servers.values():
            all_tools.extend(mcp.get_tools_list())
        self._available_tools = all_tools

        # Инициализация CircuitBreaker
        self._circuit_breakers = {
            "wazuh-mcp": CircuitBreaker(
                "mcp-wazuh",
                failure_threshold=mcp_cfg.circuit_breaker_threshold,
                reset_timeout=mcp_cfg.circuit_breaker_reset_seconds,
            ),
            "own-mcp": CircuitBreaker(
                "mcp-own",
                failure_threshold=mcp_cfg.circuit_breaker_threshold,
                reset_timeout=mcp_cfg.circuit_breaker_reset_seconds,
            ),
        }

    def _init_llm(self) -> None:
        """Инициализация LLM-агента (если ключ есть)"""
        llm_cfg = get_config().llm
        if llm_cfg.api_key and llm_cfg.api_key.get_secret_value():
            try:
                from llm_agent import SOCLLMAgent
                self._llm_agent = SOCLLMAgent()
                logger.info("LLM-agent connected")
            except Exception as e:
                logger.warning("LLM-agent unavailable: %s", e)
                self._llm_agent = None
        else:
            logger.info("LLM not configured (LLM_API_KEY not set)")

    async def initialize(self) -> None:
        """Инициализация MCP, LLM и Orchestrator"""
        logger.info("Initializing SOC AI Agent API v10")

        # Подключаемся к MCP-серверам
        await self._connect_mcp_servers()

        # Инициализация LLM
        self._init_llm()

        # Создаём Orchestrator
        self.orchestrator = Orchestrator()

        logger.info("SOC AI Agent API v10 ready")
        logger.info("Agents: %s", [a['name'] for a in self.orchestrator.get_available_agents()])
        logger.info("MCP: %s", list(self._mcp_servers.keys()) or "none")
        logger.info("LLM: %s", "connected" if self._llm_agent else "not connected")
        logger.info("Tools: %s", len(self._available_tools))

    async def start(self) -> None:
        """Запуск сервера"""
        await self.initialize()

        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)

        logger.info("API v10 server running on %s:%s", self.host, self.port)
        logger.info("Endpoints:")
        logger.info("  GET  /health            - Healthcheck")
        logger.info("  GET  /tools             - Tool list")
        logger.info("  GET  /agents            - Agent list")
        logger.info("  GET  /approvals         - Approval queue")
        logger.info("  POST /approve/{id}      - Approve action")
        logger.info("  POST /query             - Process query")
        logger.info("  POST /chat              - Chat (SSE)")
        logger.info("  GET  /                  - Web UI")

        await site.start()

        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            await self.cleanup()
            await runner.cleanup()
            logger.info("Server stopped")

    async def cleanup(self) -> None:
        """Закрытие всех соединений"""
        for _name, client in self._mcp_servers.items():
            await client.close()
        self._mcp_servers.clear()
        self._circuit_breakers.clear()
        logger.info("Connections closed")


async def main() -> None:
    api = SOCAgentAPIV3(
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8080"))
    )

    # Graceful shutdown по SIGTERM/SIGINT
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received, starting shutdown...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        await api.start()
        await shutdown_event.wait()
    except KeyboardInterrupt:
        logger.info("SOC AI Agent API v10 stopped")
    finally:
        await api.cleanup()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
