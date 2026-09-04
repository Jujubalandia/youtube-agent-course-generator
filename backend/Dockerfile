# Backend image for the YouTube -> Course generator (FastAPI + LangGraph agent).
#
# Python 3.10 is deliberate: the backend requirements pin set (numpy==1.23.5,
# numba==0.56.4) only has prebuilt wheels for Python <= 3.10. See the notes in
# requirements.txt and the plan/README for why the upstream pins were adjusted.

FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime + build fallbacks:
#  - ffmpeg: used by the Whisper fallback (audio extraction) and scenedetect.
#  - curl / ca-certificates: healthchecks and outbound HTTPS (Gemini/Groq/S3).
#  - build-essential / python3-dev: only needed if some dependency has to be
#    compiled from source because no manylinux wheel is available.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        build-essential \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/backend

# Install from the fully-pinned snapshot (requirements.freeze.txt) so the build
# is deterministic and fast (no dependency backtracking). requirements.txt is
# the human-readable manifest; freeze.txt is regenerated from a known-good
# environment when the manifest changes.
#
# Details behind the flags:
#  * --extra-index-url cpu: the +cpu torch/torchvision/torchaudio builds only
#    exist on the PyTorch CPU index. We pin the 2.2.2 trio because newer torch
#    releases require numpy>=2.x, which fights the numpy 1.23 pin the stack
#    needs. Every other dependency resolves from PyPI.
#  * setuptools 79 + wheel + --no-build-isolation: openai-whisper ships only as
#    an sdist whose setup.py imports pkg_resources, which newer setuptools
#    removed (and pip's isolated build envs install that newer setuptools).
COPY requirements.freeze.txt ./
RUN pip install setuptools==79.0.1 wheel \
    && pip install --no-build-isolation \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.freeze.txt

# Application code (absolute imports expect `app` on sys.path, hence WORKDIR
# /srv/backend with ./app) plus container helper scripts.
COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

CMD ["bash", "scripts/start.sh"]
