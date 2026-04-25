#!/usr/bin/env python3
"""
MCP Client — асинхронный клиент для подключения к MCP-серверу.
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List

import aiohttp
from aiohttp import ClientTimeout

from services.exceptions import (
    MCPConnectionError,
    MCPToolNotFoundError,
    MCPToolCallError,
    MCPTimeoutError,
)

logger = logging.getLogger(__name__)


class MCPClient:
    """Клиент для подключения к MCP-серверу"""

    def __init__(self, server_url: str, name: str = "mcp"):
        self.server_url = server_url.rstrip('/')
        self.name = name
        self._session: Optional[aiohttp.ClientSession] = None
        self._tools: List[Dict[str, Any]] = []
        self._initialized = False

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Создаёт сессию если её нет"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def connect(self) -> bool:
        """Подключение к MCP серверу с обработкой ошибок"""
        try:
            session = await self._ensure_session()
            async with session.get(
                f"{self.server_url}/health",
                timeout=ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    logger.info(f"Подключен к MCP серверу: {self.server_url}")
                    self._initialized = True
                    await self._load_tools()
                    return True
                raise MCPConnectionError(
                    self.server_url,
                    Exception(f"HTTP {resp.status}"),
                )
        except asyncio.TimeoutError:
            logger.warning(f"MCP сервер {self.server_url} не отвечает (таймаут 5с)")
        except aiohttp.ClientError as e:
            logger.warning(f"MCP сервер {self.server_url} недоступен: {e}")
        except MCPConnectionError:
            raise
        except Exception as e:
            logger.warning(f"MCP сервер {self.server_url} ошибка: {e}")
        finally:
            if not self._initialized:
                await self.close()

        self._initialized = False
        return False

    async def _load_tools(self):
        """Загрузка списка инструментов с MCP-сервера"""
        try:
            session = await self._ensure_session()
            async with session.get(
                f"{self.server_url}/tools",
                timeout=ClientTimeout(total=10)
            ) as resp:
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

    async def call_tool(
        self,
        tool_name: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Вызов инструмента MCP с детальной обработкой ошибок"""
        if not self._initialized:
            raise MCPConnectionError(
                self.server_url,
                Exception("Клиент не инициализирован"),
            )

        # Проверяем, что инструмент существует
        tool_names = self.get_tool_names()
        if tool_names and tool_name not in tool_names:
            raise MCPToolNotFoundError(tool_name, self.name)

        try:
            session = await self._ensure_session()
            payload = {
                "tool": tool_name,
                "parameters": parameters or {},
            }

            async with session.post(
                f"{self.server_url}/call",
                json=payload,
                timeout=ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

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
