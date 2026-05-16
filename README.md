# 🤖 SOC AI Agent v10

Кастомный SOC AI Agent с мульти-агентной архитектурой, интеграцией **MCP-серверов** (Wazuh, Drain3), **LLM (DeepSeek/Ollama)**, **Drain3 (парсинг логов)**, **Circuit Breaker**, **Rate Limiter**, **Prompt Injection защитой**, **асинхронной памятью (aiosqlite)** и **CI/CD**.

```
104/104 тестов  ✅  mypy 0 errors  ✅  ruff 0 errors  ✅
```

---

## 🚀 Быстрый старт

```bash
# 1. Зависимости
uv sync --dev

# 2. Настроить .env
cp config/.env.default config/.env
# Отредактировать config/.env — добавить DEEPSEEK_API_KEY (опционально)

# 3. Запустить тесты
uv run pytest tests/ -v

# 4. Запустить API сервер
python api_server_v3.py

# Или через Docker
docker build -t soc-agent-v10 .
docker run -p 8080:8080 --env-file config/.env soc-agent-v10
```

---

## 🧪 Тесты

```bash
uv run pytest tests/ -v --tb=short    # 82 теста
uv run pytest tests/ -q               # только счётчик
uv run pytest tests/test_mcp_client.py -v  # MCPClient (19 тестов)
```

| Файл | Тестов | Что проверяет |
|------|--------|---------------|
| `tests/test_agent.py` | 16 | CircuitBreaker, RateLimiter, PromptInjection, LocalMemory (async), модели |
| `tests/test_agents.py` | 35 | TriageAgent, InvestigatorAgent, ResponderAgent, ReporterAgent, Orchestrator, `_call_mcp()` |
| `tests/test_mcp_client.py` | 19 | Подключение, вызов инструментов, ошибки, таймауты |
| `tests/test_integration.py` | 12 | LLM fallback, MCP exceptions, интеграционные сценарии |
| `tests/test_drain3_server.py` | 15 | Drain3 парсинг логов, кластеры, match, MCP-протокол |

---

## 👷 CI/CD

На каждый push/PR в `main` — GitHub Actions:

```
ruff check .   →   mypy .   →   pytest tests/
```

---

## 🏗 Архитектура

```
ai_simple_soc/
├── api_server_v3.py             # REST API (aiohttp)
├── llm_agent.py                 # Интеграция с DeepSeek/Ollama
├── agents/
│   ├── base_agent.py            # BaseAgent, AgentContext, _call_mcp()
│   ├── triage_agent.py          # TriageAgent — быстрая классификация
│   ├── investigator_agent.py    # InvestigatorAgent — глубокий анализ
│   ├── responder_agent.py       # ResponderAgent — действия с одобрением
│   ├── reporter_agent.py        # ReporterAgent — отчёты и compliance
│   └── orchestrator.py          # Orchestrator — маршрутизация + memory
├── config/
│   ├── settings.py              # Pydantic Settings (MCP, LLM, Redis, Memory...)
│   └── .env.default             # Шаблон переменных окружения
├── services/
│   ├── exceptions.py            # Иерархия исключений (8 классов)
│   ├── mcp_client.py            # Клиент MCP (JSON-RPC, aiohttp)
│   ├── circuit_breaker.py       # Circuit Breaker (3 состояния)
│   ├── rate_limiter.py          # Rate Limiter (Token Bucket)
│   ├── llm_orchestrator.py      # LLM-оркестратор + async memory
│   ├── drain3_server.py         # Drain3 MCP — парсинг логов в шаблоны
│   └── logging_config.py        # structlog (JSON/цветной вывод)
├── middleware/
│   └── prompt_injection.py      # Защита от prompt injection
├── models/
│   └── request_models.py        # Pydantic модели запросов
├── memory/
│   └── local_memory.py          # Async SQLite (aiosqlite)
├── .ai_memory/
│   ├── 00-project-core.md       # Ядро, принципы, границы
│   ├── 10-architecture.md       # Архитектура (этот файл)
│   ├── 20-tech-stack.md         # Стек технологий
│   ├── 30-coding-conventions.md # Стандарты кода
│   ├── 40-mcp-contracts.md      # MCP контракты
│   ├── 50-lessons-learned.md    # ADR и уроки
│   ├── 60-errors-log.md         # Журнал ошибок
│   ├── 70-state.md              # Текущее состояние
│   ├── 80-changelog.md          # История изменений
│   └── 90-roadmap.md            # Планы
├── tests/
│   ├── test_agent.py            # Юнит-тесты (CB, RateLimiter, LocalMemory async, модели)
│   ├── test_agents.py           # Агенты, Orchestrator, _call_mcp()
│   ├── test_mcp_client.py       # MCP клиент (aioresponses)
│   ├── test_drain3_server.py    # Drain3 парсинг (15 тестов)
│   └── test_integration.py      # Интеграционные сценарии
├── TechRadar/
│   └── techradar.md             # Технологический радар
├── .github/workflows/
│   └── ci.yml                   # CI/CD (ruff → mypy → pytest)
├── Dockerfile                   # Docker-образ
├── DeepPlan.md                  # Стратегический план разработки
└── pyproject.toml               # Зависимости и конфигурация
```

### Маршрутизация запросов

```
Запрос → TriageAgent (классификация, confidence > 0.7)
            │
            ├── AGENT_STATUS       → TriageAgent (без LLM)
            ├── ALERT_TRIAGE       → TriageAgent
            ├── THREAT_HUNTING     → InvestigatorAgent (+ LLM)
            ├── VULNERABILITY      → InvestigatorAgent
            ├── ACTIVE_RESPONSE    → ResponderAgent (approval workflow)
            ├── INCIDENT_RESPONSE  → ResponderAgent
            ├── REPORT_GENERATION  → ReporterAgent
            ├── COMPLIANCE_CHECK   → ReporterAgent
            └── GENERAL_QUERY      → TriageAgent (fallback)
```

Если TriageAgent неуверен (< 0.7) → подключается LLM для уточнения.
Агенты хранят контекст в LocalMemory (async aiosqlite), автоматически подвязывается через AgentContext.

---

## 🔌 MCP-инструменты

Поддерживаются MCP-серверы (JSON-RPC 2.0 через POST /mcp):

1. **Wazuh-MCP** (`:3000`) — 48 инструментов: алерты, агенты, уязвимости, SCA, статистика
2. **Own-MCP** (`:8000`) — кастомные инструменты (IOC, compliance, risk assessment)
3. **Drain3-MCP** (`:3001`) — парсинг логов: `parse_log`, `match_log`, `get_clusters`, `get_stats`

```bash
# Проверить подключение MCP
curl http://localhost:8080/health

# Список инструментов
curl http://localhost:8080/tools
```

---

## 🔧 Конфигурация

Все параметры задаются через переменные окружения или `.env`:

| Префикс | Назначение | Ключевые переменные |
|---------|-----------|---------------------|
| `MCP_*` | MCP-сервера | `MCP_URL`, `MCP_OWN_URL`, `MCP_CIRCUIT_BREAKER_THRESHOLD` |
| `LLM_*` | DeepSeek/Ollama | `LLM_API_KEY`, `LLM_MODEL`, `LLM_DEEPSEEK_BASE_URL` |
| `HTTP_*` | API сервер | `HTTP_HOST`, `HTTP_PORT` |
| `REDIS_*` | Redis Stream | `REDIS_HOST`, `REDIS_PORT`, `REDIS_STREAM_NAME` |
| `MEMORY_*` | Память (aiosqlite) | `MEMORY_DB_PATH`, `MEMORY_MAX_EPISODES`, `MEMORY_DECAY_RATE` |
| `SEC_*` | Безопасность | `SEC_MAX_QUERY_LENGTH`, `SEC_AUDIT_LOG_ENABLED` |
| `AGENT_*` | Агент | `AGENT_DIALOG_MAX_TURNS`, `AGENT_TOOL_CACHE_TTL_SECONDS` |

---

## 🛡️ Обработка ошибок

```
SOCAgentError
├── MCPConnectionError     — сервер недоступен (→ 502)
├── MCPToolNotFoundError   — инструмент не найден (→ 400)
├── MCPToolCallError       — HTTP ошибка при вызове (→ 502)
├── MCPTimeoutError        — таймаут (→ 504)
├── LLMAnalysisError       — ошибка LLM (→ 503)
└── QueryValidationError   — некорректный запрос (→ 400)

Прочие:
  JSONDecodeError  → 400
  TimeoutError     → 504
  Internal error   → 500
```

---

## 📊 Логирование

```bash
# Разработка — цветной structlog (по умолч.)
python api_server_v3.py

# Продакшен
API_HOST=0.0.0.0 API_PORT=8080 python api_server_v3.py
```

---

## 🧠 Tech Radar

Полный технологический радар проекта — [TechRadar/techradar.md](TechRadar/techradar.md).

---

## 📋 Эндпоинты API

| Метод | Путь | Описание |
|-------|------|----------|
| `GET`  | `/health` | Healthcheck (статус MCP, LLM, агентов) |
| `GET`  | `/tools` | Список инструментов MCP |
| `GET`  | `/agents` | Список агентов |
| `GET`  | `/approvals` | Очередь подтверждений действий |
| `POST` | `/approve/{id}` | Подтвердить действие |
| `POST` | `/query` | Обработать запрос (JSON → агент → ответ) |
| `POST` | `/chat` | Чат с поддержкой SSE |
| `GET`  | `/` | Web UI |

---

## 📄 Лицензия

MIT
