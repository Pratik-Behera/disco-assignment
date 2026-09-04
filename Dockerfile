# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS ui
WORKDIR /ui
# Docker Desktop on macOS often hangs npm on IPv6 to registry.npmjs.org.
ENV NODE_OPTIONS=--dns-result-order=ipv4first
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app/backend
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANGSMITH_TRACING=false \
    DISCO_UI_DIR=/app/backend/static

COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 appuser

COPY prompts /app/prompts
COPY --from=ui /ui/dist ./static
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

# One worker: in-process resume state is lost across workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/backend"]
