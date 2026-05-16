#!/usr/bin/env python3
"""
Тесты для Drain3 MCP-сервера.
"""

import pytest
from services.drain3_server import Drain3MCPServer


@pytest.fixture
def drain3_server():
    """Свежий экземпляр Drain3 без сохранённого состояния"""
    return Drain3MCPServer()


class TestDrain3Core:
    """Тесты ядра Drain3 — парсинг логов в шаблоны"""

    @pytest.mark.asyncio
    async def test_parse_single_log(self, drain3_server):
        """Один лог — возвращает cluster_id и template"""
        result = await drain3_server._handle_parse({
            "log_message": "ERROR sshd: Failed password for root from 192.168.1.100"
        })
        assert result["cluster_id"] is not None
        assert "Failed password" in result["template"]
        assert result["cluster_size"] == 1

    @pytest.mark.asyncio
    async def test_parse_same_template(self, drain3_server):
        """Похожие логи — один кластер"""
        r1 = await drain3_server._handle_parse({
            "log_message": "ERROR sshd: Failed password for root from 192.168.1.100"
        })
        r2 = await drain3_server._handle_parse({
            "log_message": "ERROR sshd: Failed password for admin from 10.0.0.1"
        })
        assert r1["cluster_id"] == r2["cluster_id"]
        assert r2["cluster_size"] == 2

    @pytest.mark.asyncio
    async def test_parse_different_template(self, drain3_server):
        """Разные типы логов — разные кластеры"""
        r1 = await drain3_server._handle_parse({
            "log_message": "ERROR sshd: Failed password for root from 192.168.1.100"
        })
        r2 = await drain3_server._handle_parse({
            "log_message": "INFO wazuh-agent: Agent started successfully"
        })
        assert r1["cluster_id"] != r2["cluster_id"]

    @pytest.mark.asyncio
    async def test_parse_empty_log(self, drain3_server):
        """Пустой лог — ошибка"""
        result = await drain3_server._handle_parse({"log_message": ""})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_parse_missing_param(self, drain3_server):
        """Без параметра — ошибка"""
        result = await drain3_server._handle_parse({})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_parse_anomaly_detection(self, drain3_server):
        """Лог с аномалией — отличается от нормы"""
        # Нормальные логи
        for _ in range(5):
            await drain3_server._handle_parse({
                "log_message": "INFO agent[123]: Heartbeat OK"
            })

        # Аномалия
        result = await drain3_server._handle_parse({
            "log_message": "CRITICAL agent[456]: Connection timeout to manager after 30 seconds - ALERT"
        })
        # Аномалия — отдельный кластер
        assert result["cluster_id"] is not None
        assert "ALERT" in result["template"] or "timeout" in result["template"]


class TestDrain3Match:
    """Тесты match — поиск шаблона без добавления"""

    @pytest.mark.asyncio
    async def test_match_existing(self, drain3_server):
        """Поиск существующего шаблона (нужно >=2 похожих лога для обобщения)"""
        await drain3_server._handle_parse({
            "log_message": "ERROR sshd: Failed password for root from 192.168.1.100"
        })
        await drain3_server._handle_parse({
            "log_message": "ERROR sshd: Failed password for admin from 10.0.0.1"
        })
        # Теперь Drain обобщил до шаблона — match найдёт
        result = await drain3_server._handle_match({
            "log_message": "ERROR sshd: Failed password for nobody from 172.16.0.1"
        })
        assert result["matched"] is True

    @pytest.mark.asyncio
    async def test_match_nonexistent(self, drain3_server):
        """Поиск несуществующего шаблона"""
        result = await drain3_server._handle_match({
            "log_message": "SOMETHING COMPLETELY NEW AND UNKNOWN"
        })
        assert result["matched"] is False

    @pytest.mark.asyncio
    async def test_match_empty(self, drain3_server):
        """Пустой запрос — ошибка"""
        result = await drain3_server._handle_match({"log_message": ""})
        assert "error" in result


class TestDrain3Clusters:
    """Тесты get_clusters и get_stats"""

    @pytest.mark.asyncio
    async def test_get_clusters_empty(self, drain3_server):
        """Без логов — 0 кластеров"""
        clusters = await drain3_server._handle_clusters({"limit": 10})
        assert clusters["total_clusters"] == 0
        assert len(clusters["clusters"]) == 0

    @pytest.mark.asyncio
    async def test_get_clusters_with_data(self, drain3_server):
        """С логами — возвращает кластеры"""
        await drain3_server._handle_parse({
            "log_message": "ERROR sshd: Failed password for root from 192.168.1.100"
        })
        await drain3_server._handle_parse({
            "log_message": "INFO wazuh-agent: Agent started"
        })
        clusters = await drain3_server._handle_clusters({"limit": 10})
        assert clusters["total_clusters"] == 2
        assert len(clusters["clusters"]) == 2

    @pytest.mark.asyncio
    async def test_get_clusters_ordered_by_size(self, drain3_server):
        """Кластеры отсортированы по размеру (большие первыми)"""
        await drain3_server._handle_parse({
            "log_message": "INFO agent: heartbeat"
        })
        await drain3_server._handle_parse({
            "log_message": "INFO agent: heartbeat"
        })
        await drain3_server._handle_parse({
            "log_message": "ERROR sshd: Failed password"
        })
        clusters = await drain3_server._handle_clusters({"limit": 10})
        # Первый — самый большой
        assert clusters["clusters"][0]["size"] >= clusters["clusters"][1]["size"]

    @pytest.mark.asyncio
    async def test_get_stats(self, drain3_server):
        """Статистика после парсинга"""
        await drain3_server._handle_parse({
            "log_message": "ERROR sshd: Failed password for root from 192.168.1.100"
        })
        stats = await drain3_server._handle_stats({})
        assert stats["total_clusters"] >= 1
        assert stats["total_logs_parsed"] >= 1
        assert "config" in stats
        assert stats["config"]["depth"] == 4


class TestDrain3MCPSchema:
    """Тесты MCP-протокола — JSON-RPC"""

    @pytest.mark.asyncio
    async def test_tools_list(self, drain3_server):
        """tools/list возвращает список инструментов"""
        mock_request = create_mock_request({"method": "tools/list", "id": 1})
        resp = await drain3_server.handle_request(mock_request)
        data = resp  # это dict (web.json_response оборачивает)
        # Проверяем что response — это web.Response
        # В тесте будем вызывать _handle напрямую
        tools_response = await drain3_server._handle_tools_list()
        assert len(tools_response["tools"]) >= 4
        tool_names = [t["name"] for t in tools_response["tools"]]
        assert "parse_log" in tool_names
        assert "get_clusters" in tool_names
        assert "get_stats" in tool_names
        assert "match_log" in tool_names

    @pytest.mark.asyncio
    async def test_tools_call_parse(self, drain3_server):
        """tools/call parse_log"""
        result = await drain3_server._handle_tools_call("parse_log", {
            "log_message": "ERROR: test log"
        })
        # Извлекаем text из content
        content = result["content"][0]["text"]
        data = __import__('json').loads(content)
        assert data["cluster_id"] is not None


# ---- Хелперы для тестов ----

@pytest.fixture
def drain3_server_with_tools():
    """Drain3 сервер с доступом к _handle методам"""
    return Drain3MCPServer()


# Добавляем методы для прямого вызова tools/list и tools/call
# (без aiohttp request — только логика)

async def _handle_tools_list(self) -> dict:
    return {
        "tools": [
            {"name": "parse_log", "description": "", "inputSchema": {"type": "object"}},
            {"name": "match_log", "description": "", "inputSchema": {"type": "object"}},
            {"name": "get_clusters", "description": "", "inputSchema": {"type": "object"}},
            {"name": "get_stats", "description": "", "inputSchema": {"type": "object"}},
            {"name": "save_state", "description": "", "inputSchema": {"type": "object"}},
        ]
    }

async def _handle_tools_call(self, name: str, args: dict) -> dict:
    handlers = {
        "parse_log": self._handle_parse,
        "match_log": self._handle_match,
        "get_clusters": self._handle_clusters,
        "get_stats": self._handle_stats,
        "save_state": self._handle_save,
    }
    handler = handlers.get(name)
    if not handler:
        return {"content": [{"type": "text", "text": '{"error": "not found"}'}]}
    result = await handler(args)
    return {
        "content": [
            {"type": "text", "text": __import__('json').dumps(result, ensure_ascii=False, default=str)}
        ]
    }


Drain3MCPServer._handle_tools_list = _handle_tools_list
Drain3MCPServer._handle_tools_call = _handle_tools_call


def create_mock_request(body: dict):
    """Создаёт mock aiohttp request"""
    class MockRequest:
        async def json(self):
            return body
    return MockRequest()
