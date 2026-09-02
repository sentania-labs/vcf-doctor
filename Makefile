# One definition, two callers: local and CI both invoke these targets.
# CI never hand-copies a scanner command; if a gate changes, it changes here.
.PHONY: setup lint test build-frontend run dev-backend dev-frontend image \
        scan scan-deps scan-secrets scan-fs scan-image

setup:
	cd backend && uv venv && uv pip install -e ".[dev]"
	cd frontend && npm ci

lint:
	cd backend && uv run ruff check .
	cd frontend && npx tsc -b --noEmit

test:
	cd backend && uv run pytest -q

build-frontend:
	cd frontend && npm run build

# Backend serving the built frontend, like the container does.
run: build-frontend
	cd backend && VCF_DOCTOR_STATIC_DIR=../frontend/dist VCF_DOCTOR_DB_PATH=../data/vcf-doctor.db \
		uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

dev-backend:
	cd backend && VCF_DOCTOR_DB_PATH=../data/vcf-doctor.db uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

image:
	docker build -t vcf-doctor:local .

# ---- security scans ---------------------------------------------------------
# Fast path: trivy / gitleaks binaries on PATH (see README, "Security posture").
# Fallback: the pinned scanner containers, run as the calling user. Both read
# the same committed config (trivy.yaml, .trivyignore), so results are identical.
SHELL := /bin/bash
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
