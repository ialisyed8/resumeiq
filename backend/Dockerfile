# Multi-stage build. Runtime image carries no build toolchain and runs as a
# non-root user with no baked-in secrets.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --prefix=/install --no-cache-dir .


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/bin:$PATH" \
    HF_HOME=/home/app/.cache/huggingface

# Runtime dependencies only:
#   libreoffice-writer  — converts .doc/.odt/.rtf/.pages to PDF
#   tesseract-ocr       — OCR for scanned documents
#   libgl1/libglib      — OpenCV preprocessing
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
      libpq5 \
      tesseract-ocr tesseract-ocr-eng \
      libreoffice-writer \
      libgl1 libglib2.0-0 \
      curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder /install /usr/local

# Kept as a separate layer from the main dependency install: adding a small
# package here does not invalidate the cached torch/CUDA download above.
#   email-validator  EmailStr in the auth schemas needs it; pydantic core omits it
#   reportlab        per-candidate PDF reports
RUN pip install --no-cache-dir --root-user-action=ignore email-validator reportlab
COPY --chown=app:app . .

RUN mkdir -p /home/app/.cache && chown -R app:app /home/app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
