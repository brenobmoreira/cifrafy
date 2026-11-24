# Cifrafy

O Cifrafy liga suas playlists do Spotify às cifras do Cifra Club e mostra o que você consegue tocar hoje, de acordo com os acordes que já domina.

## Como funciona

- **Dificuldade.** Fácil: sem pestana relevante e com até 4 acordes, ou com todos conhecidos. Médio: exatamente 1 acorde novo ou 1 pestana. Difícil: o resto. Acordes raros de passagem são ignorados. A densidade de trocas só afeta a ordenação.
- **Capotraste.** Se uma casa de 1 a 7 deixa a música Fácil, ela aparece como "Fácil com capotraste".
- **Próximo acorde.** Indica quais acordes liberam mais músicas da sua biblioteca.
- **Repertório.** Marque músicas como Quero aprender, Praticando ou Dominada.
- **Sync seletivo.** Você escolhe as faixas, até 15 por lote. Um limitador global faz uma requisição por vez ao Cifra Club e para ao receber 403 ou 429.
- **Armazenamento.** A cifra completa fica no SQLite, comprimida com zlib, e nunca é apagada automaticamente. Abrir uma música sincronizada não acessa o site.

## Estrutura

```text
backend/    FastAPI em /api e MCP em /mcp, no mesmo processo (porta 8000)
  src/core/ analyzer, chord_scraper, spotify_client e library (casos de uso de API e MCP)
  src/db.py SQLite
frontend/   Site em JS e CSS puros, sem build (porta 3000)
docs/       Arquitetura e regra de dificuldade
```

## Como rodar

```bash
cp .env.example .env   # preencha as credenciais do Spotify
./dev.sh               # backend e frontend com hot-reload
```

Também é possível usar `docker compose up --build`.

- Site: `http://127.0.0.1:3000`
- API: `http://127.0.0.1:8000/docs`
- MCP: `http://127.0.0.1:8000/mcp/`

No painel do Spotify, registre o redirect `http://127.0.0.1:8000/api/spotify/callback`. O Spotify não aceita `localhost`.

## Testes

```bash
cd backend
pip install -e ".[dev]"
pytest
```
