# === SOC AI Agent v3 - Dockerfile ===
FROM python:3.11-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir \
    pydantic-ai \
    aiohttp \
    python-dotenv

RUN chmod +x entrypoint_api_v3.sh

RUN groupadd -r socagent && useradd -r -g socagent -d /app -s /sbin/nologin socagent
USER socagent

LABEL maintainer="SOC Team"
LABEL description="SOC AI Agent v3 with LLM optimization"
LABEL version="0.6.0"

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

EXPOSE 8080
ENTRYPOINT ["./entrypoint_api_v3.sh"]
