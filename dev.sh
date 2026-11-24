#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

API_PORT="${PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
SQLITE_UI_PORT="${SQLITE_UI_PORT:-8080}"

echo "Iniciando ambiente de desenvolvimento do Cifrafy..."

# 1. Garante que .env e ./data existam
mkdir -p "$ROOT_DIR/data"
if [[ ! -f "$ROOT_DIR/.env" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "Arquivo .env criado a partir do .env.example"
fi

# O caminho relativo do DATABASE_PATH vale a partir de backend/, onde o servidor roda
DB_SETTING="${DATABASE_PATH:-$(sed -n 's/^DATABASE_PATH=//p' "$ROOT_DIR/.env" | tail -n 1 | tr -d "\"'")}"
case "$DB_SETTING" in
  "") DB_FILE="$ROOT_DIR/data/guitar.db" ;;
  /*) DB_FILE="$DB_SETTING" ;;
  *) DB_FILE="$(realpath -m "$ROOT_DIR/backend/$DB_SETTING")" ;;
esac
DB_DIR="$(dirname "$DB_FILE")"
DB_NAME="$(basename "$DB_FILE")"
mkdir -p "$DB_DIR"

# 2. Sobe container do SQLite Web UI (coleifer/sqlite-web) se Docker ou Podman estiver instalado
CONTAINER_ENGINE=""
if command -v docker &>/dev/null; then
  CONTAINER_ENGINE="docker"
elif command -v podman &>/dev/null; then
  CONTAINER_ENGINE="podman"
fi

if [[ -n "$CONTAINER_ENGINE" ]]; then
  echo "Subindo container SQLite Web UI na porta :$SQLITE_UI_PORT..."
  $CONTAINER_ENGINE rm -f cifrafy-sqlite-ui &>/dev/null || true
  $CONTAINER_ENGINE run -d \
    --name cifrafy-sqlite-ui \
    -p "${SQLITE_UI_PORT}:8080" \
    -v "${DB_DIR}:/data" \
    coleifer/sqlite-web "/data/${DB_NAME}" &>/dev/null || \
    echo " Não foi possível iniciar o container cifrafy-sqlite-ui (verifique se o daemon do Docker está ativo)."
else
  echo "ℹ Docker/Podman não encontrado no PATH; usando SQLite local direto em ${DB_FILE}"
fi

# 3. Configura e atualiza o virtualenv (.venv) do Backend
if [[ ! -d "$ROOT_DIR/backend/.venv" ]]; then
  echo "Criando virtualenv em backend/.venv..."
  python3 -m venv "$ROOT_DIR/backend/.venv"
fi

echo "Verificando dependências do Backend..."
"$ROOT_DIR/backend/.venv/bin/pip" install -q -e "$ROOT_DIR/backend"

# Cleanup dos processos filhos ao apertar Ctrl+C
cleanup() {
  echo ""
  echo "Encerrando Cifrafy (Backend & Frontend)..."
  kill $(jobs -p) 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 4. Sobe o Backend (FastAPI /api + FastMCP /mcp) com Hot-Reload (--reload)
echo "Subindo Backend (/api & /mcp) com Hot-Reload em http://localhost:${API_PORT}"
(
  cd "$ROOT_DIR/backend"
  "$ROOT_DIR/backend/.venv/bin/uvicorn" src.api:app \
    --host 0.0.0.0 \
    --port "$API_PORT" \
    --reload \
    --reload-dir "$ROOT_DIR/backend/src"
) &

# 5. Sobe o Frontend com Live-Reload (se npx browser-sync/live-server disponível) ou Python HTTP server
echo "Subindo Frontend em http://localhost:${WEB_PORT}"
(
  cd "$ROOT_DIR/frontend"
  if command -v npx &>/dev/null; then
    npx --yes live-server --port="${WEB_PORT}" --no-browser --quiet . 2>/dev/null || \
      python3 -m http.server "$WEB_PORT"
  else
    python3 -m http.server "$WEB_PORT"
  fi
) &

echo ""
echo "============================================================"
echo "Cifrafy rodando em modo Dev (Hot-Reload ativado!):"
echo "   Frontend Web:     http://localhost:${WEB_PORT}"
echo "   Backend API Docs: http://localhost:${API_PORT}/docs"
echo "   MCP Endpoint:     http://localhost:${API_PORT}/mcp"
if [[ -n "$CONTAINER_ENGINE" ]]; then
  echo "    SQLite Web UI:    http://localhost:${SQLITE_UI_PORT}"
fi
echo "   Banco SQLite:     ${DB_FILE}"
echo "============================================================"
echo "Pressione Ctrl+C para parar tudo."

wait
