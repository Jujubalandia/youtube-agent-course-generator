# YouTube Video to Structured Course Generator — Docker-Compose Launcher

🚀 Transform YouTube videos into interactive educational courses with AI-powered content generation, quizzes, and retention strategies.

This repository is a **containerized launcher** for the upstream project
[`tanisha083/youtube-agent-course-generator`](https://github.com/tanisha083/youtube-agent-course-generator)
(vendored at commit `29c0cd5`, 2025-04-15). One `docker compose up` replaces the
manual venv + pip, `npm install`, and bare `docker run jaeger` workflow from the
original README with a reproducible full stack:

| Service       | Purpose                                                            | Host URL                       |
|---------------|--------------------------------------------------------------------|--------------------------------|
| `frontend`    | React (CRA) SPA, served by nginx                                   | http://localhost:3000          |
| `backend`     | FastAPI + LangGraph agent (Python 3.10, CPU PyTorch)               | http://localhost:8000          |
| `postgres`    | Course / quiz / retention persistence (JSONB)                      | internal only                  |
| `minio`       | Local S3-compatible storage for extracted frame images             | http://localhost:9000 (API), http://localhost:9001 (console) |
| `jaeger`      | OpenTelemetry collector + UI (traces for LLM generations)          | http://localhost:16686         |

## Quick start

Prerequisites: Docker with **Compose v2** (`docker compose version`).

```bash
cp .env.example .env        # then edit .env
```

You must set two API keys in `.env` (the backend refuses to start without them):

```dotenv
GEMINI_API_KEY=your_gemini_api_key_here   # https://aistudio.google.com/apikey
GROQ_API_KEY=your_groq_api_key_here       # https://console.groq.com/keys
```

Then build and start:

```bash
docker compose up -d --build
```

First build is slow (the backend image installs PyTorch, FiftyOne, Whisper,
numba, …; expect several minutes and a multi-GB image). Watch startup with:

```bash
docker compose ps
docker compose logs -f backend
```

## Usage

1. Open http://localhost:3000, paste a YouTube URL (a short, captioned video
   is best for a first test) and click **Generate Course**.
2. Follow the progress (transcript → frame extraction → AI processing → S3 →
   database). Results appear in three tabs: *Course Content*, *Knowledge
   Check*, *Retention & Review*.
3. Inspect traces of the agent run in the Jaeger UI at
   http://localhost:16686 (search service `video-to-course-agent`).

Everything persists across restarts (Postgres volume, MinIO volume). Wipe all
local data with `docker compose down -v`.

## Configuration (.env)

See `.env.example` for the full annotated list. Highlights:

- `GEMINI_API_KEY`, `GROQ_API_KEY` — **required**.
- `POSTGRES_USER/PASSWORD/DB` — Postgres defaults (`course`/`course`/`coursegen`).
  If you change them, also set `DATABASE_URL` accordingly.
- Frame storage:
  - **Local MinIO mode (default)** — the stack creates bucket
    `course-frames` and makes it publicly readable, so the generated course
    embeds image URLs like `http://localhost:9000/course-frames/frames/<id>/…`.
    `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` default to MinIO's root
    credentials (`minioadmin`); if you change `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`,
    mirror them here.
  - **AWS S3 cloud mode** — set `S3_ENDPOINT_URL=` and `S3_PUBLIC_URL=`
    (empty) and put real `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
    `AWS_REGION`, `S3_BUCKET_NAME` values in `.env`. No code change needed.
- `REACT_APP_API_BASE_URL` — backend API URL compiled into the frontend
  (default `http://localhost:8000`, matching the original app). Keep the UI on
  port 3000: the backend CORS allow-list only permits `http://localhost:3000`.

## What was changed vs. upstream

Small, container-oriented patches on top of the vendored source (defaults keep
the original behavior):

| File | Change |
|------|--------|
| `backend/app/api/s3_utils.py` | Honors `S3_ENDPOINT_URL` / `S3_PUBLIC_URL` so frames can go to self-hosted MinIO (path-style) instead of only real AWS S3. |
| `backend/requirements.txt` | Replaced an unsatisfiable pin set (`scipy>=1.16.5,<1.23` + `numpy<1.23` + `numba==0.56.4` cannot co-exist on any Python) with a consistent Python-3.10 set; added `opencv-python-headless` + `av` (scenedetect video backends that upstream only had transitively). |
| `frontend/src/services/api.ts`, `frontend/src/pages/UploadPage.tsx` | API/SSE base URL comes from `REACT_APP_API_BASE_URL` (default unchanged). |
| `backend/scripts/init_db.py` | One-shot schema initializer — upstream never ran `create_all`, so the `courses` table is now created on first start (`backend-init` service). |
| `backend/scripts/start.sh` | Preflight import check (torch/FiftyOne/agent) then `uvicorn` (single worker). |
| `backend/Dockerfile`, `frontend/Dockerfile`, `nginx.conf`, `.dockerignore`, `docker-compose.yml`, `.env.example` | New container artifacts (this launcher). |

## Troubleshooting / notes

- **Single-replica backend only.** Progress SSE queues and background tasks are
  in-process (`PROGRESS_QUEUES`/`ACTIVE_TASKS`), so do **not**
  `--scale backend=2`.
- **Ports.** The stack uses 3000, 8000, 9000, 9001, 16686. Edit the `ports:`
  entries in `docker-compose.yml` if any are taken — but keep the frontend at
  `3000:80` and backend reachable on `http://localhost:8000`, otherwise the CORS
  allow-list and the baked-in `REACT_APP_API_BASE_URL` no longer match (rebuild
  the frontend after changing the API URL: `docker compose build frontend`).
- **Platform.** Designed for `linux/amd64` (Docker Desktop / WSL). arm64 builds
  may need to drop or re-pin `numba==0.56.4` / FiftyOne.
- **Frames are deleted after a run** (upstream cleanup); with MinIO mode they
  are safe in the bucket. Without *any* object storage configured, generated
  courses show no images — exactly like upstream.
- **Whisper fallback** (used only when YouTube captions are unavailable)
  downloads the `base` model on first use into the `whisper_cache` volume; this
  needs internet at that moment.
- **Backend must reach the internet** at runtime (YouTube, Gemini, Groq).
- **Rebuild after editing code**: `docker compose up -d --build backend frontend`.
- **Reset everything**: `docker compose down -v` (drops Postgres + MinIO data).

## Manual (non-Docker) development

The un-containerized workflow from the upstream README still applies for
development inside `backend/` (Python venv) and `frontend/` (`npm start`) —
but note the backend entry point is `uvicorn app.main:app` run from the
`backend/` directory, and you still need a Postgres database and either AWS S3
or a MinIO instance.

## Demo

https://drive.google.com/file/d/16gN4C70lhdyf4rVMzDzx9rqFxmA8i7x7/view
