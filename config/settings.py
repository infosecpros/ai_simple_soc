#!/usr/bin/env python3
"""
Pydantic Settings — централизованная конфигурация с валидацией.
"""

import os
from typing import Optional, List, Literal
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPConnectionSettings(BaseSettings):
    # Поля читаются из env: MCP_URL, MCP_OWN_URL, MCP_CONNECT_TIMEOUT, ...
    url: str = "http://127.0.0.1:3000/mcp"
    own_url: str = "http://127.0.0.1:8000/mcp"
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    max_retries: int = 3
    retry_backoff_base: float = 1.5
    retry_max_delay: float = 30.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_seconds: float = 60.0

    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


class LLMSettings(BaseSettings):
    api_key: Optional[SecretStr] = None  # LLM_API_KEY
    model: str = "deepseek-chat"           # LLM_MODEL
    api_base: str = "https://api.deepseek.com/v1"
    temperature: float = 0.1
    max_tokens: int = 4096
    cache_size: int = 50
    timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


class HTTPSettings(BaseSettings):
    host: str = "0.0.0.0"       # HTTP_HOST
    port: int = 8080             # HTTP_PORT
    log_level: str = "INFO"     # HTTP_LOG_LEVEL
    rate_limit_per_minute: int = 60
    cors_origins: List[str] = ["*"]

    model_config = SettingsConfigDict(
        env_prefix="HTTP_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


class MemorySettings(BaseSettings):
    engine: Literal["local", "alaya", "none"] = "local"
    db_path: str = "~/.soc_agent/memory.db"
    max_episodes: int = 500
    cache_ttl: float = 30.0
    enable_forgetting: bool = True
    decay_rate: float = 0.1
    retrieval_top_k: int = 10

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )


class SecuritySettings(BaseSettings):
    active_response_require_confirmation: bool = True
    active_response_require_2fa: bool = True
    audit_log_enabled: bool = True
    audit_log_path: str = "~/.soc_agent/audit.log"
    max_query_length: int = 4096
    prompt_injection_check: bool = True
    token_rotation_days: int = 30

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
