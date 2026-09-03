#!/usr/bin/env bash
# Container entrypoint for the backend service.
#
# 1. Runs a preflight import of the heavy stack (fiftyone, the LangGraph agent
#    and the FastAPI app) so the container fails fast and visibly if a
#    dependency is broken, instead of crash-looping uvicorn silently.
# 2. Launches uvicorn with a single worker on 0.0.0.0:8000.
#
# NOTE: must stay a single worker/process. Progress queues and background
# course-generation tasks live in process memory (PROGRESS_QUEUES /
# ACTIVE_TASKS in app/api/routes.py), so do NOT scale this service.

set -euo pipefail

echo "[start] Preflight import check (fiftyone + app)..."
python -c "import fiftyone; import app.main; print('[start] preflight OK')"

echo "[start] Launching uvicorn on 0.0.0.0:8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
