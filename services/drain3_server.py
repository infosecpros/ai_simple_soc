#!/usr/bin/env python3
"""
Drain3 MCP-сервер — парсинг логов в шаблоны.

Поток:
  Лог → Drain3.add_log_message() → template_id + cluster
  Агент вызывает MCP-инструменты:
    - parse_log(log_message) — разобрать один лог
    - get_clusters() — все найденные шаблоны
    - get_stats() — статистика парсинга

Запуск:
  python -m services.drain3_server [--port 3001] [--state drain3_state.bin]
"""

import argparse
import json
import logging
import os

from aiohttp import web

from drain3 import TemplateMiner  # type: ignore[import-untyped]
from drain3.template_miner_config import TemplateMinerConfig  # type: ignore[import-untyped]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("drain3-mcp")


# ---- MCP JSON-RPC обработчики ----

class Drain3MCPServer:
    """
    MCP-сервер для Drain3.
    Поддерживает JSON-RPC 2.0 через POST /mcp.
    """

    def __init__(self, state_path: str = ""):
        self._state_path = state_path

        # Конфиг Drain3: настраиваем под логи безопасности
        config = TemplateMinerConfig()
        config.drain_depth = 4           # Глубина парсинга (4-5 для логов безопасности)
        config.drain_sim_th = 0.4        # Порог сходства
        config.drain_max_clusters = 500  # Максимум кластеров
        self._miner = TemplateMiner(config=config)

        # Загружаем сохранённое состояние если есть
        if state_path and os.path.exists(state_path):
            try:
                self._miner.load_state()
                logger.info("Loaded state from %s (%d clusters)",
                            state_path, len(self._miner.drain.clusters))
            except Exception as e:
                logger.warning("Failed to load state: %s", e)

        logger.info("Drain3 MCP server initialized (depth=%d, sim_threshold=%.2f)",
                    config.drain_depth, config.drain_sim_th)

    # ---- MCP инструменты ----

    async def _handle_parse(self, params: dict) -> dict:
        """parse_log — разобрать один лог в шаблон"""
        log_message = params.get("log_message", "").strip()
        if not log_message:
            return {"error": "log_message is required"}

        result = self._miner.add_log_message(log_message)

        return {
            "log_message": log_message[:200],
            "template": result.get("template_mined") or "NO_TEMPLATE",
            "cluster_id": result.get("cluster_id"),
            "cluster_size": result.get("cluster_size", 1),
            "is_new": result.get("change_type") == "cluster_created",
            "params": self._miner.extract_parameters(
                result.get("template_mined", ""), log_message
            ),
        }

    async def _handle_match(self, params: dict) -> dict:
        """match — найти подходящий шаблон (без добавления)"""
        log_message = params.get("log_message", "").strip()
        if not log_message:
            return {"error": "log_message is required"}

        cluster = self._miner.match(log_message)
        if cluster:
            return {
                "matched": True,
                "cluster_id": cluster.cluster_id,
                "template": cluster.get_template(),
                "cluster_size": cluster.size,
            }
        return {"matched": False, "template": None}

    async def _handle_clusters(self, params: dict) -> dict:
        """get_clusters — список всех шаблонов"""
        limit = params.get("limit", 50)
        clusters = sorted(
            self._miner.drain.clusters,
            key=lambda c: c.size,
            reverse=True,
        )[:limit]

        return {
            "total_clusters": len(self._miner.drain.clusters),
            "clusters": [
                {
                    "cluster_id": c.cluster_id,
                    "size": c.size,
                    "template": c.get_template(),
                }
                for c in clusters
            ],
        }

    async def _handle_stats(self, params: dict) -> dict:
        """get_stats — статистика парсинга"""
        clusters = self._miner.drain.clusters
        total_logs = sum(c.size for c in clusters)

        return {
            "total_clusters": len(clusters),
            "total_logs_parsed": total_logs,
            "state_path": self._state_path or "not saved",
            "config": {
                "depth": self._miner.config.drain_depth,
                "similarity_threshold": self._miner.config.drain_sim_th,
                "max_clusters": self._miner.config.drain_max_clusters,
            },
        }

    async def _handle_save(self, params: dict) -> dict:
        """save_state — сохранить состояние"""
        if not self._state_path:
            return {"error": "No state path configured"}

        self._miner.save_state("manual_save")
        logger.info("State saved to %s", self._state_path)
        return {"saved": True, "path": self._state_path}

    async def handle_request(self, request: web.Request) -> web.Response:
        """Обработка JSON-RPC запроса"""
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
                status=400,
            )

        msg_id = body.get("id", None)
        method = body.get("method", "")

        # MCP protocol: tools/list
        if method == "tools/list":
            return web.json_response({
                "jsonrpc": "2.0",
                "result": {
                    "tools": [
                        {
                            "name": "parse_log",
                            "description": "Разобрать лог-сообщение в шаблон (Drain3)",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "log_message": {
                                        "type": "string",
                                        "description": "Лог-сообщение для парсинга"
                                    }
                                },
                                "required": ["log_message"]
                            }
                        },
                        {
                            "name": "match_log",
                            "description": "Найти шаблон для лога без добавления",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "log_message": {
                                        "type": "string",
                                        "description": "Лог-сообщение"
                                    }
                                },
                                "required": ["log_message"]
                            }
                        },
                        {
                            "name": "get_clusters",
                            "description": "Список всех найденных шаблонов логов",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "limit": {
                                        "type": "integer",
                                        "description": "Максимум шаблонов",
                                        "default": 50
                                    }
                                }
                            }
                        },
                        {
                            "name": "get_stats",
                            "description": "Статистика парсинга логов",
                            "inputSchema": {"type": "object", "properties": {}}
                        },
                        {
                            "name": "save_state",
                            "description": "Сохранить состояние Drain3 на диск",
                            "inputSchema": {"type": "object", "properties": {}}
                        },
                    ]
                },
                "id": msg_id,
            })

        # MCP protocol: tools/call
        if method == "tools/call":
            params = body.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            handlers = {
                "parse_log": self._handle_parse,
                "match_log": self._handle_match,
                "get_clusters": self._handle_clusters,
                "get_stats": self._handle_stats,
                "save_state": self._handle_save,
            }

            handler = handlers.get(tool_name)
            if not handler:
                return web.json_response(
                    {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Tool not found: {tool_name}"}, "id": msg_id},
                    status=404,
                )

            try:
                result = await handler(tool_args)
                return web.json_response({
                    "jsonrpc": "2.0",
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}
                        ]
                    },
                    "id": msg_id,
                })
            except Exception as e:
                logger.exception("Tool call failed: %s", tool_name)
                return web.json_response(
                    {"jsonrpc": "2.0", "error": {"code": -32000, "message": str(e)}, "id": msg_id},
                    status=500,
                )

        # Health check (GET /mcp)
        if method == "health":
            return web.json_response({"status": "ok", "service": "drain3-mcp"})

        return web.json_response(
            {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Method not found: {method}"}, "id": msg_id},
            status=404,
        )


async def main():
    parser = argparse.ArgumentParser(description="Drain3 MCP Server")
    parser.add_argument("--port", type=int, default=3001, help="TCP port")
    parser.add_argument("--state", type=str, default="", help="Path to state file")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
    args = parser.parse_args()

    server = Drain3MCPServer(state_path=args.state)
    app = web.Application()
    app.router.add_post("/mcp", server.handle_request)
    app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))

    logger.info("Drain3 MCP server starting on %s:%s", args.host, args.port)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
