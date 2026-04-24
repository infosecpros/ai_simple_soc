#!/bin/bash
# === SOC AI Agent v3 - Production Entrypoint ===
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}🚀 Запуск SOC AI Agent v3${NC}"
echo "========================================"

check_env() {
    local var_name=$1
    local var_value=${!var_name:-}
    if [ -z "$var_value" ]; then
        echo -e "${YELLOW}⚠️  $var_name не установлен${NC}"
        return 1
    else
        echo -e "  ✅ $var_name: ${var_value:0:10}..."
        return 0
    fi
}

echo -e "\n${GREEN}📋 Проверка конфигурации:${NC}"
check_env "WAZUH_MCP_URL"
check_env "DEEPSEEK_API_KEY" || true

export WAZUH_MCP_URL="${WAZUH_MCP_URL:-http://127.0.0.1:3000/mcp}"
export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-8080}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo -e "\n${GREEN}📊 Конфигурация:${NC}"
echo "  WAZUH_MCP_URL: $WAZUH_MCP_URL"
echo "  API_HOST: $API_HOST"
echo "  API_PORT: $API_PORT"
echo "  LOG_LEVEL: $LOG_LEVEL"
echo "========================================"

# Проверка подключения к Wazuh-MCP-Server
echo -e "\n${GREEN}🔌 Проверка подключения к Wazuh-MCP-Server...${NC}"
if python -c "
import asyncio, sys
sys.path.insert(0, '.')
from soc_agent_v3 import MCPClient

async def check():
    try:
        client = MCPClient('$WAZUH_MCP_URL')
        await client.connect()
        tools = client.get_tools_list()
        print(f'  ✅ Подключено. Инструментов: {len(tools)}')
        await client.close()
        return True
    except Exception as e:
        print(f'  ❌ Ошибка: {e}')
        return False

if asyncio.run(check()):
    sys.exit(0)
else:
    sys.exit(1)
"; then
    echo -e "${GREEN}✅ Wazuh-MCP-Server доступен${NC}"
else
    echo -e "${YELLOW}⚠️  Wazuh-MCP-Server недоступен. Запуск в режиме ожидания...${NC}"
fi

echo -e "\n${GREEN}🌐 Запуск API v3 сервера...${NC}"
echo "========================================"

exec python api_server_v3.py
