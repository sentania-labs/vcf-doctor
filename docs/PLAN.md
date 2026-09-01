# VCF Doctor: MVP Plan, Schedulable Inventory

Companion to `startup.md`. That document is the full hackathon spec. This one
narrows it to a first shippable MVP and adds what the spec is missing for
unattended, scheduled inventory.

## MVP use case

> Add a vCenter in the GUI, set it to scan every N minutes, walk away. Come
> back, see a list of timestamped inventory snapshots, pick two, see what
> changed.

The MVP is done when that sentence is true against a real vCenter and against
fixture data with no vCenter at all.

## Gaps in startup.md this plan closes

1. **No scheduler.** The spec only has "Scan Now". Nothing runs unattended.
2. **Connections are not persisted.** The spec keeps credentials in memory,
   so a scheduled scan stops working on every process restart until an
   operator re-enters the password.
3. **No retention policy.** A scan every 15 minutes fills the database
   forever with no way to prune.

Everything else in the spec (contracts, collector, checks, semantic diff, UI
shell, demo mode) carries over unchanged.

## Deployment boundary

This repo is not responsible for deployment. It is deployed by Argo CD from
the lab deployment repo.

This repo delivers:

- one container image at `ghcr.io/sentania-labs/vcf-doctor`, tagged on merge
  to `main` (semver plus `sha-<short>`);
- a tagged GitHub release per merge to `main`;
- a README "deployment contract" section listing image name, listening port,
  environment variables, and the SQLite volume path.

This repo does not contain Kubernetes manifests, Helm charts, or Argo
configuration. `docker-compose.yml` is a local development shortcut only.

Two consequences for the application:

- **Single replica.** In-process scheduler plus SQLite means exactly one pod.
  The deployment must use a Recreate strategy, not RollingUpdate, or two pods
  will both scan and contend for the database. Documented in the README so
  the manifest author sees it. Postgres and an external job queue are
  follow-up issues.
- **Config split.** Deployment-time config comes from environment variables
  set by the deployment repo (`VCF_DOCTOR_DB_PATH`, `VCF_DOCTOR_DEMO_MODE`,
  later the LLM variables). Operator-time config (vCenter connections,
  schedules, retention) is application state in SQLite, set through the GUI,
  and survives restarts via the persistent volume. No specific vCenter is
  named in either repo.

---

## Phase 0: Freeze contracts and skeleton

Duration: about 30 minutes. One agent.

### Repository layout

As in `startup.md` section 6, plus `docs/PLAN.md` (this file).

### Contracts

The spec's `Resource`, `Finding`, `DiagnosticCheck`, `Snapshot`, and `Change`
(section 7) are adopted verbatim. Three contracts are added:

```python
class Connection:
    id: str
    name: str
    host: str
    username: str
    password: str          # stored in SQLite, lab-grade, see Risks
    verify_tls: bool
    created_at: datetime

class Schedule:
    connection_id: str
    interval_minutes: int  # floor 5
    enabled: bool
    last_run: datetime | None
    next_run: datetime | None
    last_status: Literal["ok", "error", "skipped"] | None

class ScanRun:
    id: str
    connection_id: str
    started: datetime
    finished: datetime | None
    status: Literal["running", "ok", "error", "skipped"]
    error: str | None
    snapshot_id: str | None
```

### API surface

The spec's endpoints (section 20) plus:

```text
GET    /api/connections
POST   /api/connections
GET    /api/connections/{id}
PUT    /api/connections/{id}
DELETE /api/connections/{id}
POST   /api/connections/{id}/test
GET    /api/connections/{id}/schedule
PUT    /api/connections/{id}/schedule
GET    /api/scans
GET    /api/settings
PUT    /api/settings
```

### Packaging

One container. FastAPI serves the built frontend from `/`. SQLite lives at
`VCF_DOCTOR_DB_PATH` (default `/data/vcf-doctor.db`).
`VCF_DOCTOR_DEMO_MODE=true` loads fixtures and requires no vCenter.

Exit criteria: contracts committed, empty FastAPI app boots, empty Vite app
builds, `docker compose up` serves a placeholder page.

---

## Phase 1: Parallel build

Duration: about 90 minutes. Five agents in parallel, disjoint ownership.

| Agent | Owns | Delivers |
|---|---|---|
| A: Core + scheduler | `backend/app/` except collectors, diagnostics, diff | FastAPI, SQLite persistence, scheduler, scan-run history, retention pruning, fixture collector |
| B: vSphere collector | `backend/app/collectors/vsphere/` | pyVmomi collector per spec section 8, stable resource IDs |
| C: Diagnostics + diff | `backend/app/diagnostics/`, `backend/app/diff/` | Eight checks, semantic diff with significance |
| D: Frontend | `frontend/` | Overview, Inventory, Snapshots, Changes, Health, Connections (with schedule controls), Settings |
| F: Fixtures | `fixtures/` | Snapshot A (healthy) and B (degraded), demo mode wiring |

The spec's Agent E (LLM assistant) is deferred to a later phase. It is not on
the path for this MVP.

Agent prompts are in `startup.md` sections 20 through 25. Agent A's prompt is
extended with the scheduler requirements below. Agent D starts on mock data
and is not blocked on the backend.

### Scheduler design

- **In-process APScheduler.** One process, one container, nothing else to
  keep alive. A scheduler bug costs one container restart.
- **Schedule state lives in SQLite** and is reloaded at startup. A restart
  resumes the schedule with no operator action.
- **One scan per connection at a time.** A slow vCenter cannot stack
  overlapping runs. A skipped run appears in scan history as
  `skipped: previous run still active`.
- **Every scheduled run produces a snapshot**, auto-labelled
  `Scheduled 2026-08-31 21:15`. Manual captures carry the operator's label
  and are never pruned.
- **Retention default: keep the last 96 scheduled snapshots** (24 hours at
  15 minutes). Adjustable on the Settings page.
- **Defaults on connection create: enabled, every 15 minutes.** A freshly
  added vCenter is scanning within 15 minutes with no further setup.

Every setting above has a GUI control. Nothing requires editing a file or a
database row.

Exit criteria per agent: unit tests pass in isolation; the agent's slice runs
against fixture data.

---

## Phase 2: Integration

Duration: about 30 minutes. Lead agent only; feature work stops.

Merge order: core, fixtures, collector, diagnostics and diff, frontend.

Verify this path in demo mode, then against a real vCenter:

```text
browser
  -> frontend
  -> FastAPI
  -> collector (fixture, then vSphere)
  -> snapshot stored
  -> findings computed
  -> changes computed between two snapshots
```

If any link is broken, fix it before anything else. No new features enter
during this phase.

Exit criteria: the full path works end to end in both modes from a clean
`docker compose up`.

---

## Phase 3: Seen working

Nothing is reported complete on tests or CI alone. The MVP is done when this
sequence has been performed and observed:

1. `docker compose up` from a clean checkout; open the UI cold.
2. Add the lab vCenter on the Connections page; set a 5 minute interval.
3. Watch the Snapshots page populate on its own without clicking Scan Now.
4. Power off one VM in the lab; wait one interval.
5. Changes page shows `poweredOn -> poweredOff` at medium significance.
6. Restart the container; confirm the schedule resumes with no GUI action.
7. Stop the container, start with `VCF_DOCTOR_DEMO_MODE=true`, confirm the
   same pages render from fixtures with no vCenter reachable.

---

## CI

Public repository, so all jobs run on GitHub-hosted runners. Nothing in CI
touches vCenter or the lab; the real-vCenter check in Phase 3 is manual for
the hackathon.

Jobs on pull request: pytest, frontend type-check and build, image build.
Jobs on merge to `main`: the above, plus image push to GHCR and a tagged
release.

Image scanning and similar gates are suspended under the hackathon exception
until 2026-09-02.

---

## Explicitly out of the MVP

LLM assistant, SDDC Manager discovery, NSX collector, topology view,
correlation engine, authentication, encrypted credential storage, Postgres,
external job queue. Each becomes a follow-up issue, not extra rounds on this
work.

---

## Risks

- **Credentials on disk.** Connection passwords sit in SQLite on the
  persistent volume, protected by file permissions only. Lab-grade, stated
  plainly in the README. Encryption at rest is a follow-up issue. The
  alternative (memory only, re-enter after restart) defeats scheduling.
- **Collection time on large inventories.** Mitigation: pyVmomi property
  collector with a fixed property list rather than per-object traversal;
  interval floor of 5 minutes; one-scan-at-a-time guard.
- **TLS verification.** Off by default for the lab, toggle in the Add vCenter
  dialog. Documented.
- **Frontend size.** The largest single workstream. It gets the strongest
  frontend model and begins on mocks so it is never blocked on the backend.
- **Single replica.** A deployment with two replicas will double-scan and
  contend for SQLite. Called out in the README deployment contract.
