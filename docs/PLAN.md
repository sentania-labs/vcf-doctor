# VCF Doctor: MVP Plan, Schedulable Inventory

Companion to `startup.md`. That document is the full hackathon spec. This one
narrows it to a first shippable MVP and adds what the spec is missing for
unattended, scheduled inventory.

## MVP use case

> Add one or more vCenters in the GUI, set each to scan every N minutes, walk
> away. Come back, see a list of timestamped inventory snapshots, pick two,
> see what changed, and ask Claude to explain a finding and draft an
> investigation script.

The MVP is done when that sentence is true against a real vCenter and against
fixture data with no vCenter at all, and the pages still render when no LLM
is configured.

## Demo posture

The hackathon demo runs **live** against one of Scott's VCF workload-domain
vCenters. Real inventory, real scheduled captures, a real change made in the
lab between snapshots. Fixture mode (`VCF_DOCTOR_DEMO_MODE=true`) exists as
the fallback if conference networking or the lab is unreachable; it is not
the primary path and is not what gets rehearsed first.

Consequences:

- The live vCenter path is verified before anything else in Phase 3. Fixture
  mode is verified last.
- The connection is added through the GUI on demo day like any other
  vCenter. No workload-domain name, hostname, or credential appears in this
  repo, in fixtures, or in the image.
- The "something changes" step is a rehearsed, reversible lab action
  (power off a designated VM, put a host in maintenance mode) chosen so it
  lands inside one scan interval and is undone afterwards.
- The scan interval floor of 5 minutes is too slow for a live demo. Scan
  Now remains on the top bar and produces a snapshot immediately; the
  scheduler proves itself in the background while the presenter talks.
- Fixture snapshot data should resemble a VCF workload domain (cluster
  names, vSAN datastores, NSX-backed segments) so the fallback does not
  look like a different product.

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
  `ANTHROPIC_API_KEY`, `VCF_DOCTOR_LLM_MODEL`). Operator-time config (vCenter connections,
  schedules, retention) is application state in SQLite, set through the GUI,
  and survives restarts via the persistent volume. No specific vCenter is
  named in either repo.

## Multiple vCenters

The MVP supports any number of vCenter connections from the first release.

- `Connection` is a table; `/api/connections` is list, create, update, delete.
- Each connection has its own `Schedule`, its own scan history, and its own
  one-scan-at-a-time guard. A slow vCenter never blocks the others.
- Resource IDs are namespaced by source (`host:vc01:esx03`), so two vCenters
  with a host named `esx03` never collide in a snapshot or a diff.
- **Snapshots are per connection.** One snapshot is one vCenter's inventory
  at one moment. The Changes page compares two snapshots of the same
  connection. This keeps independent schedules simple. Estate-wide
  comparison across vCenters is a follow-up.
- The top bar carries a connection selector with an "All" option. Overview,
  Health, and Inventory filter by it. Snapshots and Changes always operate
  on one connection.

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

Assistant configuration is also application state so it has a GUI:

```python
class AssistantSettings:
    enabled: bool
    model: str             # default "claude-opus-5"
    api_key_set: bool      # never returns the key itself
```

The API key may be supplied by `ANTHROPIC_API_KEY` from the deployment or
entered on the Settings page. The environment variable wins when both are
present. The key is never returned by any endpoint or written to a snapshot.

### API surface

The spec's endpoints (section 20) plus:

```text
POST   /api/assistant
GET    /api/assistant/status
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

Duration: about 90 minutes. Six agents in parallel, disjoint ownership.

| Agent | Owns | Delivers |
|---|---|---|
| A: Core + scheduler | `backend/app/` except collectors, diagnostics, diff | FastAPI, SQLite persistence, scheduler, scan-run history, retention pruning, fixture collector |
| B: vSphere collector | `backend/app/collectors/vsphere/` | pyVmomi collector per spec section 8, stable resource IDs |
| C: Diagnostics + diff | `backend/app/diagnostics/`, `backend/app/diff/` | Eight checks, semantic diff with significance |
| D: Frontend | `frontend/` | Overview, Inventory, Snapshots, Changes, Health (with Explain / Investigate / Generate Script), Assistant, Connections (with schedule controls), Settings (retention, assistant) |
| E: Assistant | `backend/app/assistant/` | Anthropic API provider, mock provider, evidence-grounded prompt, `/api/assistant` |
| F: Fixtures | `fixtures/` | Snapshot A (healthy) and B (degraded), demo mode wiring, canned assistant responses for the mock provider |

Six agents run in parallel. Token spend is authorized; speed is the
constraint, not cost.

Agent prompts are in `startup.md` sections 20 through 25. Agent A's prompt is
extended with the scheduler requirements below; Agent E's prompt is replaced
by the assistant design below. Agent D starts on mock data and is not blocked
on the backend.

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

### Assistant design (Anthropic API)

The assistant is in the MVP. The spec's stretch providers (OpenAI-compatible
endpoints, local models) are not; the provider abstraction exists so they can
be added later without touching the API surface.

- **Provider: Anthropic, via the official `anthropic` Python SDK.** Not an
  OpenAI-compatible shim. Model default `claude-opus-5`, changeable on the
  Settings page. Adaptive thinking is the model default and is left on.
- **Second provider: mock.** Returns canned, evidence-shaped answers from
  fixtures. Used in demo mode, in tests, and automatically whenever no API
  key is configured. This is how the app runs with no LLM at all.
- **Evidence package, not free chat.** Every request carries the
  `AssistantContext` from spec section 7.7: the selected finding, the changes
  around it, the related resources. The system prompt is spec section 18
  verbatim. The UI shows the evidence count ("Using: 1 finding, 3 changes,
  5 resources") on every answer.
- **Three tasks:** `explain`, `investigate`, `generate-script` (formats:
  PowerCLI, Python, shell, REST). Scripts are returned as text for operator
  review; nothing is ever executed. Script output is split into
  read-only and modifying sections so the UI can badge them differently.
- **Streaming responses**, so a long script does not sit behind a spinner
  or trip an HTTP timeout. The backend streams to the browser as
  server-sent events.
- **Refusal handling.** The API can return a `refusal` stop reason. The
  assistant reports it plainly in the UI rather than showing an empty
  answer.
- **Failure mode is degradation, not breakage.** No key, bad key, network
  down, rate limited: the Health and Changes pages are unaffected, the
  Explain button shows why the assistant is unavailable, and the mock
  provider remains selectable from Settings for demos.
- **Nothing sensitive leaves the box except the evidence package.** No
  credentials, no connection details beyond hostnames already in the
  resource data.

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
2. Add the workload-domain vCenter on the Connections page; set a 5 minute
   interval. Click Scan Now; confirm a snapshot with real inventory.
3. Watch the Snapshots page populate on its own without clicking Scan Now.
4. Power off one VM in the lab; wait one interval.
5. Changes page shows `poweredOn -> poweredOff` at medium significance.
6. Restart the container; confirm the schedule resumes with no GUI action.
7. Open the powered-off VM finding on the Health page, click Explain;
   confirm a streamed answer from Claude that references only the supplied
   evidence and shows the evidence count.
8. Click Generate Script, choose PowerCLI; confirm a reviewable script with
   read-only and modifying sections badged separately, and no execute
   control anywhere.
9. Remove the API key in Settings; confirm Health and Changes still work
   and the Explain button explains why the assistant is unavailable.
10. Stop the container, start with `VCF_DOCTOR_DEMO_MODE=true`, confirm the
    same pages render from fixtures with no vCenter reachable and the
    assistant answers from the mock provider.

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

Assistant tests run against the mock provider. No Anthropic API key exists
in CI.

---

## Explicitly out of the MVP

OpenAI-compatible and local LLM providers, SDDC Manager discovery, NSX
collector, topology view, correlation engine, cross-vCenter snapshot
comparison, authentication, encrypted credential storage, Postgres,
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
- **Live demo against real infrastructure.** The primary demo depends on
  the lab vCenter being reachable from the conference floor. Fixture mode
  is the rehearsed fallback, one environment variable away, with data that
  looks like a workload domain.
- **Assistant availability on demo day.** Conference networks fail. The
  mock provider is a one-click switch on the Settings page, and the demo
  script has a mock-provider path rehearsed alongside the live one.
- **API key handling.** The key lives in an environment variable or in
  SQLite alongside the vCenter credentials, same lab-grade caveat. It is
  never logged, never returned by the API, never in a snapshot.
