# План развития AI SOC

Основан на анализе текущего кода (`ai_simple_soc`) и Build-vs-Buy-оценке (см. таблицу ниже).  
Адаптирован под ресурсы стартапа (1–2 разработчика).

**Ключевые решения:**
– **Build vs Buy:** 6 компонентов пишем (тонкие обёртки, <400 строк), 12 берём готовыми
– Kafka отложена до >500 EPS **или** появления качественных триггеров (exactly-once, Schema Registry, DLQ), Redis Streams на старте
– Одна модель LogBERT + RAG Llama3 вместо мульти-модельного ансамбля
– Ollama (CPU) для MVP, vLLM — когда >100 параллельных запросов
– Workflow сначала в Orchestrator (Python), n8n — когда появятся внешние сервисы
– Существующий Circuit Breaker **не заменяем** (встроен в архитектуру, 35 тестов), `tenacity` добавляем для HTTP-retry, `aiolimiter` для глобальных лимитов
– Ollama SDK (5 строк), не `litellm` — до появления 3+ провайдеров
– Compliance: Wazuh SCA + OpenSCAP/Lynis MCP (разделить), не только SCA
– `guardrails-ai` не тащим (2GB, transformers), вместо него Pydantic strict + ToolCallAuthorizer + OutputValidator
– Celery не используется — asyncio + aiohttp для всех воркеров
– Security-by-Design с фазы 1, не откладывать аудит
– GTM / РФ-локализация — с фазы 2, не опционально

---

## 🏗 Уже готово (репозиторий `ai_simple_soc`)

### Что работает сейчас

- **MCP-клиент** — JSON-RPC через aiohttp, таймауты, Circuit Breaker
- **Мульти-агентная архитектура** — Orchestrator + TriageAgent / InvestigatorAgent / ResponderAgent / ReporterAgent
- **Circuit Breaker** — 3 состояния, порог сбоев, reset timeout (кастомный, **не заменяем**)
- **Rate Limiter** — Token Bucket (кастомный, **не заменяем**)
- **Prompt Injection защита** — regex + rule-based фильтрация на входе
- **Иерархия исключений** — 8 классов, маппинг на HTTP статусы
- **88 тестов** — pytest + pytest-asyncio + aioresponses
- **CI/CD** — GitHub Actions (ruff → mypy → pytest)
- **Docker** — multi-stage Dockerfile
- **Технологический радар** — `TechRadar/techradar.md`

---

## 📦 Build vs Buy (итоговая таблица)

| Компонент | Решение | Стек | Дней | Спринт |
|-----------|---------|------|------|--------|
| **Redis Streams Consumer** | WRAP (тонко) | `redis.asyncio` + `aiosqlite` (идемпотентность) | 1 | 1 |
| **Drain3 (парсер шаблонов)** | BUY (библиотека) | `drain3` PyPI + aiohttp/MCP-обёртка (50 строк) | 0.5 | 1 |
| **LogBERT Inference** | BUILD (тонкий) | `onnxruntime` + aiohttp + батчинг (asyncio.Semaphore) | 2 | 2 |
| **Embeddings** | BUY (библиотека) | `sentence-transformers` (all-MiniLM-L6-v2) | 0.5 | 2 |
| **LLM Adapter (Ollama)** | BUY (SDK) | `ollama` SDK (5 строк), не `litellm` | 0.5 | 2 |
| **Model Registry** | BUILD (лёгкий) | JSON-конфиг + asyncio.Queue + fallback-цепочки. **Без `watchdog`/hot-reload** (MVP) | 0.5 | 2 |
| **Active Learning Queue** | BUILD (логика) | Redis List (очередь) + SQLite (хранение меток), uncertainty sampling | 1 | 3 |
| **Compliance / SCA** | BUY + ADAPT | Wazuh SCA + OpenSCAP/Lynis MCP + YAML-маппинг | 2 | 3 |
| **Security Guards** | WRAP (лёгкий) | Pydantic strict + `ToolCallAuthorizer` + `OutputValidator`. **Не `guardrails-ai`** | 0.5 | 1 |
| **Metrics & Logging** | BUY | `prometheus-client` + `structlog` (уже есть) | 0.5 | 1 |
| **Tracing** | BUY (позже) | OpenTelemetry auto-instrumentation (после спринта 3) | 1 | 4 |
| **Async SQLite** | BUY (замена) | `aiosqlite` (только LocalMemory, 1 процесс) | 0.5 | 1 |
| **HTTP Retry** | BUY (добавить) | `tenacity` для MCPClient (низкий уровень, не замена CB) | 0.5 | 1 |
| **Global Rate Limiter** | BUY (добавить) | `aiolimiter` для API (верхний уровень) | 0.5 | 2 |
| **Orchestrator / Agents** | BUILD (на своём) | Текущий Orchestrator + asyncio + MCP-роутинг | рефакторинг | 1 |
| **n8n / Workflow** | HOLD → BUY | Встроенный Python → n8n (фаза 2+) | — | 4+ |

**Итого:** 6 компонентов BUILD/WRAP (~5 дней), 10 BUY (~4 дня).  
**Спринт 1 = 5 дней, Спринт 2 = 4 дня, Спринт 3 = 3 дня.**  
Оставшееся время — ожидание данных (baseline LogBERT, legitimate трафик).

---

## 📅 Спринт 1: Фундамент (Дни 1–5)

**Цель:** закрыть техдолг, baseline LogBERT, подготовить инфраструктуру для real-time конвейера.

### День 1–2: Техдолг + Zero-shot baseline

| Задача | Тип | Код | Тесты |
|--------|-----|-----|-------|
| `BaseAgent._call_mcp()` — вынести общий вызов MCP из 4 агентов | REFACTOR | `agents/base_agent.py` — новый метод | 5 тестов |
| `aiosqlite` — замена sqlite3 в LocalMemory | REFACTOR | `memory/local_memory.py` | Все тесты LocalMemory (4 шт) |
| Прикрутить LocalMemory к Orchestrator | REFACTOR | `agents/orchestrator.py` — `context.memory = get_memory()` | 2 теста |
| Zero-shot baseline LogBERT | DATA | Скрипт `scripts/zero_shot_baseline.py` — 500 логов → F1 | 1 тест (заглушка) |

**Критерий готовности:** 0 асинхронных блокировок, LocalMemory доступна из агентов, отчёт с F1.

### День 3–4: Security + Compliance (начало)

| Задача | Тип | Код | Тесты |
|--------|-----|-----|-------|
| Pydantic strict mode — включить везде | CONFIG | `models/request_models.py` — `model_config = {"strict": True}` | Все тесты моделей |
| `ToolCallAuthorizer` — белый список инструментов | BUILD | `services/authorizer.py` — 30 строк | 3 теста |
| `OutputValidator` — проверка схем MCP-ответов | BUILD | `services/output_validator.py` — 30 строк | 3 теста |
| Audit-log: хэширование tool_calls + запись в SQLite | BUILD | `services/audit_logger.py` — 40 строк | 3 теста |
| Compliance-репозиторий: YAML-правила (10 шт) | DATA | `compliance-rules/` — YAML-файлы | 1 тест (схема) |

**Критерий готовности:** все security-гады проходят, audit-log пишется, 10 YAML-правил в репозитории.

### День 5: HTTP Retry + Redis Streams (start)

| Задача | Тип | Код | Тесты |
|--------|-----|-----|-------|
| `tenacity` retry для MCPClient.call_tool() | ADD | `services/mcp_client.py` — `@retry` декоратор | 2 теста |
| Redis Stream Consumer (start) | BUILD | `services/redis_consumer.py` — каркас + XREADGROUP | 2 теста (mock) |

**Критерий готовности:** MCPClient retry при 503/таймауте, Redis консьюмер читает Stream.

**Итого спринт 1:** 5 дней, ~25 тестов новых.

---

## 📅 Спринт 2: AI-пайплайн (Дни 6–10)

**Цель:** поднять Drain3, LogBERT, Ollama, Qdrant, Embeddings — сквозной тест.

### День 6: Drain3 + Embeddings

| Задача | Тип | Код | Тесты |
|--------|-----|-----|-------|
| Drain3 MCP-сервер (aiohttp, JSON-RPC) | WRAP | `services/drain3_server.py` — 50 строк | 3 теста |
| Embeddings: `all-MiniLM-L6-v2` через sentence-transformers | ADD | `services/embeddings.py` — 20 строк | 1 тест |

**Критерий готовности:** Drain3 принимает лог → возвращает template_id. Embeddings возвращает вектор.

### День 7–8: LogBERT MCP-сервер

| Задача | Тип | Код | Тесты |
|--------|-----|-----|-------|
| ONNX Runtime + LogBERT модель (INT8) | BUILD | `services/logbert_server.py` — 80 строк | 5 тестов |
| Батчинг (asyncio.Semaphore, batch 64) | BUILD | Там же — буферизация + batch inference | 3 теста |
| Fallback: если LogBERT недоступен → rule-based | BUILD | `services/logbert_server.py` — 20 строк | 2 теста |

**Критерий готовности:** LogBERT принимает пачку template_id → возвращает `[is_anomaly, confidence]`. При падении — rule-based.

### День 9: Ollama адаптер + Model Registry

| Задача | Тип | Код | Тесты |
|--------|-----|-----|-------|
| Ollama адаптер (ollama SDK, 5 строк) | ADD | `services/llm_providers/ollama.py` | 2 теста |
| Model Registry (JSON-конфиг + fallback) | BUILD | `services/model_registry.py` — 50 строк | 4 теста |
| `aiolimiter` — глобальный Rate Limiter для API | ADD | `api_server_v3.py` — middleware | 2 теста |

**Критерий готовности:** Orchestrator может выбрать: Ollama (CPU) → DeepSeek (API) → Rule-based.

### День 10: Qdrant + сквозной тест

| Задача | Тип | Код | Тесты |
|--------|-----|-----|-------|
| Qdrant MCP-сервер (search/upsert) | WRAP | `services/qdrant_server.py` — 60 строк | 3 теста |
| Wire в InvestigatorAgent — поиск похожих инцидентов | ADD | `agents/investigator_agent.py` | 2 теста |
| Сквозной тест: Drain3 → LogBERT → Ollama → Qdrant | TEST | `tests/test_integration.py` | 2 теста |

**Итого спринт 2:** 5 дней, ~25 тестов. Первый сквозной проход пайплайна.

---

## 📅 Спринт 3: Метки + Compliance (Дни 11–14)

**Цель:** собрать первые метки, запустить Active Learning, закрыть Compliance MVP.

### День 11–12: Active Learning Queue

| Задача | Тип | Код | Тесты |
|--------|-----|-----|-------|
| Redis List — очередь алертов для разметки | BUILD | `services/label_queue.py` — 30 строк | 2 теста |
| Uncertainty sampling — LogBERT confidence < 0.7 → в очередь | BUILD | `services/label_queue.py` — 20 строк | 2 теста |
| SQLite — хранение результатов разметки (TP/FP) | BUILD | `services/label_queue.py` — 20 строк | 2 теста |
| API: GET /label-queue/next, POST /label-queue/submit | ADD | `api_server_v3.py` — 2 эндпоинта | 3 теста |

**Критерий готовности:** LogBERT < 0.7 → алерт в Redis. Аналитик размечает → результат в SQLite.

### День 13–14: Compliance MVP + имитация атак

| Задача | Тип | Код | Тесты |
|--------|-----|-----|-------|
| OpenSCAP/Lynis MCP-инструмент | WRAP | `services/compliance_mcp.py` — 60 строк | 3 теста |
| YAML-правила → Compliance Score → Grafana | ADD | Grafana dashboard | 1 тест |
| Имитация атак: hydra, nmap, sqlmap (shell-скрипты) | DATA | `scripts/attack_simulation/` | 1 тест (check) |
| docker-compose.kafka (артефакт) | DATA | `docker-compose.kafka.yml` | — |

**Итого спринт 3:** 4 дня, ~15 тестов. Active Learning + Compliance работают.

---

## 📅 После спринта 3: Ожидание данных (2–4 недели)

Конвейер работает, но без данных — это MVP без обучения. Параллельно:

- Сбор legitimate baseline (7–14 дней)
- Аналитик размечает алерты через Active Learning
- LogBERT собирает статистику (real F1, FP Rate)
- Compliance сканирование хостов

**Триггер на фазу 2:** ≥500 размеченных меток **или** >300 EPS **или** 30 дней работы.

---

## 🧭 Итоговый технологический радар (обновлённый)

| Уровень | Технологии |
|--------|------------|
| **ADOPT** (внедрено) | Wazuh, Redis Streams, Drain3 (aiohttp), LogBERT (ONNX INT8, batch), Ollama (CPU), Qdrant, all-MiniLM-L6-v2, Grafana + API, Prometheus, Docker, MCP, мульти-агентная архитектура, Circuit Breaker (кастомный), CI/CD, имитация атак, OpenSCAP/Lynis, audit-log, Pydantic strict, ToolCallAuthorizer, OutputValidator, `tenacity`, `aiolimiter` |
| **TRIAL** (пилотировать) | Kafka/Redpanda, TheHive / IVRE, n8n, MISP API, DistilBERT (триаж), MLflow, SecureBERT, Sigma, Telegram бот, Model Registry + Fallback, LoRA fine-tune, Active Learning, locale RU, `pytest-cov`, `bandit`, `pre-commit` |
| **ASSESS** (изучать) | LogPPT (замена LogBERT), OpenCTI, LangGraph, vLLM, CAIBench, OWASP Agentic AI (полный), Lakera Guard, Trivy, Falco + Talon, ГОСТ 57580 полный профиль, OpenTelemetry |
| **HOLD** (отложить) | Мульти-модельные ансамбли, OpenUBA, Apache Spot, Celery, замена aiohttp на httpx, замена кастомного CB, `guardrails-ai`, `litellm` |
