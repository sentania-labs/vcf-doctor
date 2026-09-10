# STATUS

Current state of the product against `main`. Updated 2026-09-09. Every claim
here was checked against the code on that date; the earlier hackathon-era
version of this file is in git history.

## Working

- **PostgreSQL is the database** (#59). SQLite and its single-connection,
  process-wide write lock are gone, so the console runs with more than one
  uvicorn worker and more than one replica; one of them takes an advisory lock
  that makes it the only one running scheduled scans, and a schedule edited on
  any worker reaches it within a minute. The schema is numbered `.sql`
  migrations in `backend/app/migrations`, applied before the console by
  `python3 -m app.migrate upgrade` under an advisory lock. `docker compose up` is
  still one command: it bundles `postgres:16`, generates the database password
  into a file and runs the migration as a one-shot service. The password is
  never an environment variable, and a URL carrying one is refused at startup.
  `python3 -m app.import_sqlite` moves an existing volume's history across in
  one pass. Settings gains a read-only Database panel that reports reachable or
  not, and loses the SQLite compaction control, which autovacuum replaces.
- Multi-vCenter connections with per-connection schedules (floor 5 minutes,
  enforced by `VCF_DOCTOR_MIN_INTERVAL_MINUTES`; the API clamps lower values
  rather than rejecting them). Scan Now and manual snapshots.
- Deep inventory capture (#16) normalised to the contract in
  `docs/PROPERTIES.md`; semantic diff with per-property significance,
  hardened against type changes and odd list shapes (#35).
- 17 deterministic diagnostic checks (hosts, datastores, VMs, clusters,
  removed networks and resources). Fixture data is a test-only hook
  (`VCF_DOCTOR_TEST_FIXTURES`), not a run mode; demo mode is gone (#34).
- Severity-weighted, per-object health score on the Overview, weights
  editable in Settings (#42).
- Retention in tiered days (every scan 14 days, hourly to 30, daily to 365,
  editable in Settings), with the daily tier's day marks anchored at midnight
  in a Settings timezone seeded from `TZ` or UTC (#28),
  gzip-compressed snapshots, a persisted change log,
  and vCenter events and tasks captured per scan (#31). The old snapshot
  count setting is gone.
- Environment Changes page: estate-wide roll-up of what changed between two
  points in time across every connection (#38).
- Finding drawer shows evidence, related changes (walking back past
  identical snapshots, #39) and events in the same window. Historical recovery
  follows the [change-log contract](docs/RETENTION_EVENTS.md#change-log-persisted)
  (#41); first-observation lookup uses the SQL membership query in
  `snapshot_ids_with_finding` (#40).
- Assistant: Anthropic streaming with Explain, Investigate and Generate
  Script; scripts labelled READ ONLY or MODIFIES ENVIRONMENT and never
  executed. The mock provider is an explicit Settings choice, not an
  automatic fallback. Live-call defects fixed in #14 and #15.
- Ten pages plus Login: Overview, Health, Changes, Environment, Events,
  Inventory, Snapshots, Assistant, Connections, Settings.
- Shared operator password with first-run setup, per-client login lockout
  and a trusted proxies setting (#4, #47); vCenter passwords and the
  Anthropic key encrypted at rest (#46). The Settings password change shares
  the login lockout (#37).
- Encryption key rotation in place with the previous key at startup, plus a
  one-click migration off the generated key file; no key is ever entered in
  the interface (#48). See
  [rotation and recovery](docs/SECURITY.md#secrets-at-rest).
- Security gates in CI (lint, tests, dependency and secret scan, repo scan,
  image scan, smoke test, CodeQL); the image is signed with provenance and
  SBOM, and CI publishes the exact digest it tested with one-by-one release
  numbers (#17, #36).
- Python 3.14 base image, pip dropped from the runtime image (#52).
- Build identity is available (#60); see the [deployment contract](docs/DEPLOYMENT.md#contract).
- Live lab operation against real vCenters is confirmed. Issue #58 recorded
  1,194,962 event rows and a 393 MB events table collected over 4.6 days from
  that deployment (evidence checked 2026-09-08); #61 gave events their own
  retention (48 hours, 250,000 rows per connection, both in Settings) and
  capture checkpoints that retry gaps (#27). Reclaiming the space is
  PostgreSQL's autovacuum since #59; the bounded compaction #61 added was
  SQLite-only and is gone.
- Event capture tolerates managed object types the installed pyVmomi does
  not define, such as `ContentLibrary` on vCenter 9.1 (#65).
- 672 backend tests and 9 frontend tests pass against a real PostgreSQL 16
  (`make test`, 2026-09-10).

## Broken

- Nothing known. Open follow-ups are tracked in GitHub Issues. The login
  lockout counters are per worker process rather than per deployment now that
  more than one replica is supported (#74); see
  [Access](docs/SECURITY.md#access).

## Stretch

- SDDC Manager discovery (a disabled "Experimental" card on Connections,
  nothing behind it), NSX, topology view, correlation engine, other LLM
  providers.
- "Cross-vCenter diff" was redefined on 2026-09-02 as a time-1 vs time-2
  estate-wide view and delivered as the Environment page (#38).

## Owed to the deployment repo

- A PostgreSQL for the lab instance: start with a single pod on one Longhorn
  PVC (best-effort locality, two replicas) and graduate to CloudNativePG. Both
  shapes are in [Deployment](docs/DEPLOYMENT.md#the-database).
- `VCF_DOCTOR_DATABASE_URL` without a password, plus a Secret mounted at
  `/run/secrets/vcf-doctor-db-password`.
- Run `python3 -m app.import_sqlite --path /data/vcf-doctor.db` once against
  the old volume so the lab keeps its history, and keep the encryption key with
  it or the vCenter passwords need re-entering.
- Drop the exactly-one-replica and `strategy: Recreate` rules; more than one
  pod is supported now.
- Point `livenessProbe` at `/api/health/live` and `readinessProbe` at
  `/api/health/ready`. `/api/health` still answers liveness, so an un-updated
  manifest is safe, but it no longer tells an orchestrator when to stop sending
  traffic.
- Drop `VCF_DOCTOR_DB_PATH` (no longer read).
- Drop `VCF_DOCTOR_DEMO_MODE` from the manifest (no longer read).
- Add a SealedSecret for `VCF_DOCTOR_SECRET_KEY` so the encryption key
  survives redeploys.
- Set `VCF_DOCTOR_TRUSTED_PROXIES` to the ingress pod network so login
  lockouts are per visitor, not per ingress.
