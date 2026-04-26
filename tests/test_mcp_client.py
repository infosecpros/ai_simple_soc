#!/usr/bin/env python3
"""
Тесты для MCPClient с использованием aioresponses для мока HTTP.
"""

import pytest
import asyncio
from aioresponses import aioresponses

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.mcp_client import MCPClient
from services.exceptions import MCPConnectionError, MCPToolNotFoundError, MCPTimeoutError


class TestMCPClient:
    """Тесты MCPClient"""

    @pytest.mark.asyncio
    async def test_init(self):
        """Проверка инициализации"""
        client = MCPClient("http://test:3000", name="test-mcp")
        assert client.server_url == "http://test:3000"
        assert client.name == "test-mcp"
        assert client._initialized is False
        assert client._tools == []

    @pytest.mark.asyncio
    async def test_init_strips_trailing_slash(self):
        """Проверка что trailing slash удаляется"""
        client = MCPClient("http://test:3000/", name="test")
        assert client.server_url == "http://test:3000"

    @pytest.mark.asyncio
    async def test_connect_success_200(self):
        """Подключение с HTTP 200 + загрузка инструментов"""
        client = MCPClient("http://test:3000", name="test-mcp")
        tools_response = {
            "result": {
                "tools": [
                    {"name": "get_alerts", "description": "Get alerts", "inputSchema": {}},
                ]
            }
        }

        with aioresponses() as mocked:
            mocked.get("http://test:3000/health", status=200, body="ok")
            mocked.post(
                "http://test:3000/mcp",
                status=200,
                payload=tools_response,
            )
            result = await client.connect()

        assert result is True
        assert client._initialized is True
        assert len(client._tools) == 1
        assert client._tools[0]["name"] == "get_alerts"
        assert client.get_tool_names() == ["get_alerts"]

    @pytest.mark.asyncio
    async def test_connect_success_503(self):
        """Подключение с HTTP 503 (degraded) тоже считается успешным"""
        client = MCPClient("http://test:3000", name="test-mcp")

        with aioresponses() as mocked:
            mocked.get("http://test:3000/health", status=503, body="degraded")
            mocked.post(
                "http://test:3000/mcp",
                status=200,
                payload={"result": {"tools": []}},
            )
            result = await client.connect()

        assert result is True
        assert client._initialized is True

    @pytest.mark.asyncio
    async def test_connect_http_404_raises(self):
        """HTTP 404 — ошибка подключения"""
        client = MCPClient("http://test:3000", name="test-mcp")

        with aioresponses() as mocked:
            mocked.get("http://test:3000/health", status=404, body="not found")

            with pytest.raises(MCPConnectionError) as exc_info:
                await client.connect()

        assert "server_url" in exc_info.value.details
        assert client._initialized is False

    @pytest.mark.asyncio
    async def test_connect_timeout_returns_false(self):
        """Таймаут при подключении — возвращает False без ошибки"""
        client = MCPClient("http://test:3000", name="test-mcp")

        with aioresponses() as mocked:
            mocked.get("http://test:3000/health", exception=asyncio.TimeoutError())

            result = await client.connect()

        assert result is False
        assert client._initialized is False

    @pytest.mark.asyncio
    async def test_connect_connection_error_returns_false(self):
        """Сетевая ошибка — возвращает False"""
        client = MCPClient("http://test:3000", name="test-mcp")

        with aioresponses() as mocked:
            mocked.get("http://test:3000/health", exception=ConnectionError("refused"))

            result = await client.connect()

        assert result is False
        assert client._initialized is False

    @pytest.mark.asyncio
    async def test_connect_load_tools_fail_still_connected(self):
        """Если tools/list падает — всё равно connected (инструменты пусты)"""
        client = MCPClient("http://test:3000", name="test-mcp")

        with aioresponses() as mocked:
            mocked.get("http://test:3000/health", status=200, body="ok")
            mocked.post("http://test:3000/mcp", exception=ConnectionError("fail"))

            result = await client.connect()

        assert result is True
        assert client._initialized is True
        assert client._tools == []

    @pytest.mark.asyncio
    async def test_get_tools_list_empty_before_connect(self):
        """До connect список инструментов пуст"""
        client = MCPClient("http://test:3000", name="test-mcp")
        assert client.get_tools_list() == []
        assert client.get_tool_names() == []

    @pytest.mark.asyncio
    async def test_call_tool_not_initialized(self):
        """Вызов без инициализации — MCPConnectionError"""
        client = MCPClient("http://test:3000", name="test-mcp")

        with pytest.raises(MCPConnectionError) as exc_info:
            await client.call_tool("get_alerts")

        assert exc_info.value.code == "mcp_connection_error"

    @pytest.mark.asyncio
    async def test_call_tool_not_found(self):
        """Вызов несуществующего инструмента — MCPToolNotFoundError"""
        client = MCPClient("http://test:3000", name="test-mcp")
        client._initialized = True
        client._tools = [{"name": "get_alerts", "description": "", "inputSchema": {}}]

        with pytest.raises(MCPToolNotFoundError) as exc_info:
            await client.call_tool("nonexistent_tool")

        assert exc_info.value.code == "mcp_tool_not_found"
        assert "nonexistent_tool" in str(exc_info.value.details.get("tool_name", ""))

    @pytest.mark.asyncio
    async def test_call_tool_success(self):
        """Успешный вызов инструмента с проверкой JSON-RPC payload"""
        client = MCPClient("http://test:3000", name="test-mcp")
        client._initialized = True
        client._tools = [{"name": "get_alerts", "description": "", "inputSchema": {}}]

        expected_result = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "alerts data"}]},
        }

        with aioresponses() as mocked:
            mocked.post(
                "http://test:3000/mcp",
                status=200,
                payload=expected_result,
                repeat=True,
            )
            result = await client.call_tool("get_alerts", {"limit": 10})

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_call_tool_timeout(self):
        """Таймаут при вызове инструмента"""
        client = MCPClient("http://test:3000", name="test-mcp")
        client._initialized = True
        client._tools = [{"name": "get_alerts", "description": "", "inputSchema": {}}]

        with aioresponses() as mocked:
            mocked.post("http://test:3000/mcp", exception=asyncio.TimeoutError())

            with pytest.raises(MCPTimeoutError) as exc_info:
                await client.call_tool("get_alerts")

        assert exc_info.value.code == "mcp_timeout"
        assert exc_info.value.details.get("tool_name") == "get_alerts"

    @pytest.mark.asyncio
    async def test_call_tool_http_error(self):
        """HTTP ошибка при вызове — MCPConnectionError"""
        client = MCPClient("http://test:3000", name="test-mcp")
        client._initialized = True
        client._tools = [{"name": "get_alerts", "description": "", "inputSchema": {}}]

        with aioresponses() as mocked:
            mocked.post("http://test:3000/mcp", status=500, body="server error")

            with pytest.raises(MCPConnectionError) as exc_info:
                await client.call_tool("get_alerts")

        assert exc_info.value.code == "mcp_connection_error"

    @pytest.mark.asyncio
    async def test_call_tool_no_params(self):
        """Вызов инструмента без параметров"""
        client = MCPClient("http://test:3000", name="test-mcp")
        client._initialized = True
        client._tools = [{"name": "ping", "description": "", "inputSchema": {}}]

        with aioresponses() as mocked:
            mocked.post("http://test:3000/mcp", status=200, payload={}, repeat=True)
            await client.call_tool("ping")

    @pytest.mark.asyncio
    async def test_close_cleans_up(self):
        """close() сбрасывает состояние — сессия закрывается"""
        client = MCPClient("http://test:3000", name="test-mcp")
        _ = await client._ensure_session()
        client._initialized = True

        await client.close()

        assert client._session is None
        assert client._initialized is False

    @pytest.mark.asyncio
    async def test_double_close_no_error(self):
        """Двойной close() не вызывает ошибку"""
        client = MCPClient("http://test:3000", name="test-mcp")
        await client.close()
        await client.close()

    @pytest.mark.asyncio
    async def test_ensure_session_creates_if_none(self):
        """_ensure_session создаёт сессию если её нет"""
        client = MCPClient("http://test:3000", name="test-mcp")
        session = await client._ensure_session()
        assert session is not None
        assert not session.closed
        await client.close()

    @pytest.mark.asyncio
    async def test_ensure_session_reuses_existing(self):
        """_ensure_session переиспользует существующую сессию"""
        client = MCPClient("http://test:3000", name="test-mcp")
        session1 = await client._ensure_session()
        session2 = await client._ensure_session()
        assert session1 is session2
        await client.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
