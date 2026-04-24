#!/usr/bin/env python3
"""
Pydantic Settings — централизованная конфигурация с валидацией.
"""

import os
from typing import Optional, List, Literal
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


# --- Магические числа, вынесенные в константы ---
_DEFAULT_MCP_CONNECT_TIMEOUT = 5.0
_DEFAULT_MCP_READ_TIMEOUT = 30.0
_DEFAULT_MCP_MAX_RETRIES = 3
_DEFAULT_MCP_RETRY_BACKOFF = 1.5
_DEFAULT_MCP_RETRY_MAX_DELAY = 30.0
_DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 5
_DEFAULT_CIRCUIT_BREAKER_RESET = 60.0

_DEFAULT_LLM_TEMPERATURE = 0.1
_DEFAULT_LLM_MAX_TOKENS = 4096
_DEFAULT_LLM_CACHE_SIZE = 50
_DEFAULT_LLM_TIMEOUT = 30.0

_DEFAULT_HTTP_PORT = 8080
_DEFAULT_RATE_LIMIT = 60
_DEFAULT_LOG_LEVEL = "INFO"

_DEFAULT_MEMORY_ENGINE = "local"
_DEFAULT_MEMORY_DB = "~/.soc_agent/memory.db"
_DEFAULT_MEMORY_MAX_EPISODES = 500
_DEFAULT_MEMORY_TTL = 30.0
_DEFAULT_MEMORY_DECAY = 0.1
_DEFAULT_MEMORY_TOP_K = 10

_DEFAULT_SEC_QUERY_LENGTH = 4096
_DEFAULT_SEC_TOKEN_ROTATION = 30

# --- Настройки ---

class MCPConnectionSettings(BaseSettings):
    """Настройки подключения к MCP серверу"""
    url: str = "http://127.0.0.1:3000/mcp"
    own_url: str = "http://127.0.0.1:8000/mcp"
    connect_timeout: float = _DEFAULT_MCP_CONNECT_TIMEOUT
    read_timeout: float = _DEFAULT_MCP_READ_TIMEOUT
    max_retries: int = _DEFAULT_MCP_MAX_RETRIES
    retry_backoff_base: float = _DEFAULT_MCP_RETRY_BACKOFF
    retry_max_delay: float = _DEFAULT_MCP_RETRY_MAX_DELAY
    circuit_breaker_threshold: int = _DEFAULT_CIRCUIT_BREAKER_THRESHOLD
    circuit_breaker_reset_seconds: float = _DEFAULT_CIRCUIT_BREAKER_RESET

    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


class LLMSettings(BaseSettings):
    """Настройки LLM"""
    api_key: Optional[SecretStr] = None  # LLM_API_KEY
    model: str = "deepseek-chat"           # LLM_MODEL
    api_base: str = "https://api.deepseek.com/v1"
    temperature: float = _DEFAULT_LLM_TEMPERATURE
    max_tokens: int = _DEFAULT_LLM_MAX_TOKENS
    cache_size: int = _DEFAULT_LLM_CACHE_SIZE
    timeout_seconds: float = _DEFAULT_LLM_TIMEOUT

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


class HTTPSettings(BaseSettings):
    """Настройки HTTP сервера"""
    host: str = "0.0.0.0"       # HTTP_HOST
    port: int = _DEFAULT_HTTP_PORT
    log_level: str = _DEFAULT_LOG_LEVEL
    rate_limit_per_minute: int = _DEFAULT_RATE_LIMIT
    cors_origins: List[str] = ["*"]

    model_config = SettingsConfigDict(
        env_prefix="HTTP_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


class MemorySettings(BaseSettings):
    """Настройки памяти"""
    engine: Literal["local", "alaya", "none"] = _DEFAULT_MEMORY_ENGINE
    db_path: str = _DEFAULT_MEMORY_DB
    max_episodes: int = _DEFAULT_MEMORY_MAX_EPISODES
    cache_ttl: float = _DEFAULT_MEMORY_TTL
    enable_forgetting: bool = True
    decay_rate: float = _DEFAULT_MEMORY_DECAY
    retrieval_top_k: int = _DEFAULT_MEMORY_TOP_K

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


class SecuritySettings(BaseSettings):
    """Настройки безопасности"""
    active_response_require_confirmation: bool = True
    active_response_require_2fa: bool = True
    audit_log_enabled: bool = True
    audit_log_path: str = _DEFAULT_MEMORY_DB.rsplit("memory", 1)[0] + "audit.log"
    max_query_length: int = _DEFAULT_SEC_QUERY_LENGTH
    prompt_injection_check: bool = True
    token_rotation_days: int = _DEFAULT_SEC_TOKEN_ROTATION

    model_config = SettingsConfigDict(
        env_prefix="SEC_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


class AppConfig(BaseSettings):
    """Агрегированная конфигурация приложения"""

    mcp: MCPConnectionSettings = MCPConnectionSettings()
    llm: LLMSettings = LLMSettings()
    http: HTTPSettings = HTTPSettings()
    memory: MemorySettings = MemorySettings()
    security: SecuritySettings = SecuritySettings()

    @classmethod
    def load(cls) -> "AppConfig":
        env_file = os.path.join(os.path.dirname(__file__), ".env")
        if not os.path.exists(env_file):
            cls._create_default_env(env_file)
        return cls()

    @staticmethod
    def _create_default_env(path: str):
        import shutil
        src = os.path.join(os.path.dirname(__file__), ".env.default")
        if os.path.exists(src):
            shutil.copy2(src, path)
            print(f"[config] ✅ Скопирован шаблон .env (из .env.default)")
        else:
            with open(path, "w") as f:
                f.write("# SOC AI Agent v10 — скопируйте .env.default и настройте\n")
            print(f"[config] ✅ Создан пустой .env")


_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig.load()
    return _config
