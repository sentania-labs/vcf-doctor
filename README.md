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

Then open http://localhost:8000. On first run you are asked to set the
operator password (or seed it with `VCF_DOCTOR_ADMIN_PASSWORD`). Then go to
Connections and add a vCenter.

Fixture mode with no vCenter: `VCF_DOCTOR_DEMO_MODE=true make run`.

## Checks

`make lint` and `make test` are the same targets CI runs.

## Deployment contract

This repository is **not** responsible for deployment. It publishes a
container image; the lab deployment repo and Argo CD own everything else.

| Item | Value |
|---|---|
| Image | `ghcr.io/sentania-labs/vcf-doctor:<tag>` where tag is `v0.1.N` (release), `sha-<7>` or `latest` |
| Port | `8000` (HTTP) |
| Health | `GET /api/health` |
| Persistent volume | `/data` (SQLite at `/data/vcf-doctor.db`) |
| Replicas | **exactly 1**, `strategy: Recreate`. Two pods would double-scan and contend for SQLite. |
| User | runs as uid `10001`; set `fsGroup: 10001` so the volume is writable |

Environment variables (all optional):

| Variable | Default | Purpose |
|---|---|---|
| `VCF_DOCTOR_DB_PATH` | `/data/vcf-doctor.db` | SQLite location |
| `VCF_DOCTOR_DEMO_MODE` | `false` | Load fixture data, no vCenter needed |
| `ANTHROPIC_API_KEY` | unset | Enables the Claude assistant. A key entered in Settings takes precedence. |
| `VCF_DOCTOR_AUTH` | `on` | `off` disables the login page (use only behind ingress auth) |
| `VCF_DOCTOR_ADMIN_PASSWORD` | unset | Seeds the operator password on first boot; otherwise the UI asks on first visit |
| `VCF_DOCTOR_LLM_MODEL` | `claude-opus-5` | Default assistant model; changeable in Settings |
| `VCF_DOCTOR_RETENTION_RECENT_DAYS` | `14` | Default retention tier: every scheduled snapshot younger than this is kept; changeable in Settings |
| `VCF_DOCTOR_RETENTION_HOURLY_DAYS` | `30` | Between recent and this age, one scheduled snapshot per hour is kept |
| `VCF_DOCTOR_RETENTION_DAILY_DAYS` | `365` | Between hourly and this age, one per day is kept; older scheduled snapshots and change-log rows are pruned. Manual snapshots are never pruned. (`VCF_DOCTOR_DEFAULT_RETENTION`, the old snapshot count, is ignored.) |
| `VCF_DOCTOR_MIN_INTERVAL_MINUTES` | `5` | Floor for scan intervals |
| `VCF_DOCTOR_SCHEDULER` | `on` | `off` disables scheduled scans (Scan Now still works) |
| `VCF_DOCTOR_STATIC_DIR` | `/app/static` in the image | Built frontend location |
| `VCF_DOCTOR_FIXTURES_DIR` | `/app/fixtures` in the image | Fixture data for demo mode |

A key entered on the Settings page takes precedence over `ANTHROPIC_API_KEY`.

vCenter connections, schedules, retention, and assistant settings are
application state set through the GUI and stored on the volume. Nothing
about a specific vCenter belongs in a manifest.

## Security posture (lab-grade)

A single shared operator password gates the UI and API (session cookie, 7
days). vCenter passwords and the Anthropic key (when entered via the GUI) are
stored in SQLite on the persistent volume, protected by filesystem
permissions only. Generated scripts are never executed. Encryption at rest
and per-user accounts are tracked as follow-up issues.
