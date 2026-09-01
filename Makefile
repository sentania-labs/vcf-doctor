# One definition, two callers: local and CI both invoke these targets.
.PHONY: setup lint test build-frontend run dev-backend dev-frontend image

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
