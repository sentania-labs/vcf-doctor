# Base images are pinned by digest (Dependabot's docker ecosystem bumps them);
# the tag is kept alongside for humans. uv is pinned by version.
FROM node:26-alpine@sha256:2d984a15c9b54fd0aeb608b8e0d0d83529eb34d2966db27a1fb4f1edc3d298a3 AS frontend
WORKDIR /src
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc AS backend
COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN uv pip install --system --no-cache .
COPY fixtures/ /app/fixtures/
COPY --from=frontend /src/dist /app/static
RUN useradd -r -u 10001 -d /app -s /usr/sbin/nologin app \
    && mkdir -p /data && chown -R app:app /data
ENV VCF_DOCTOR_STATIC_DIR=/app/static \
    VCF_DOCTOR_DB_PATH=/data/vcf-doctor.db
USER app
VOLUME ["/data"]
EXPOSE 8000
# /api/health needs no session. Uses the stdlib, so no curl in the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
# Forwarded headers are handled by the app (trusted proxies setting), not
# by uvicorn, which would believe X-Forwarded-For from anyone.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
