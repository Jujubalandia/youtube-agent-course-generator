# syntax=docker/dockerfile:1
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

# Install CPU-only PyTorch first so that the generic `pip install -r
# requirements.txt` below never resolves CUDA wheels for torch / torchvision /
# torchaudio (multi-GB downloads). The 2.2.2 trio is pinned because newer torch
# releases require numpy>=2.x, which would fight the numpy 1.23 pin the rest of
# the stack needs (see requirements.txt). The +cpu local versions exist only on
# the PyTorch CPU index, so --extra-index-url picks them up while every other
# dependency resolves from PyPI.
# setuptools 79 + wheel + --no-build-isolation: openai-whisper ships only as an
# sdist whose setup.py imports pkg_resources, which newer setuptools removed
# (and pip's isolated build envs install that newer setuptools). Installing
# setuptools 79 globally and disabling isolation makes the sdist build work.
COPY requirements.txt ./
RUN pip install setuptools==79.0.1 wheel \
    && pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        torch==2.2.2+cpu torchvision==0.17.2+cpu torchaudio==2.2.2+cpu \
    && pip install --no-build-isolation -r requirements.txt

# Application code (absolute imports expect `app` on sys.path, hence WORKDIR
# /srv/backend with ./app) plus container helper scripts.
COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

CMD ["bash", "scripts/start.sh"]
