# DocuMind — Air-Gapped Document Intelligence Platform

DocuMind is a fully self-hosted, **air-gapped** Retrieval-Augmented Generation
(RAG) platform for technical manuals and documents. It ingests PDFs (digital,
scanned, or mixed), extracts text/tables/figures, embeds them into pgvector,
and answers grounded, cited questions over a streaming chat UI — with **zero
external API calls**. All models run locally via [Ollama](https://ollama.com).

- **Backend:** FastAPI (async) + SQLAlchemy 2.0 + Alembic
- **Database:** PostgreSQL 16 + pgvector
- **Cache/Queue:** Redis 7 + Celery 5
- **Models:** Ollama — chat LLM, a **generic, swappable Vision model (VLM)**, and `nomic-embed-text`
- **OCR fallback:** Tesseract 5
- **Frontend:** Vanilla JS/HTML/CSS, self-hosted Inter fonts (no CDN)
- **Proxy:** Nginx (static files + reverse proxy)

---

## The VLM is generic (not tied to the chat LLM)

The vision model is **decoupled** from the chat LLM and configured
independently. Set `VLM_MODEL` in `.env` to **any** multimodal model your
Ollama instance serves (`llava:13b`, `llama3.2-vision:11b`, `qwen2.5vl:7b`,
`gemma3:27b`, …). No code changes are required to switch it.

| Setting | Purpose |
| --- | --- |
| `VLM_ENABLED` | `true`/`false` — turn vision on/off entirely. When `false`, scanned pages fall back to Tesseract OCR and figures are skipped. |
| `VLM_MODEL` | The multimodal model used for figure description + scanned-page transcription. |
| `VLM_MAX_NEW_TOKENS` | Max tokens generated per vision call. |
| `LLM_MODEL` | The **chat** model (text answers). Separate from the VLM. |

Vision requests go through `describe_image()` / `transcribe_page_image()` in
`backend/app/services/llm_service.py` (async, chat API) and the synchronous
mirror in `backend/app/pipeline/ollama_sync.py` (used by the Celery pipeline).
Both read `settings.vlm_model`, never the chat model.

---

## Prerequisites

- Docker + Docker Compose v2
- ~20 GB free disk for model weights + data
- **GPU (recommended):** NVIDIA GPU + drivers + the NVIDIA Container Toolkit.
  The stack still runs CPU-only if no GPU is present (slower) — remove the
  `deploy.resources` block from the `ollama` service in `docker-compose.yml`.

### VRAM guidance

| Model | Approx VRAM |
| --- | --- |
| 27B class | ~18 GB |
| 12B class | ~9 GB |
| 4B class | ~4 GB |
| `llava:13b` (VLM) | ~9 GB |
| `nomic-embed-text` | <1 GB |

If VRAM is tight, set `LLM_MODEL` to a smaller model and/or pick a lighter
`VLM_MODEL`, then update `OLLAMA_PULL_MODELS` to match.

---

## Deployment

```bash
git clone <this-repo> documind
cd documind

# 1. Configure secrets
cp .env.example .env
#    Edit .env — at minimum set:
#      POSTGRES_PASSWORD, SECRET_KEY (64+ random chars)
#    Choose your models:
#      LLM_MODEL, VLM_MODEL, EMBEDDING_MODEL
#    Keep OLLAMA_PULL_MODELS in sync with the three model names above.

# 2. Launch the whole stack
docker compose up -d --build

# 3. Watch model download + service health (first run pulls the models)
docker compose logs -f ollama
docker compose ps
```

The app is served at **http://localhost** once `nginx` and `backend` are healthy.

> **Air-gapped note:** the only step that needs the internet is the initial
> `ollama pull` (and the one-time image build). Once models are cached in the
> `ollama_data` volume you can run entirely offline. To deploy on a truly
> isolated host, pre-pull the models on a connected machine and copy the
> `ollama_data` volume across.

---

## First-run: create the first admin user

There is no public sign-up. Create the first admin from inside the backend
container:

```bash
docker compose exec backend python -m scripts.create_admin \
  --email admin@example.com \
  --password 'ChangeMe!2024' \
  --name "Site Admin"
```

Then sign in at http://localhost with those credentials. Additional users are
created from **Admin → Users → Create User**.

---

## Adding PDFs

1. Sign in as an admin and open **Admin** (top-right link, or `/admin.html`).
2. On the **Upload** tab, drag-and-drop a PDF (max 100 MB) and click
   **Upload & Ingest**.
3. Track progress on the **Jobs** tab (auto-refreshes every 5 s). Click a row
   for the full ingestion log.
4. When the job reaches **done**, the document is queryable from the chat UI.

---

## Switching the LLM or VLM

Edit `.env` and restart — no code changes:

```bash
# Example: smaller chat model + a different vision model
LLM_MODEL=gemma3:12b
VLM_MODEL=llama3.2-vision:11b
OLLAMA_PULL_MODELS=gemma3:12b llama3.2-vision:11b nomic-embed-text

docker compose up -d        # ollama entrypoint pulls any missing models
```

To disable vision entirely (OCR-only ingestion):

```bash
VLM_ENABLED=false
```

> Changing `EMBEDDING_MODEL`/`EMBEDDING_DIM` changes the vector dimension. If
> you switch embedding models, re-ingest existing documents so their vectors
> match the new dimension.

---

## Ingestion pipeline (7 phases)

| Phase | File | Output |
| --- | --- | --- |
| 0 Profiling | `pipeline/profiler.py` | scanned/digital, font→heading map |
| 1 Text/OCR | `pipeline/extractor.py` | classified text + headings (VLM/Tesseract for scans) |
| 2 Tables | `pipeline/table_extractor.py` | markdown tables (camelot → pdfplumber) |
| 3 Images | `pipeline/image_extractor.py` | figure descriptions via generic VLM |
| 4 Structure | `pipeline/structurer.py` | nested section tree |
| 5 Chunking | `pipeline/chunker.py` | breadcrumb-rich ~400-word chunks |
| 6 Embedding | `pipeline/embedder.py` | pgvector rows |

Retrieval is hybrid: pgvector cosine (top 20) → BM25 re-rank → MMR (λ=0.5) →
top 5, assembled with conversation history and `[N]` citations.

---

## Security

- bcrypt (cost 12) password hashing; JWT access (15 min) + refresh (7 days)
- Refresh/access tokens in `httpOnly + Secure + SameSite=Strict` cookies only
- All `/admin/*` routes require the `admin` role (403 otherwise)
- Upload validation: content-type **and** `%PDF` magic bytes; 100 MB cap in
  both Nginx and FastAPI
- Login rate limiting (10 / 15 min / IP) via Redis
- CORS restricted to `ALLOWED_ORIGIN`; no stack traces leaked to clients

> **HTTPS:** auth cookies are `Secure`, so browsers only send them over HTTPS
> (or `http://localhost`). For non-localhost deployments, terminate TLS at
> Nginx (or an upstream proxy).

---

## Project layout

```
documind/
├── docker-compose.yml        # full stack, single command
├── .env.example              # all configuration keys
├── ollama/entrypoint.sh      # serve + auto-pull models
├── nginx/                    # static serving + reverse proxy
├── backend/                  # FastAPI app, Celery pipeline, Alembic
│   └── app/{api,models,schemas,services,pipeline}
└── frontend/                 # vanilla JS/HTML/CSS + self-hosted fonts
```
