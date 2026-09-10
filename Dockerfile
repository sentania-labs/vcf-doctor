# Base images are pinned by digest (Dependabot's docker ecosystem bumps them);
# the tag is kept alongside for humans. uv is pinned by version.
FROM node:26-alpine@sha256:2d984a15c9b54fd0aeb608b8e0d0d83529eb34d2966db27a1fb4f1edc3d298a3 AS frontend
WORKDIR /src
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS backend
ARG BUILD_VERSION=dev
ARG BUILD_SHA=unknown
ARG BUILD_DATE=unknown
COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN python3 -c 'import json,sys; print(json.dumps({"version":sys.argv[1],"sha":sys.argv[2],"built_at":sys.argv[3]}))' \
    "$BUILD_VERSION" "$BUILD_SHA" "$BUILD_DATE" > /app/VERSION
RUN uv pip install --system --no-cache .
# The base image ships pip only so users can install things; this image never
# does (uv installed everything above, uvicorn is what runs). pip's vendored
# copies of msgpack and setuptools are what the image scan flags, and they
# are only patched when the base image ships a newer pip, so drop pip entirely.
RUN uv pip uninstall --system pip && rm -f /usr/local/bin/pip
COPY fixtures/ /app/fixtures/
COPY --from=frontend /src/dist /app/static
RUN useradd -r -u 10001 -d /app -s /usr/sbin/nologin app \
    && mkdir -p /data && chown -R app:app /data
# /data is no longer a database: PostgreSQL holds everything. The volume's only
# remaining job is the generated encryption key file, so a deployment that sets
# VCF_DOCTOR_SECRET_KEY needs no volume at all.
ENV VCF_DOCTOR_STATIC_DIR=/app/static \
    VCF_DOCTOR_DATA_DIR=/data
USER app
VOLUME ["/data"]
EXPOSE 8000
# Liveness, not readiness: an unhealthy container is a container something will
# restart, and restarting this one does not bring a database back. Readiness
# lives at /api/health/ready and is what a load balancer or a Kubernetes
# readinessProbe should ask. Needs no session; uses the stdlib, so no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health/live')"
# Forwarded headers are handled by the app (trusted proxies setting), not
# by uvicorn, which would believe X-Forwarded-For from anyone.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
