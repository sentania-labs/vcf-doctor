FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim AS backend
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml ./
RUN uv pip install --system --no-cache . || true
COPY backend/ .
RUN uv pip install --system --no-cache .
COPY fixtures/ /app/fixtures/
COPY --from=frontend /src/dist /app/static
ENV VCF_DOCTOR_STATIC_DIR=/app/static \
    VCF_DOCTOR_DB_PATH=/data/vcf-doctor.db
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
