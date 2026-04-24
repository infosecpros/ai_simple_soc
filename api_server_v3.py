#!/usr/bin/env python3
"""
REST API сервер для SOC AI Agent v3
"""

import asyncio
import json
import logging
import os
import sys
from typing import Optional, Union
from datetime import datetime

from aiohttp import web
from aiohttp.web import StreamResponse
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

from soc_agent_v3 import SOCAgentV3

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SOCAgentAPIV3:
    """REST API сервер для SOC AI Agent v3"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.agent: Optional[SOCAgentV3] = None
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_get('/tools', self.get_tools)
        self.app.router.add_post('/query', self.process_query)
        self.app.router.add_post('/chat', self.chat_endpoint)
        self.app.router.add_get('/', self.web_ui)

    async def health_check(self, request: web.Request) -> web.Response:
        """Healthcheck"""
        status = {
            "status": "ok",
            "service": "soc-ai-agent-v3",
            "version": "0.6.0",
            "timestamp": datetime.now().isoformat(),
            "llm_available": bool(self.agent and self.agent.llm_agent._agent)
        }
        return web.json_response(status)

    async def get_tools(self, request: web.Request) -> web.Response:
        if not self.agent:
            return web.json_response({"error": "Agent not initialized"}, status=503)

        tools = []
        for server_name, client in self.agent.mcp_servers.items():
            for tool in client.get_tools_list():
                tools.append({
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "server": server_name
                })

        return web.json_response({"tools": tools, "total": len(tools)})

    async def process_query(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            query = data.get("query", "").strip()

            if not query:
                return web.json_response({"error": "Query is required"}, status=400)

            logger.info(f"📨 Получен запрос: {query}")

            if not self.agent:
                return web.json_response({"error": "Agent not initialized"}, status=503)

            result = await self.agent.process_query(query)
            return web.json_response(result)

        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def chat_endpoint(self, request: web.Request) -> web.StreamResponse:
        try:
            data = await request.json()
            messages = data.get("messages", [])

            if not messages:
                return web.json_response({"error": "Messages are required"}, status=400)

            last_message = next(
                (msg for msg in reversed(messages) if msg.get("role") == "user"),
                None
            )
            if not last_message:
                return web.json_response({"error": "No user message found"}, status=400)

            query = last_message.get("content", "").strip()

            accept = request.headers.get("Accept", "")
            if "text/event-stream" in accept:
                return await self._sse_chat(query, request)

            if not self.agent:
                return web.json_response({"error": "Agent not initialized"}, status=503)
            result = await self.agent.process_query(query)
            return web.json_response(result)

        except Exception as e:
            logger.error(f"❌ Ошибка chat: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _sse_chat(self, query: str, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
            }
        )

        await response.prepare(request)

        try:
            await response.write(
                f"data: {json.dumps({'status': 'processing'})}\n\n".encode()
            )

            if not self.agent:
                await response.write(
                    f"data: {json.dumps({'error': 'Agent not initialized'})}\n\n".encode()
                )
                await response.write_eof()
                return response

            result = await self.agent.process_query(query)
            await response.write(f"data: {json.dumps(result)}\n\n".encode())
            await response.write(
                f"data: {json.dumps({'status': 'complete'})}\n\n".encode()
            )

        except Exception as e:
            logger.error(f"❌ SSE ошибка: {e}")
            await response.write(
                f"data: {json.dumps({'error': str(e)})}\n\n".encode()
            )
        finally:
            await response.write_eof()

        return response

    async def web_ui(self, request: web.Request) -> web.Response:
        html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>SOC AI Agent v3</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #1a1a2e; color: #e0e0e0; }
        h1 { color: #00d4ff; margin-bottom: 20px; }
        .chat-container { border: 1px solid #333; border-radius: 10px; padding: 20px; min-height: 400px; max-height: 600px; overflow-y: auto; background: #16213e; }
        .message { margin: 10px 0; padding: 12px; border-radius: 8px; line-height: 1.5; }
        .user { background: #0f3460; text-align: right; border: 1px solid #1a5276; }
        .agent { background: #1a1a3e; border: 1px solid #2d2d5e; }
        .system { background: #1e3a2e; border: 1px solid #2d5e3e; font-size: 0.9em; }
        strong { color: #00d4ff; }
        .user strong { color: #4fc3f7; }
        .input-area { display: flex; margin-top: 15px; gap: 10px; }
        #query { flex: 1; padding: 12px; font-size: 16px; background: #16213e; border: 1px solid #333; border-radius: 5px; color: #e0e0e0; }
        #query:focus { outline: none; border-color: #00d4ff; }
        button { padding: 12px 24px; font-size: 16px; background: #00d4ff; color: #1a1a2e; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; }
        button:hover { background: #00b4d9; }
        .stats { color: #888; font-size: 0.8em; margin-top: 5px; }
        .thinking { color: #888; font-style: italic; }
        .error { color: #ff4444; }
    </style>
</head>
<body>
    <h1>🤖 SOC AI Agent v3</h1>
    <p style="color:#888;margin-bottom:20px;">
        Оптимизированная версия с кэшированием LLM, контекстом диалога и быстрым ответом
    </p>
    <div class="chat-container" id="chat">
        <div class="message agent">
            <strong>🤖 Агент v3</strong>
            <p>Привет! Я SOC AI Agent v3 с оптимизациями:</p>
            <ul style="margin-left:20px;margin-top:8px;">
                <li>⚡ Кэширование LLM запросов</li>
                <li>💬 Контекст диалога</li>
                <li>🎯 Правильный выбор инструментов</li>
                <li>🔒 Безопасность логов</li>
            </ul>
            <p style="margin-top:8px;">Чем могу помочь?</p>
        </div>
    </div>
    <div class="input-area">
        <input type="text" id="query" placeholder="Введите запрос..." autofocus>
        <button onclick="sendQuery()">Отправить</button>
    </div>
    <script>
        async function sendQuery() {
            const input = document.getElementById('query');
            const query = input.value.trim();
            if (!query) return;

            const chat = document.getElementById('chat');
            chat.innerHTML += '<div class="message user"><strong>👤 Вы:</strong> ' + escapeHtml(query) + '</div>';
            input.value = '';
            chat.innerHTML += '<div class="message system thinking"><strong>🤖 Агент:</strong> ⏳ Думаю...</div>';
            chat.scrollTop = chat.scrollHeight;

            try {
                const response = await fetch('/query', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({query: query})
                });
                const result = await response.json();

                // Убираем "думаю"
                const thinking = chat.querySelector('.thinking');
                if (thinking) thinking.remove();

                const stats = result.tools_used && result.tools_used.length > 0
                    ? '<div class="stats">🔧 ' + result.tools_used.join(', ') + ' | 🎯 ' + (result.intent || '') + '</div>'
                    : '';

                chat.innerHTML += '<div class="message agent"><strong>🤖 Агент:</strong> ' + escapeHtml(result.response || 'Нет ответа') + stats + '</div>';
            } catch (error) {
                const thinking = chat.querySelector('.thinking');
                if (thinking) thinking.remove();
                chat.innerHTML += '<div class="message error"><strong>❌ Ошибка:</strong> ' + error.message + '</div>';
            }

            chat.scrollTop = chat.scrollHeight;
        }

        function escapeHtml(text) {
            return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }

        document.getElementById('query').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') sendQuery();
        });
    </script>
</body>
</html>"""
        return web.Response(text=html, content_type='text/html')

    async def initialize(self):
        logger.info("🚀 Инициализация SOC AI Agent API v3")
        self.agent = SOCAgentV3()
        await self.agent.initialize()
        logger.info("✅ SOC AI Agent API v3 готов к работе")

    async def start(self):
        await self.initialize()

        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)

        logger.info(f"🌐 API v3 сервер запущен на {self.host}:{self.port}")
        logger.info("📋 Доступные эндпоинты:")
        logger.info("  • GET  /health     - Healthcheck")
        logger.info("  • GET  /tools      - Список инструментов")
        logger.info("  • POST /query      - Обработка запроса")
        logger.info("  • POST /chat       - Chat с SSE")
        logger.info("  • GET  /           - Web UI")

        await site.start()

        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            await runner.cleanup()
            if self.agent:
                await self.agent.close()


async def main():
    api = SOCAgentAPIV3(
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8080"))
    )

    try:
        await api.start()
    except KeyboardInterrupt:
        logger.info("👋 SOC AI Agent API v3 завершил работу")


if __name__ == "__main__":
    asyncio.run(main())
