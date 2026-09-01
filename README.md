# VCF Doctor

What's wrong, what changed, and what should I do about it?

A deterministic VMware Cloud Foundation diagnostic and historical-analysis
application with an optional Claude-assisted interpretation layer. Connects to
one or more vCenters, captures inventory on a schedule, runs deterministic
health checks, shows semantic change between snapshots, and can ask Claude to
explain a finding or draft an investigation script from the evidence.

Design: `startup.md` (full spec) and `docs/PLAN.md` (MVP plan). Current
state: `STATUS.md`.

## Run locally

```bash
make setup          # uv venv + npm ci
make run            # builds frontend, serves everything on :8000
# or, with hot reload in two terminals:
make dev-backend    # :8000
make dev-frontend   # :5173, proxies /api to :8000
```

Or with Docker (local convenience only, see below):

```bash
docker compose up --build
```

Then open http://localhost:8000, go to Connections, add a vCenter.

Fixture mode with no vCenter: `VCF_DOCTOR_DEMO_MODE=true make run`.

## Checks

`make lint` and `make test` are the same targets CI runs.

## Deployment contract

This repository is **not** responsible for deployment. It publishes a
container image; the lab deployment repo and Argo CD own everything else.

| Item | Value |
|---|---|
| Image | `ghcr.io/sentania-labs/vcf-doctor:<tag>` |
| Port | `8000` (HTTP) |
| Health | `GET /api/health` |
| Persistent volume | `/data` (SQLite at `/data/vcf-doctor.db`) |
| Replicas | **exactly 1**, `strategy: Recreate`. Two pods would double-scan and contend for SQLite. |

Environment variables (all optional):

| Variable | Default | Purpose |
|---|---|---|
| `VCF_DOCTOR_DB_PATH` | `/data/vcf-doctor.db` | SQLite location |
| `VCF_DOCTOR_DEMO_MODE` | `false` | Load fixture data, no vCenter needed |
| `ANTHROPIC_API_KEY` | unset | Enables the Claude assistant. Can also be entered in Settings. |
| `VCF_DOCTOR_LLM_MODEL` | `claude-opus-5` | Default assistant model; changeable in Settings |
| `VCF_DOCTOR_DEFAULT_RETENTION` | `96` | Scheduled snapshots kept per connection; changeable in Settings |

vCenter connections, schedules, retention, and assistant settings are
application state set through the GUI and stored on the volume. Nothing
about a specific vCenter belongs in a manifest.

## Security posture (lab-grade)

vCenter passwords and the Anthropic key (when entered via the GUI) are stored
in SQLite on the persistent volume, protected by filesystem permissions only.
No authentication on the UI. Generated scripts are never executed.
Encryption at rest and authentication are tracked as follow-up issues.
