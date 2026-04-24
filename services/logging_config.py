#!/usr/bin/env python3
"""
Структурированное логирование для продакшена через structlog.
"""

import logging
import sys
from typing import Optional

import structlog
from structlog.processors import JSONRenderer, TimeStamper
from structlog.dev import ConsoleRenderer

from config.settings import get_config


def configure_logging(env: Optional[str] = None) -> None:
    """
    Настройка структурированного логирования.
    
    Args:
        env: Окружение: "dev" | "prod" | None (из LOG_LEVEL)
    """
    cfg = get_config().http
    log_level = env or cfg.log_level

    if log_level == "DEV" or log_level == "dev":
        # Разработка: цветной вывод в консоль
        processors = [
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # Продакшен: JSON в stdout
        processors = [
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Настройка стандартного logging
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Получить structlog логгер.
    
    Args:
        name: Имя логгера (обычно __name__)
        
    Returns:
        BoundLogger из structlog
    """
    return structlog.get_logger(name)
