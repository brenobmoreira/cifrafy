# 🎸 Cifrafy — Spotify & Cifra Club Assistant (Backend `/api` + `/mcp` & Frontend)

O **Cifrafy** conecta suas playlists do **Spotify** (usando a Spotify Web API oficial via `httpx`) às cifras do **Cifra Club**, avaliando a dificuldade real para o seu nível no violão, detectando pestanas e "acordes intrusos" de passagem, e sugerindo automaticamente o uso de **Capotraste** para eliminar pestanas.

---

## 🏗️ Estrutura do Projeto (`/home/breno/projects/cifrafy`)

Arquitetura limpa separada em **Backend** (que serve tanto a REST API em `/api` quanto o servidor MCP em `/mcp` no mesmo processo FastAPI) e **Frontend**:

```text
cifrafy/
├── backend/                   # Serviço Backend Unificado (Porta 8000)
│   ├── src/
│   │   ├── api.py             # FastAPI expondo /api/* e montando o FastMCP em /mcp
│   │   ├── mcp_server.py      # Definição das Tools do FastMCP (HTTP /mcp ou stdio)
│   │   ├── db.py              # SQLite 2-Tier (song_index ~200B + chord_sheets_cache zlib/LRU)
│   │   ├── config.py          # Configurações (.env)
│   │   └── core/
│   │       ├── analyzer.py        # Motor de dificuldade ponderada + Capotraste anti-pestana
│   │       ├── chord_scraper.py   # Scraper Cifra Club + Busca Solr + Limpeza de títulos Spotify
│   │       └── spotify_client.py  # Cliente assíncrono oficial da Spotify Web API (httpx puro)
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
│
├── frontend/                  # Serviço Frontend Web Desacoplado (Porta 3000)
│   ├── index.html             # Interface Dark-Mode consumindo http://localhost:8000/api
│   └── Dockerfile
│
├── docs/
│   └── ARCHITECTURE_AND_DIFFICULTY.md
├── data/                      # Volume persistente do banco SQLite (guitar.db)
├── docker-compose.yml         # Sobe cifrafy-backend (:8000) e cifrafy-frontend (:3000)
└── .env.example
```

---

## 🚀 Como Rodar

### Com Docker Compose
```bash
cd /home/breno/projects/cifrafy
docker compose up --build
```
- **Frontend Web (Cifrafy UI):** `http://localhost:3000`
- **REST API & Swagger Docs:** `http://localhost:8000/docs`
- **Endpoint MCP (Streamable HTTP / SSE):** `http://localhost:8000/mcp`
