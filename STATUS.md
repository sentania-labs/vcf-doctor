# STATUS

Current state of the product against `main`, plus the roadmap: what is next
and why, in priority order. Updated 2026-09-15. Every claim here was checked
against the code and the open GitHub issues on that date; the earlier
hackathon-era version of this file is in git history.

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

## Roadmap

Updated 2026-09-15. Next in priority order, with the reason. All eight items
below are open GitHub issues; there is no other planning material behind this
list.

1. **Console strands the operator during a database outage (#75).** Reload
   the browser while PostgreSQL is down and the operator lands on a login
   page that blames "the container" when the container is fine; there is no
   path to the Database panel that would show the real problem. This goes
   first because it actively misleads whoever is troubleshooting at the
   worst moment to be misled.
2. **Login lockout counters are per worker, not per deployment (#74).**
   PostgreSQL (#59, shipped) lifted the one-replica rule, so a deployment can
   now run several workers and several pods, and each one counts login
   failures separately. A password guesser gets that many times the free
   attempts, once per worker. Follows #75 because it is also
   a login-path correctness gap, smaller blast radius.
3. **Out-of-range event policy values are clamped silently instead of
   refused (#76).** A typo in an event retention environment variable (for
   example a row cap of 100 instead of 100000) now runs quietly under a
   different limit instead of failing startup. Silent misconfiguration is
   worse than a hard failure an operator can see and fix.
4. **SQLite importer's missing-column fallback is wider than any shipped
   schema needs (#77).** The one-time SQLite to PostgreSQL importer
   (`app.import_sqlite`) is more permissive than it needs to be; if it ever
   applies to a resource-count column it would import a snapshot showing zero
   resources instead of the real count. Narrow the fallback to the two
   columns that actually varied. Lower urgency: it only runs once, during a
   lab's move off SQLite.
5. **Event capture leaves a wrong placeholder type registered until restart
   (#66).** Follow-on from the #65 fix that lets capture survive a managed
   type pyVmomi does not know: an unknown event class hits the same code path
   and registers a wrong placeholder, so later drains of that type fail until
   the process restarts. Narrow, cosmetic in the failure mode (a restart
   clears it), so it sits behind the correctness items above.
6. **Settings rotation refresh has no test guarding it against discarding
   unsaved edits (#71, #73).** The #68 fix that stops a key rotation from
   silently wiping an operator's unsaved Settings edits works, but nothing in
   the test suite would catch someone undoing it later. This is test debt,
   not a live defect, so it is scheduled behind everything above.

Deferred:

- **Per-user accounts (#49).** Today one shared operator password gates the
  console; per-user accounts, or delegating sign-in to an ingress or IdP,
  would give an audit trail of who did what and let one password be revoked
  without rotating it for everyone. Deliberately deferred: it is an
  authentication redesign, not a fix to something broken, and needs a captain
  decision on native accounts versus delegated sign-in before work starts.

## Stretch

- SDDC Manager discovery (a disabled "Experimental" card on Connections,
  nothing behind it), NSX, topology view, correlation engine, other LLM
  providers. These were named in the original hackathon plan
  (`docs/ORIGINAL_PLAN.md`) and have no issue or schedule behind them; they
  stay parked unless the captain asks for one.
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
