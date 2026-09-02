# Security

## Access

A single shared operator password gates the UI and API (session cookie, 7
days, PBKDF2 hash, signing secret rotated on password change). Failed
sign-ins are counted per client address: five, then an exponential wait
capped at a minute, reported back as `Retry-After` and counted down on the
login page. A process-wide ceiling (30 failures a minute across every
address) backstops guessing from many addresses. The client address is the
TCP peer unless that peer is a trusted proxy (Settings, or
`VCF_DOCTOR_TRUSTED_PROXIES`), in which case the rightmost untrusted
`X-Forwarded-For` hop is used. Nothing is trusted by default, so behind an
ingress every visitor shares the ingress's address and one lockout; trust
the ingress and each visitor gets their own.

The console is read-only against vCenter: it only ever reads inventory and
events, and generated scripts are shown, never executed. Per-user accounts
are a follow-up issue.

`VCF_DOCTOR_AUTH=off` removes the login page. Use it only behind ingress
authentication.

## Secrets at rest

vCenter passwords and the Anthropic key (when entered via the GUI) are
encrypted (Fernet, authenticated) before they reach SQLite. The encryption
key comes from `VCF_DOCTOR_SECRET_KEY` when set (in the lab a sealed
Kubernetes secret, so it survives redeploys); otherwise the app generates
`vcf-doctor.key` next to the database on first start, owner-only
permissions, and reuses it. Rows written by older builds are encrypted on
the next startup. Settings shows which key source is active, never the key.

Losing the key means re-entering the vCenter passwords and the API key,
nothing worse: affected connections are flagged "Needs password" on the
Connections page until you do. Rotate the same way: set the new key,
restart, re-enter.

## Browser headers

Every response carries `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
`Permissions-Policy: camera=(), microphone=(), geolocation=()` and a
same-origin Content-Security-Policy (`default-src 'self'`, no inline or
external scripts, `frame-ancestors 'none'`; inline styles stay allowed
because React and Tailwind set style attributes). `/api` responses are
`Cache-Control: no-store`. `Strict-Transport-Security` (one year) is sent
only when a trusted proxy reports `X-Forwarded-Proto: https`, so a plain-http
lab deployment is never locked out and a visitor cannot switch it on by
sending the header themselves.

## Gates in CI

All GitHub-hosted, all via `make`, so a developer and CI run the same
command:

| Gate | Target | Blocks a PR / publish on |
|---|---|---|
| Lint and tests | `make lint`, `make test` | ruff or TypeScript errors; any failing backend test |
| Dependency audit | `make scan-deps` | any known CVE in the backend environment (pip-audit); HIGH+ in frontend runtime dependencies (npm audit) |
| Secret scan | `make scan-secrets` | any secret anywhere in git history (gitleaks) |
| Repo scan | `make scan-fs` | HIGH/CRITICAL fixable CVE in `uv.lock` / `package-lock.json`; Dockerfile misconfiguration (trivy) |
| Image scan | `make scan-image IMAGE=...` | HIGH/CRITICAL fixable CVE in the built image: OS packages, Python and npm packages (trivy) |
| Dependency review | GitHub action, PRs only | a newly added dependency with a HIGH+ advisory |
| CodeQL | `codeql.yml` | Python and TypeScript static analysis; PRs, main, weekly |
| Smoke test | `ci.yml` image job | container does not boot, fixture scan fails, auth bypass, path traversal, missing headers, or not running as uid 10001 |

Only a main-branch push that passed every gate publishes, and it publishes
the exact digest that was scanned and smoke-tested. Dependabot opens weekly
grouped PRs for pip, npm, GitHub Actions and the base image digests. The
hackathon exception that once suspended image scanning is closed; these
gates are the release discipline.

## What ships

Base images are pinned by digest and `uv` by version. The container runs as
uid 10001 and declares a `HEALTHCHECK` on `/api/health`. The published image
index carries a max-mode SLSA provenance attestation and an SPDX SBOM, and
the digest is signed keyless with cosign. See [Deployment](DEPLOYMENT.md)
for the verification commands.

## Running the scans locally

`make scan` runs deps + secrets + repo scan; `make image && make scan-image
IMAGE=vcf-doctor:local` scans the built image. Fast path: put `trivy` and
`gitleaks` on your PATH (Debian/Ubuntu: `sudo apt install trivy` from Aqua's
apt repo, or the release tarballs from github.com/aquasecurity/trivy and
github.com/gitleaks/gitleaks). Without them, the targets fall back to the
pinned scanner containers (`aquasec/trivy:0.74.0`,
`ghcr.io/gitleaks/gitleaks:v8.30.1`), run as your user with the trivy DB
cached in `~/.cache/trivy` (override with `TRIVY_CACHE=...`). Results are
identical either way because both read `trivy.yaml` and `.trivyignore`.

## Accepting a finding

Unfixed CVEs never gate (`ignore-unfixed` in `trivy.yaml`);
`trivy image --ignore-unfixed=false` lists them on demand. A fixable finding
is fixed by bumping (base image digest, `uv lock`, `npm audit fix`), not
ignored. If one genuinely cannot be fixed yet, add it to `.trivyignore` with
the CVE id, a comment giving the reason, and an expiry
(`CVE-XXXX-NNNNN exp:YYYY-MM-DD`); after that date it fails the gate again.

## Reporting

Open a GitHub issue. If the finding is sensitive, say so in the issue title
without details and a maintainer will follow up privately.
