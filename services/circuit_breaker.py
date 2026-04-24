#!/usr/bin/env python3
"""
Circuit Breaker + Retry с exponential backoff для MCP запросов.
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Callable, Awaitable, TypeVar, Optional

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from config.settings import get_config

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Предохранитель для MCP сервера.
    Если сервер упал — не бомбардируем его запросами, ждём.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: Optional[int] = None,
        reset_timeout: Optional[float] = None,
    ):
        cfg = get_config().mcp
        self.name = name
        self._failure_threshold = failure_threshold or cfg.circuit_breaker_threshold
        self._reset_timeout = reset_timeout or cfg.circuit_breaker_reset_seconds
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_open_time: float = 0.0
        self._lock = asyncio.Lock()
        self._total_failures = 0
        self._total_successes = 0

    @property
    def state(self) -> CircuitState:
        return self._state

    async def call(self, coro_factory: Callable[[], Awaitable[T]]) -> T:
        await self._check_state()

        if self._state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(
                f"Circuit breaker OPEN для {self.name}"
            )

        try:
            result = await coro_factory()
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        self._total_successes += 1
        if self._state == CircuitState.HALF_OPEN:
            logger.info(f"Half-open success для {self.name}")
            self._state = CircuitState.CLOSED
            self._failure_count = 0

    def _on_failure(self):
        self._total_failures += 1
        self._failure_count += 1
        if self._failure_count >= self._failure_threshold:
            logger.warning(f"Circuit breaker OPEN для {self.name}")
            self._state = CircuitState.OPEN
            self._last_open_time = time.time()

    async def _check_state(self):
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_open_time >= self._reset_timeout:
                logger.info(f"Пробуем half-open для {self.name}")
                self._state = CircuitState.HALF_OPEN

    def reset(self):
        self._state = CircuitState.CLOSED
        self._failure_count = 0

    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.value,
            "failures": self._total_failures,
            "successes": self._total_successes,
        }


class CircuitBreakerOpenError(Exception):
    pass


def mcp_retry_decorator():
    """Retry с exponential backoff для MCP запросов."""
    cfg = get_config().mcp

    return retry(
        stop=stop_after_attempt(cfg.max_retries),
        wait=wait_exponential(
            multiplier=cfg.retry_backoff_base,
            max=cfg.retry_max_delay,
        ),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
