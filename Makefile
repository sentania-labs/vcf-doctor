# One definition, two callers: local and CI both invoke these targets.
# CI never hand-copies a scanner command; if a gate changes, it changes here.
.PHONY: setup lint test test-backend build-frontend run dev-backend dev-frontend image \
        dev-db dev-db-stop migrate scan scan-deps scan-secrets scan-fs scan-image

SHELL := /bin/bash

# PostgreSQL is the only supported database. Two throwaway servers live here so
# a checkout needs nothing installed: one for `make test`, dropped afterwards,
# and one for `make run` / `make dev-backend`, which keeps its volume.
POSTGRES_IMAGE ?= postgres:16
TEST_PG_NAME ?= vcf-doctor-test-pg
TEST_PG_PORT ?= 55433
TEST_DATABASE_URL ?= postgresql://vcf_doctor@127.0.0.1:$(TEST_PG_PORT)/vcf_doctor_test
DEV_PG_NAME ?= vcf-doctor-dev-pg
DEV_PG_PORT ?= 55432
DEV_DATABASE_URL ?= postgresql://vcf_doctor@127.0.0.1:$(DEV_PG_PORT)/vcf_doctor

setup:
	cd backend && uv venv && uv pip install -e ".[dev]"
	cd frontend && npm ci

lint:
	cd backend && uv run ruff check .
	cd frontend && npx tsc -b --noEmit

# The backend suite drops and rebuilds the schema between tests, so it needs a
# database of its own and refuses to run without one being named. Set
# VCF_DOCTOR_TEST_DATABASE_URL to use a server you already have; otherwise a
# disposable container is started and removed here. CI calls this same target.
test: test-backend
	cd frontend && npm test

test-backend:
	@if [ -n "$$VCF_DOCTOR_TEST_DATABASE_URL" ]; then \
	  cd backend && uv run pytest -q; \
	else \
	  trap 'docker rm -f $(TEST_PG_NAME) >/dev/null 2>&1 || true' EXIT; \
	  docker rm -f $(TEST_PG_NAME) >/dev/null 2>&1 || true; \
	  docker run -d --name $(TEST_PG_NAME) \
	    -e POSTGRES_USER=vcf_doctor -e POSTGRES_DB=vcf_doctor_test \
	    -e POSTGRES_HOST_AUTH_METHOD=trust \
	    -p 127.0.0.1:$(TEST_PG_PORT):5432 $(POSTGRES_IMAGE) >/dev/null; \
	  for i in $$(seq 1 60); do \
	    docker exec $(TEST_PG_NAME) pg_isready -U vcf_doctor -q 2>/dev/null && break; \
	    if [ "$$i" = 60 ]; then \
	      echo "test postgres never became ready (port $(TEST_PG_PORT) in use?):"; \
	      docker logs $(TEST_PG_NAME) 2>&1 | tail -20; exit 1; \
	    fi; \
	    sleep 1; \
	  done; \
	  cd backend && VCF_DOCTOR_TEST_DATABASE_URL="$(TEST_DATABASE_URL)" uv run pytest -q; \
	fi

# A local PostgreSQL for `make run` and `make dev-backend`. It keeps its data in
# a named volume, so a restart does not lose the connections you added.
dev-db:
	@docker start $(DEV_PG_NAME) >/dev/null 2>&1 || \
	  docker run -d --name $(DEV_PG_NAME) \
	    -e POSTGRES_USER=vcf_doctor -e POSTGRES_DB=vcf_doctor \
	    -e POSTGRES_HOST_AUTH_METHOD=trust \
	    -v vcf-doctor-dev-pg:/var/lib/postgresql/data \
	    -p 127.0.0.1:$(DEV_PG_PORT):5432 $(POSTGRES_IMAGE) >/dev/null
	@for i in $$(seq 1 60); do \
	  docker exec $(DEV_PG_NAME) pg_isready -U vcf_doctor -q && exit 0; \
	  sleep 1; \
	done; echo "dev postgres never became ready"; exit 1

dev-db-stop:
	docker stop $(DEV_PG_NAME) >/dev/null 2>&1 || true

# Apply pending schema migrations to the dev database. The app does this itself
# at startup; this target is for looking at the schema without starting it.
migrate: dev-db
	cd backend && VCF_DOCTOR_DATABASE_URL="$(DEV_DATABASE_URL)" uv run python -m app.migrate upgrade

build-frontend:
	cd frontend && npm run build

# Backend serving the built frontend, like the container does.
run: build-frontend dev-db
	cd backend && VCF_DOCTOR_STATIC_DIR=../frontend/dist VCF_DOCTOR_DATABASE_URL="$(DEV_DATABASE_URL)" \
		VCF_DOCTOR_DATA_DIR=../data \
		uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers

dev-backend: dev-db
	cd backend && VCF_DOCTOR_DATABASE_URL="$(DEV_DATABASE_URL)" VCF_DOCTOR_DATA_DIR=../data \
		uv run uvicorn app.main:app --reload --port 8000 --no-proxy-headers

dev-frontend:
	cd frontend && npm run dev

image:
	docker build \
		--build-arg BUILD_VERSION=dev \
		--build-arg BUILD_SHA=$$(git rev-parse HEAD 2>/dev/null || echo unknown) \
		--build-arg BUILD_DATE=$$(date -u +%Y-%m-%dT%H:%M:%SZ) \
		-t vcf-doctor:local .

# ---- security scans ---------------------------------------------------------
# Fast path: trivy / gitleaks binaries on PATH (see README, "Security posture").
# Fallback: the pinned scanner containers, run as the calling user. Both read
# the same committed config (trivy.yaml, .trivyignore), so results are identical.
TRIVY_VERSION ?= 0.74.0
GITLEAKS_VERSION ?= v8.30.1
TRIVY_CACHE ?= $(HOME)/.cache/trivy
DOCKER_SOCK ?= /var/run/docker.sock
DOCKER_SOCK_GID := $(shell stat -c %g $(DOCKER_SOCK) 2>/dev/null || echo 0)
# In a git worktree .git is a file pointing outside the checkout; mount the
# common dir read-only so gitleaks can read history from inside the container.
GIT_COMMON := $(shell git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
GIT_COMMON_MOUNT := $(if $(filter $(CURDIR)/%,$(GIT_COMMON)),,$(if $(GIT_COMMON),-v "$(GIT_COMMON):$(GIT_COMMON):ro",))

ifneq ($(shell command -v trivy 2>/dev/null),)
TRIVY = trivy --cache-dir "$(TRIVY_CACHE)"
else
TRIVY = mkdir -p "$(TRIVY_CACHE)" && docker run --rm \
	--user $(shell id -u):$(shell id -g) --group-add $(DOCKER_SOCK_GID) \
	-v "$(CURDIR):/repo:ro" -w /repo \
	-v "$(TRIVY_CACHE):/cache" \
	-v "$(DOCKER_SOCK):/var/run/docker.sock" \
	aquasec/trivy:$(TRIVY_VERSION) --cache-dir /cache
endif

ifneq ($(shell command -v gitleaks 2>/dev/null),)
GITLEAKS = gitleaks
else
GITLEAKS = docker run --rm --user $(shell id -u):$(shell id -g) \
	-v "$(CURDIR):/repo" $(GIT_COMMON_MOUNT) -w /repo ghcr.io/gitleaks/gitleaks:$(GITLEAKS_VERSION)
endif

# Everything a pull request must pass before an image is built.
scan: scan-deps scan-secrets scan-fs

# Known-vulnerable dependencies. pip-audit checks the installed backend
# environment (runtime plus dev extras); npm audit checks the frontend
# lockfile, runtime dependencies only, HIGH and above.
scan-deps:
	cd backend && uv run pip-audit --skip-editable --progress-spinner off
	cd frontend && npm audit --omit=dev --audit-level=high

# Committed secrets, full git history (CI checks out with fetch-depth 0).
# gitleaks exits 0 when git itself fails and it scanned nothing, so the gate
# also requires that at least one commit was actually scanned.
scan-secrets:
	log=$$(mktemp); trap 'rm -f "$$log"' EXIT; \
	$(GITLEAKS) detect --source . --no-banner --redact >"$$log" 2>&1; rc=$$?; cat "$$log"; \
	test $$rc -eq 0 && grep -q -E '[1-9][0-9]* commits scanned' "$$log"

# Repository scan: misconfiguration (Dockerfile, compose, workflows) and known
# vulnerabilities in lockfiles. Severity, exit code and skips live in trivy.yaml.
scan-fs:
	$(TRIVY) fs --scanners vuln,misconfig .

# Built image scan (OS packages plus bundled Python and npm packages).
# Usage: make scan-image IMAGE=vcf-doctor:local
scan-image:
	@test -n "$(IMAGE)" || { echo "usage: make scan-image IMAGE=<tag>"; exit 2; }
	$(TRIVY) image $(IMAGE)
