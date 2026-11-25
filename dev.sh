#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

API_PORT="${PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
SQLITE_UI_PORT="${SQLITE_UI_PORT:-8080}"
# Só este computador por padrão. Para a rede local: CIFRAFY_HOST=0.0.0.0 ./dev.sh
BIND_HOST="${CIFRAFY_HOST:-127.0.0.1}"

echo "Iniciando ambiente de desenvolvimento do Cifrafy..."

if [[ "$BIND_HOST" != "127.0.0.1" ]]; then
  echo "AVISO: API e site acessíveis pela rede local em ${BIND_HOST}, sem autenticação."
  echo "       Inclua o IP do computador em ALLOWED_ORIGINS e ALLOWED_HOSTS no .env."
fi

# 1. Garante que o .env exista
if [[ ! -f "$ROOT_DIR/.env" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "Arquivo .env criado a partir do .env.example"
fi

# O caminho relativo do DATABASE_PATH vale a partir de backend/, onde o servidor roda
DB_SETTING="${DATABASE_PATH:-$(sed -n 's/^DATABASE_PATH=//p' "$ROOT_DIR/.env" | tail -n 1 | tr -d "\"'")}"
case "$DB_SETTING" in
  "") DB_FILE="$ROOT_DIR/backend/data/guitar.db" ;;
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
    -p "127.0.0.1:${SQLITE_UI_PORT}:8080" \
    -v "${DB_DIR}:/data" \
    coleifer/sqlite-web "/data/${DB_NAME}" &>/dev/null || \
    echo " Não foi possível iniciar o container cifrafy-sqlite-ui (verifique se o daemon do Docker está ativo)."
else
  echo "Docker/Podman não encontrado no PATH; usando SQLite local direto em ${DB_FILE}"
fi

# 3. Configura e atualiza o virtualenv (.venv) do Backend
if [[ ! -d "$ROOT_DIR/backend/.venv" ]]; then
  echo "Criando virtualenv em backend/.venv..."
  python3 -m venv "$ROOT_DIR/backend/.venv"
fi

echo "Verificando dependências do Backend..."
"$ROOT_DIR/backend/.venv/bin/pip" install -q -e "$ROOT_DIR/backend"

# Encerra todo o grupo de processos (npx e uvicorn criam netos que kill $(jobs -p) não alcança)
cleanup() {
  trap - EXIT INT TERM
  echo ""
  echo "Encerrando Cifrafy (Backend & Frontend)..."
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 4. Sobe o Backend (FastAPI /api + FastMCP /mcp) com Hot-Reload (--reload)
echo "Subindo Backend (/api & /mcp) com Hot-Reload em http://127.0.0.1:${API_PORT}"
(
  cd "$ROOT_DIR/backend"
  "$ROOT_DIR/backend/.venv/bin/uvicorn" src.api:app \
    --host "$BIND_HOST" \
    --port "$API_PORT" \
    --reload \
    --reload-dir "$ROOT_DIR/backend/src"
) &

# 5. Sobe o Frontend com Live-Reload (se npx browser-sync/live-server disponível) ou Python HTTP server
echo "Subindo Frontend em http://127.0.0.1:${WEB_PORT}"
(
  cd "$ROOT_DIR/frontend"
  if command -v npx &>/dev/null; then
    npx --yes live-server --host="$BIND_HOST" --port="${WEB_PORT}" --no-browser --quiet . 2>/dev/null || \
      python3 -m http.server "$WEB_PORT" --bind "$BIND_HOST"
  else
    python3 -m http.server "$WEB_PORT" --bind "$BIND_HOST"
  fi
) &

echo ""
echo "============================================================"
echo "Cifrafy rodando em modo Dev (Hot-Reload ativado!):"
echo "   Frontend Web:     http://127.0.0.1:${WEB_PORT}"
echo "   Backend API Docs: http://127.0.0.1:${API_PORT}/docs"
echo "   MCP Endpoint:     http://127.0.0.1:${API_PORT}/mcp/"
if [[ -n "$CONTAINER_ENGINE" ]]; then
  echo "    SQLite Web UI:    http://127.0.0.1:${SQLITE_UI_PORT}"
fi
echo "   Banco SQLite:     ${DB_FILE}"
echo "============================================================"
echo "Pressione Ctrl+C para parar tudo."

wait
