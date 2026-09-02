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

VCF Doctor expects a live vCenter. There is no demo or sample-data mode;
the first useful screen is the one after you add a connection.

## Checks

`make lint`, `make test` and `make scan` are the same targets CI runs. See
"Security posture" for what `make scan` needs on your machine.

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

A key entered on the Settings page takes precedence over `ANTHROPIC_API_KEY`.

vCenter connections, schedules, retention, and assistant settings are
application state set through the GUI and stored on the volume. Nothing
about a specific vCenter belongs in a manifest.

## Security posture

**Access.** A single shared operator password gates the UI and API (session
cookie, 7 days, PBKDF2 hash, signing secret rotated on password change).
vCenter passwords and the Anthropic key (when entered via the GUI) are stored
in SQLite on the persistent volume, protected by filesystem permissions only.
Generated scripts are never executed. Encryption at rest and per-user accounts
are tracked as follow-up issues.

**Browser headers.** Every response carries `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
`Permissions-Policy: camera=(), microphone=(), geolocation=()` and a
same-origin Content-Security-Policy (`default-src 'self'`, no inline or
external scripts, `frame-ancestors 'none'`; inline styles stay allowed
because React and Tailwind set style attributes). `/api` responses are
`Cache-Control: no-store`. `Strict-Transport-Security` (one year) is sent
only when the ingress reports `X-Forwarded-Proto: https`, so a plain-http
lab deployment is never locked out.

**Gates in CI** (all GitHub-hosted, all via `make`, so a developer and CI run
the same command):

| Gate | Target | Blocks a PR / publish on |
|---|---|---|
| Dependency audit | `make scan-deps` | any known CVE in the backend environment (pip-audit); HIGH+ in frontend runtime dependencies (npm audit) |
| Secret scan | `make scan-secrets` | any secret anywhere in git history (gitleaks) |
| Repo scan | `make scan-fs` | HIGH/CRITICAL fixable CVE in `uv.lock` / `package-lock.json`; Dockerfile misconfiguration (trivy) |
| Image scan | `make scan-image IMAGE=...` | HIGH/CRITICAL fixable CVE in the built image: OS packages, Python and npm packages (trivy) |
| Dependency review | GitHub action, PRs only | a newly added dependency with a HIGH+ advisory |
| CodeQL | `codeql.yml` | Python and TypeScript static analysis; PRs, main, weekly |
| Smoke test | `ci.yml` image job | container does not boot, fixture scan fails, auth bypass, path traversal, missing headers, or not running as uid 10001 |

Only a main-branch push that passed every gate publishes. Dependabot opens
weekly grouped PRs for pip, npm, GitHub Actions and the base image digests.
The hackathon exception that suspended image scanning is closed; these gates
are the release discipline now.

**What ships.** Base images are pinned by digest and `uv` by version. The
container declares a `HEALTHCHECK` on `/api/health`. The published image
index carries a max-mode SLSA provenance attestation and an SPDX SBOM, and the
digest is signed keyless with cosign (identity: this repository's `ci.yml`,
logged in Rekor). To verify a pulled image:

```bash
cosign verify ghcr.io/sentania-labs/vcf-doctor@<digest> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/sentania-labs/vcf-doctor/'
docker buildx imagetools inspect ghcr.io/sentania-labs/vcf-doctor:<tag> --format '{{ json .SBOM }}'
```

**Running the scans locally.** `make scan` runs deps + secrets + repo scan;
`make image && make scan-image IMAGE=vcf-doctor:local` scans the built image.
Fast path: put `trivy` and `gitleaks` on your PATH (Debian/Ubuntu:
`sudo apt install trivy` from Aqua's apt repo, or the release tarballs from
github.com/aquasecurity/trivy and github.com/gitleaks/gitleaks). Without
them, the targets fall back to the pinned scanner containers
(`aquasec/trivy:0.74.0`, `ghcr.io/gitleaks/gitleaks:v8.30.1`), run as your
user with the trivy DB cached in `~/.cache/trivy` (override with
`TRIVY_CACHE=...`). Results are identical either way because both read
`trivy.yaml` and `.trivyignore`.

**Accepting a finding.** Unfixed CVEs never gate (`ignore-unfixed` in
`trivy.yaml`); `trivy image --ignore-unfixed=false` lists them on demand. A
fixable finding is fixed by bumping (base image digest, `uv lock`,
`npm audit fix`), not ignored. If one genuinely cannot be fixed yet, add it
to `.trivyignore` with the CVE id, a comment giving the reason, and an expiry
(`CVE-XXXX-NNNNN exp:YYYY-MM-DD`); after that date it fails the gate again.
