# Arquitetura, Banco de Dados e Motor de Dificuldade — Guitar Practice Assistant

## 1. O Problema da Busca & Por que o Banco NÃO Fica Pesado

### A Dúvida: *"Quanto mais cifras procurarmos, mais pesado ficará o banco?"*
A resposta curta é: **Só fica pesado se salvarmos a letra/cifra bruta inteira (`raw_chord_sheet`) dentro da tabela principal de busca.**

Vamos fazer a conta real de armazenamento:
- Uma página de cifra completa (`raw_chord_sheet` com letra + espaços + tablaturas) ocupa cerca de **3 KB a 6 KB** de texto.
- Mas os **metadados musicais extraídos** de uma música ocupam apenas **~200 bytes**!
  Exemplo do que realmente precisamos para filtrar/recomendar:
  ```json
  {
    "spotify_track_id": "4uLU6hMCjMI75M1A2tKUQC",
    "artist": "Legião Urbana",
    "title": "Tempo Perdido",
    "cifra_url": "https://www.cifraclub.com.br/legiao-urbana/tempo-perdido/",
    "has_simplified_url": true,
    "musical_key": "G",
    "chord_frequencies": {"C": 34, "Am": 32, "Bm": 30, "Em": 35},
    "total_chord_changes": 131,
    "avg_changes_per_line": 2.4,
    "has_tabs": true
  }
  ```
- **Quanto ocupam 10.000 músicas** salvando apenas essa ficha técnica de 200 bytes?
  **Apenas ~2 MB (dois megabytes)!** O SQLite lê e filtra 10.000 registros de 200 bytes em menos de **1 milissegundo**.

---

## 2. Estratégia de Armazenamento em 2 Níveis (Hot Index vs. Cold/On-Demand Sheet)

Para manter o banco **ultraleve** e a busca **instantânea**, separamos o banco em duas camadas:

```mermaid
flowchart LR
    subgraph Sync["1. Indexação Leve (Progressive / Streaming)"]
        SP["Playlist Spotify"] --> SCR["Extrator Rápido"]
        SCR -->|"Guarda SÓ a Ficha Musical (~200 bytes)"| IDX[("Tabela: song_index\n(Permanente, ~2MB p/ 10k músicas)")]
    end

    subgraph OnDemand["2. Leitura da Cifra Completa (Sob Demanda + TTL)"]
        USER["Usuário Clica para Tocar a Música"] --> CACHE[("Tabela: chord_sheets_cache\n(Texto completo zstd/gzip + Limpeza LRU)")]
        CACHE -.->|"Cache Miss"| CC["Cifra Club"]
    end
```

### Alternativas ao "Sync Manual Pesado":
Em vez de obrigar um sync gigante de uma vez, usamos **3 técnicas combinadas**:

1. **Progressive Streaming (SSE / Background Worker sob demanda):**
   - Quando você abre uma playlist pela primeira vez na interface Web, as músicas que **já estão na `song_index`** aparecem classificadas na mesma hora (0ms).
   - As músicas inéditas daquela playlist entram numa fila assíncrona com concorrência controlada (ex: 4 conexões paralelas via `httpx.AsyncClient`) e vão aparecendo na tela em tempo real conforme são analisadas.
2. **Descarte do Texto Bruto na Indexação (`Index-Only Mode`):**
   - Durante a varredura de uma playlist, o scraper lê o HTML do Cifra Club na memória RAM, extrai o dicionário `chord_frequencies` (`{"G": 40, "D": 35...}`), calcula a densidade de troca por linha, salva esses **200 bytes** na tabela `song_index` e **descarta ou comprime com `zlib` o texto bruto**.
   - Se guardarmos o texto bruto no cache (`chord_sheets_cache`), aplicamos compressão `zlib` (reduz 4 KB para ~800 bytes) e uma política **LRU / TTL** (ex: guarda apenas as últimas 200 cifras que você realmente abriu para tocar).

---

## 3. Modelagem do Banco de Dados (SQLite / PostgreSQL Ready)

A estrutura abaixo foi desenhada em SQL padrão para rodar em **SQLite** (arquivo único em `./data/guitar.db` mapeado por volume no Docker) e poder migrar para PostgreSQL sem mudar uma linha de código se um dia virar SaaS:

### Tabela 1: `song_index` (Catálogo Leve & Permanente — ~200 bytes/música)
Guarda a anatomia harmônica da música. **Não depende do nível do usuário** (pois a música em si não muda quando você aprende um acorde novo).

| Coluna | Tipo | Descrição |
| :--- | :--- | :--- |
| `id` | ` INTEGER PRIMARY KEY` | ID interno |
| `artist_slug` | `TEXT NOT NULL` | Ex: `legiao-urbana` |
| `track_slug` | `TEXT NOT NULL` | Ex: `tempo-perdido` |
| `spotify_track_id` | `TEXT` | ID original da faixa no Spotify (indexado) |
| `artist_name` | `TEXT NOT NULL` | Nome de exibição do artista |
| `track_name` | `TEXT NOT NULL` | Nome limpo da faixa |
| `cifra_url` | `TEXT` | URL canônica no Cifra Club |
| `has_simplified` | `BOOLEAN` | Se existe `/simplificada.html` no Cifra Club |
| `musical_key` | `TEXT` | Tom original (ex: `G`, `Am`) |
| `unique_chords_count` | `INTEGER` | Quantidade de acordes únicos (permite `WHERE unique_chords_count <= 4`) |
| `has_barre_chords` | `BOOLEAN` | Flag rápida se a versão original tem pestana |
| `chord_frequencies_json`| `TEXT (JSON)` | Ex: `{"G": 42, "D": 38, "Em": 35, "C": 34, "Bm": 2}` |
| ` simplified_chords_json`| `TEXT (JSON)` | Frequência dos acordes na versão `/simplificada.html` (se houver) |
| `total_chord_events` | `INTEGER` | Soma total de trocas de acorde na música |
| `avg_changes_per_line` | `REAL` | Densidade de troca (acordes por linha com letra) |
| `has_tabs` | `BOOLEAN` | Se possui bloco de tablatura (`E\|---`) |
| `best_capo_fret` | `INTEGER` | Casa do capotraste que zera/minimiza pestanas (0 a 11) |
| `best_capo_chords_json` | `TEXT (JSON)` | Acordes resultantes usando o capotraste sugerido |
| `updated_at` | `TIMESTAMP` | Data da indexação |

> [!IMPORTANT]
> Repare nas colunas pré-calculadas `unique_chords_count`, `has_barre_chords` e `best_capo_fret`: elas permitem fazer queries SQL diretas como:
> `SELECT * FROM song_index WHERE unique_chords_count <= 4 AND has_barre_chords = 0`!

### Tabela 2: `chord_sheets_cache` (Cache Sob Demanda com Limite LRU)
Guarda o texto completo da cifra apenas para as músicas que você abrir para tocar.

| Coluna | Tipo | Descrição |
| :--- | :--- | :--- |
| `song_id` | `INTEGER PRIMARY KEY` | FK para `song_index.id` |
| `compressed_sheet` | `BLOB` | Cifra completa comprimida com `zlib` (~70% menor) |
| `compressed_simplified` | `BLOB` | Cifra simplificada comprimida (opcional) |
| `last_accessed_at` | `TIMESTAMP` | Usado para limpar cifras antigas se passar de N músicas |

### Tabela 3: `user_profile` & `user_repertoire` (Seu Progresso Pessoal)
| Coluna | Tipo | Descrição |
| :--- | :--- | :--- |
| `known_chords_json` | `TEXT (JSON)` | Lista de acordes dominados: `["C","G","D","A","E","Am","Em","Dm"]` |
| `learning_chords_json` | `TEXT (JSON)` | Acordes que você está treinando no momento (ex: `["F", "Bm"]`) |
| `avoid_barre` | `BOOLEAN` | Preferência global de evitar pestanas |

---

## 4. O Motor de Dificuldade (Como Calculamos a Qualidade de Verdade)

Para classificar uma música de forma inteligente e justa para um violonista, nosso módulo `src/core/analyzer.py` avalia **5 dimensões**:

### Dimensão 1: Taxonomia e Peso de Complexidade dos Acordes (`CHORD_WEIGHTS`)
Todo acorde extraído passa por um parser que separa:
- **Fundamental (Root):** `C`, `C#`, `D`, `Eb`, `E`, `F`, `F#`, `G`, `G#`, `A`, `Bb`, `B`
- **Qualidade/Extensão:** `m`, `7`, `7M`, `9`, `sus4`, `dim`, `m7(b5)`, etc.
- **Baixo Invertido (Slash Chord):** ex: `D/F#`, `C/G`, `G/B`.

Classificamos cada acorde em 4 categorias:
1. **Abertos Básicos (Peso 1.0):** `C, G, D, A, E, Am, Em, Dm, E7, A7, D7, G7, Am7, Em7, Dm7, C7M`.
2. **Abertos com Extensão / Baixo Simples (Peso 1.4):** `C9, D9, Dsus4, Asus2, G/B, C/G, D/F#, F7M(sem pestana)`.
3. **Pestanas Padrão (Peso 3.0):**
   - Qualquer acorde com fundamental `F, F#, Gb, B, Bb, A#, C#, Db, G#, Ab, D#, Eb` (exceto `F7M` aberto).
   - Acordes menores/sétima em fôrma fechada: `Cm, Gm, Dm(na 5ª casa se aplicável, mas Dm aberto é livre)`.
4. **Dissonantes / Jazz / Pestanas Complexas (Peso 4.5):**
   - Acordes com `dim`, `°`, `m7(b5)`, `7(b9)`, `7(#11)`, `7(b13)`, `6(9)` ou baixos invertidos com pestana (`F/A`, `Bb/D`).

### Dimensão 2: Frequência Ponderada vs. "Acorde Intruso"
Em vez de tratar todos os acordes da lista `unique_chords` com o mesmo peso, usamos `chord_frequencies`:
- Se uma música tem 100 trocas de acorde no total:
  - `G` (35x), `D` (32x), `Em` (31x) $\rightarrow$ **98% da música** são 3 acordes abertos básicos.
  - `Bm` (2x) $\rightarrow$ **2% da música** é uma pestana.
- **Detecção de Acorde Intruso (`rare_hard_chords`):**
  - Qualquer pestana ou acorde desconhecido que represente **$< 5\%$ do total de acordes da música** (ou apareça $\le 2$ vezes) é marcado como `intruso/opcional`.
  - A interface avisa: *"Fácil (98% conhecida — possui apenas 1 `Bm` isolado que você pode pular ou substituir)"*.

### Dimensão 3: Densidade Harmônica (`avg_changes_per_line`)
Contamos quantos acordes existem em média para cada linha de letra da música:
- **Baixa densidade ($\le 2.5$ acordes/linha):** Ritmo tranquilo, bastante tempo para montar o próximo acorde.
- **Média densidade ($2.6$ a $4.0$ acordes/linha):** Trocas a cada meia frase.
- **Alta densidade ($> 4.0$ acordes/linha):** Trocas rápidas (comum em Samba, Bossa Nova, MPB complexa). Penaliza o score de facilidade mesmo que os acordes sejam conhecidos.

### Dimensão 4: Cobertura do Usuário (`known_coverage_pct`)
Calculamos duas métricas contra a sua lista `known_chords`:
- **Match Único (`unique_match_pct`):** `%` dos acordes únicos que você conhece (ex: conhece 3 de 4 = 75%).
- **Match Ponderado no Tempo (`weighted_match_pct`):** `%` da música inteira que você consegue tocar com o que já sabe (ex: os 3 acordes que você conhece representam 96% da música!).

### Dimensão 5: Válvula de Escape — Transposição / Capotraste Automático
Se uma música der **Difícil** porque está em um tom ruim para violão (ex: `Bb`, `Eb`, `F#`), o analisador roda uma simulação transpondo a música de `-11` a `+11` semitons:
- Se encontrar um tom onde o número de pestanas cai para **zero** (ou o match com seus `known_chords` sobe para $>90\%$), ele anexa à recomendação:
  - `easier_with_capo`: *"Toque com Capotraste na 3ª casa (fôrmas de `G, D, Em, C` — 0 pestanas!)"*.

---

## 5. Classificação Final e Score (0 a 100)

A nota de facilidade (`ease_score`, de 0 a 100, onde 100 é a música mais fácil possível para você hoje) é calculada por:

$$\text{Score} = (0.50 \times \text{WeightedKnownPct}) + (0.25 \times \text{NoBarreBonus}) + (0.15 \times \text{LowDensityBonus}) + (0.10 \times \text{FewChordsBonus})$$

| Nível | Critério Prático |
| :--- | :--- |
| 🟢 **Fácil** (`Score >= 80`) | **Zero pestanas principais** (máx. 1 acorde intruso $<3\%$) **E** ($\le 4$ acordes básicos **OU** $\ge 95\%$ de cobertura ponderada dos seus acordes conhecidos) **E** densidade $\le 3.5$ acordes/linha. |
| 🟡 **Médio** (`Score 55 - 79`) | Até **1 acorde novo** frequente para você aprender (ótimo para evoluir!) **OU** 1 pestana recorrente (ex: `F` ou `Bm`), ou música sem pestana mas com 5 a 7 acordes abertos. |
| 🔴 **Difícil** (`Score < 55`) | **2 ou mais pestanas** frequentes, múltiplos acordes desconhecidos/dissonantes, ou alta velocidade de troca de acordes ($>4$ por linha). |
| 💡 **Fácil com Capotraste** | Originalmente *Médio/Difícil*, mas vira 🟢 **Fácil** colocando o capotraste na casa sugerida (ou usando a versão `/simplificada.html`). |

---

## 6. Infraestrutura com Containers (Docker & Docker Compose)

Incluiremos na raiz do projeto:
- **`Dockerfile`**: Imagem leve baseada em `python:3.12-slim`, instalando dependências e expondo a porta `8000`.
- **`docker-compose.yml`**:
  - Serviço `guitar-app`: Roda o servidor **FastAPI + Interface Web Integrada + MCP over SSE/HTTP** na porta `8000`.
  - Volume persistente `./data:/app/data` para o banco SQLite `guitar.db` e `./.cache:/app/.cache` para o token OAuth do Spotify.
  - Hot-reload ativado (`--reload`) mapeando `./src:/app/src` para qualquer alteração refletir na hora.
