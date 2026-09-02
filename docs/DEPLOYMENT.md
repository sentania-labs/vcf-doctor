# Deployment

This repository is **not** responsible for deployment. It publishes a
container image; whoever deploys it (in the lab, the deployment repository
and Argo CD) owns everything else. Nothing about a specific vCenter belongs
in a manifest: connections, schedules, retention and assistant settings are
application state set through the GUI and stored on the volume.

## Contract

| Item | Value |
|---|---|
| Image | `ghcr.io/sentania-labs/vcf-doctor:<tag>` where tag is `v0.1.N` (release), `sha-<7>` or `latest` |
| Port | `8000` (HTTP) |
| Health | `GET /api/health` (the container also declares a `HEALTHCHECK` on it) |
| Persistent volume | `/data` (SQLite at `/data/vcf-doctor.db`, encryption key file next to it) |
| Replicas | **exactly 1**, `strategy: Recreate`. Two pods would double-scan and contend for SQLite. |
| User | runs as uid `10001`; set `fsGroup: 10001` so the volume is writable |

Only a main-branch push that passed every CI gate publishes an image, and
the digest that was scanned and smoke-tested is the digest that is pushed.
Release numbers continue from the highest existing `v0.1.N` tag.

## Environment variables

All optional. Anything an operator would change day to day has a GUI
control in Settings; these only set deployment-time defaults or override
them.

| Variable | Default | Purpose |
|---|---|---|
| `VCF_DOCTOR_DB_PATH` | `/data/vcf-doctor.db` | SQLite location |
| `VCF_DOCTOR_SECRET_KEY` | unset | Key for encrypting vCenter passwords and the Anthropic key at rest. Unset: a key file is generated next to the database. See [Security](SECURITY.md). |
| `ANTHROPIC_API_KEY` | unset | Enables the Claude assistant. A key entered in Settings takes precedence. |
| `VCF_DOCTOR_AUTH` | `on` | `off` disables the login page (use only behind ingress authentication) |
| `VCF_DOCTOR_ADMIN_PASSWORD` | unset | Seeds the operator password on first boot; otherwise the UI asks on first visit |
| `VCF_DOCTOR_TRUSTED_PROXIES` | unset (trust nobody) | Comma-separated IPs or CIDRs (the ingress) whose `X-Forwarded-For` and `X-Forwarded-Proto` are believed. Overrides the Settings page value. Set it to the ingress pod network so each visitor gets their own login lockout instead of sharing the ingress's. |
| `VCF_DOCTOR_LLM_MODEL` | `claude-opus-5` | Default assistant model; changeable in Settings |
| `VCF_DOCTOR_RETENTION_RECENT_DAYS` | `14` | Default retention tier: every scheduled snapshot younger than this is kept; changeable in Settings |
| `VCF_DOCTOR_RETENTION_HOURLY_DAYS` | `30` | Between recent and this age, one scheduled snapshot per hour is kept |
| `VCF_DOCTOR_RETENTION_DAILY_DAYS` | `365` | Between hourly and this age, one per day is kept; older scheduled snapshots and change-log rows are pruned. Manual snapshots are never pruned. (`VCF_DOCTOR_DEFAULT_RETENTION`, the old snapshot count, is ignored.) |
| `VCF_DOCTOR_HEALTH_WEIGHTS` | `critical=40,warning=15,info=0` | Deployment default for the health score weights; the values saved in Settings take precedence |
| `VCF_DOCTOR_MIN_INTERVAL_MINUTES` | `5` | Floor for scan intervals |
| `VCF_DOCTOR_SCHEDULER` | `on` | `off` disables scheduled scans (Scan Now still works) |
| `VCF_DOCTOR_STATIC_DIR` | `/app/static` in the image | Built frontend location |

`VCF_DOCTOR_TEST_FIXTURES` (and `VCF_DOCTOR_FIXTURES_DIR`, which points it
at a different sample set) exist for the test suite and the CI smoke test
only: they allow a connection backed by bundled sample data instead of a
vCenter. Never set them on a real deployment.

## Verifying a pulled image

The published image index carries a max-mode SLSA provenance attestation and
an SPDX SBOM, and the digest is signed keyless with cosign (identity: this
repository's `ci.yml`, logged in Rekor).

```bash
cosign verify ghcr.io/sentania-labs/vcf-doctor@<digest> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/sentania-labs/vcf-doctor/'
docker buildx imagetools inspect ghcr.io/sentania-labs/vcf-doctor:<tag> --format '{{ json .SBOM }}'
```

## Local convenience

`docker-compose.yml` builds and runs the image with a named volume for
laptop use. It is not a deployment artifact.

## Recovery

- **Lost volume**: history is gone; connections and settings must be
  re-entered. Nothing in vCenter is affected.
- **Lost encryption key, volume intact**: history is intact; re-enter each
  vCenter password (flagged "Needs password" on Connections) and the
  Anthropic key. See [Security](SECURITY.md).
- **Bad release**: re-pin the previous digest or tag and file an issue. The
  database schema is migrated forward on startup; going back a release is
  not guaranteed to be safe once a newer release has written to the volume,
  so snapshot the volume before upgrading anything you care about.
