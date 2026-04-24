# 🤖 SOC AI Agent v10

Кастомный SOC AI Agent с интеграцией **MCP-серверов**, **LLM (DeepSeek)**, **Circuit Breaker**, **контекстом диалога** и **структурированным логированием**.

## 🚀 Быстрый старт

```bash
# 1. Клонировать
git clone <repo-url> && cd soc-agent-v10

# 2. Установить зависимости
uv venv && source .venv/bin/activate
uv sync

# 3. Настроить .env
cp config/.env.default config/.env
# Отредактировать config/.env — добавить DEEPSEEK_API_KEY

# 4. Запустить
python soc_agent_v3.py
```

## 🧪 Тесты

```bash
uv run pytest tests/ -v
# 32 теста, все проходят ✅
```

## 📁 Архитектура

```
agnet_v3/
├── soc_agent_v3.py          # Основной агент + MCP-клиент
├── llm_agent.py             # Интеграция с DeepSeek через pydantic-ai
├── api_server_v3.py         # HTTP API (FastAPI)
├── config/
│   ├── settings.py          # Pydantic Settings (валидация через env)
│   └── .env.default         # Шаблон переменных окружения
├── services/
│   ├── exceptions.py        # Кастомные исключения (8 классов)
│   ├── circuit_breaker.py   # Circuit Breaker (3 состояния)
│   ├── rate_limiter.py      # Rate Limiter (Token Bucket)
│   └── logging_config.py    # structlog (JSON/цветной вывод)
├── middleware/
│   └── prompt_injection.py  # Защита от prompt injection
├── models/
│   └── request_models.py    # Pydantic модели запросов
├── memory/
│   └── local_memory.py      # Локальное хранилище (SQLite)
└── tests/
    ├── test_agent.py        # 20 юнит-тестов
    └── test_integration.py  # 12 интеграционных тестов
```

## 🔧 Конфигурация

Все параметры задаются через переменные окружения или `.env`:

| Префикс | Назначение | Пример |
|---------|-----------|--------|
| `MCP_*` | MCP-сервера | `MCP_URL`, `MCP_CONNECT_TIMEOUT` |
| `LLM_*` | DeepSeek | `LLM_API_KEY`, `LLM_MODEL` |
| `HTTP_*` | API сервер | `HTTP_PORT`, `HTTP_LOG_LEVEL` |
| `MEMORY_*` | Память | `MEMORY_DB_PATH`, `MEMORY_MAX_EPISODES` |
| `SEC_*` | Безопасность | `SEC_MAX_QUERY_LENGTH`, `SEC_AUDIT_LOG_ENABLED` |
| `AGENT_*` | Агент | `AGENT_DIALOG_MAX_TURNS`, `AGENT_TOOL_CACHE_TTL_SECONDS` |

## 🔌 MCP-инструменты

Агент подключается к двум MCP-серверам:

1. **Wazuh-MCP** (`:3000`) — Wazuh API (алерты, агенты, уязвимости, SCA)
2. **Own-MCP** (`:8000`) — кастомные инструменты (IOC, compliance, risk assessment)

## 🛡️ Обработка ошибок

```
SOCAgentError
├── MCPConnectionError     — сервер недоступен
├── MCPToolNotFoundError   — инструмент не найден
├── MCPToolCallError       — HTTP ошибка при вызове
├── MCPTimeoutError        — таймаут
├── LLMAnalysisError       — ошибка LLM
└── QueryValidationError   — некорректный запрос
```

## 📊 Логирование

```bash
# Разработка — цветной вывод
python soc_agent_v3.py

# Продакшен — JSON в stdout
HTTP_LOG_LEVEL=prod python soc_agent_v3.py
```

## 📋 История версий

| Коммит | Описание |
|--------|----------|
| `83a0c11` | Полный рефакторинг: компонентная архитектура, CircuitBreaker, RateLimiter, pydantic-settings |
| `02a3a3e` | Кастомные исключения, structlog, магические числа в константы, Generic типизация |
| `25a3cab` | CircuitBreaker в MCP, resp.raise_for_status(), AgentSettings, 12 интеграционных тестов |
| `45ac750` | pyproject.toml с зависимостями |

## 📄 Лицензия

MIT
