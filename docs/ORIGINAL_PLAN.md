> Historical (2026-09-02): this is the original hackathon plan. It does not describe the current product. Kept for the record; the shipped product is described in the [README](../README.md).
>
> **Decisions since this was written**
>
> - 2026-09-02: demo mode retired (#34). Bundled fixture data is a test-only hook (`VCF_DOCTOR_TEST_FIXTURES`), not a run mode.
> - Authentication and encryption at rest were originally out of scope and were shipped: shared operator password (#4), secrets encrypted at rest (#46), per-client login lockout and trusted proxies (#47).
> - Retention moved from a snapshot count to tiered days, 14/30/365, editable in Settings (#31).
> - Cross-vCenter comparison was redefined as an estate-wide time-1 vs time-2 view and delivered as the Environment page (#38).
> - The Investigate action is an LLM prompt over the recorded evidence. The guided, deterministic evidence collection described in the vision is not built.

# VCF Doctor

**Tagline:** What’s wrong, what changed, and what should I do about it?

## 1. Product Vision

VCF Doctor is a deterministic VMware Cloud Foundation diagnostic and historical-analysis application with an optional LLM-assisted interpretation layer.

It connects to a VMware environment, discovers infrastructure, captures normalized snapshots, evaluates deterministic health checks, compares current and historical state, and presents findings in a polished web interface.

An optional LLM can:

* explain findings;
* correlate multiple findings;
* recommend investigation steps;
* generate PowerCLI, Python, shell, REST, or other remediation scripts;
* answer questions using only supplied environment evidence.

The LLM does **not** determine whether infrastructure is healthy.

The deterministic engine owns facts.

The LLM interprets those facts.

---

# 2. Hackathon Goal

At the end of three hours, demonstrate this workflow:

1. Open VCF Doctor.
2. Connect to a vCenter.
3. Automatically inventory:

   * vCenter;
   * datacenters;
   * clusters;
   * ESXi hosts;
   * VMs;
   * datastores;
   * networks.
4. Run deterministic health checks.
5. Capture a named snapshot.
6. Make one or more changes in the lab.
7. Capture another snapshot.
8. Show a semantic "What Changed?" view.
9. Select a finding.
10. Ask an optional LLM to:

    * explain it;
    * suggest investigation steps;
    * generate a remediation/investigation script.
11. Show that VCF Doctor works without an LLM configured.

Stretch goal during hours 4–5:

12. Bootstrap from SDDC Manager and discover vCenter/NSX targets.
13. Add one additional VCF collector, preferably NSX or SDDC Manager.
14. Add timeline/correlation views.

---

# 3. Design Principles

## 3.1 Deterministic Core

All factual assertions must originate from API data and deterministic rules.

Example:

```text
Host esx03 is disconnected.
```

is determined by:

```text
connectionState == "disconnected"
```

not by an LLM.

## 3.2 Evidence-First AI

The assistant receives structured evidence.

Example:

```json
{
  "finding": {
    "type": "HOST_DISCONNECTED",
    "severity": "critical",
    "resource": {
      "type": "host",
      "name": "esx03"
    },
    "evidence": {
      "connectionState": "disconnected",
      "previousConnectionState": "connected",
      "firstObserved": "2026-08-31T20:14:00"
    }
  }
}
```

The assistant may interpret this evidence but may not invent additional environmental state.

## 3.3 Read-Only MVP

VCF Doctor does not execute remediation during the hackathon.

Generated commands/scripts are artifacts for operator review.

## 3.4 Graceful Capability Degradation

The application must support:

```text
vCenter only
```

without requiring VCF.

Later discovery sources may provide richer functionality.

## 3.5 Provider-Neutral AI

VCF Doctor should support an optional provider abstraction.

MVP requirement:

```text
Anthropic API
```

Stretch providers:

* OpenAI
* OpenRouter
* local Ollama/vLLM/LM Studio
* other OpenAI-compatible endpoints

---

# 4. Architecture

```text
                     ┌─────────────────┐
                     │   Slick Web UI   │
                     └────────┬────────┘
                              │
                     ┌────────▼────────┐
                     │ Application API │
                     └────────┬────────┘
                              │
          ┌───────────────────┼──────────────────┐
          │                   │                  │
          ▼                   ▼                  ▼
   Discovery Engine     Diagnostic Engine     Assistant
          │                   │                  │
          ▼                   ▼                  ▼
   Target Registry       Finding Engine       LLM Provider
          │
          ▼
   Collector Registry
          │
     ┌────┼────┐
     ▼    ▼    ▼
  vSphere NSX SDDC...
          │
          ▼
   Normalized Object Graph
          │
     ┌────┴───────────┐
     ▼                ▼
 Snapshot Store    Current State
     │
     ▼
 Semantic Diff Engine
```

---

# 5. Recommended Technology Stack

Optimize for development speed.

## Backend

Python 3.14+

FastAPI

Pydantic

SQLite

SQLModel or SQLAlchemy only if useful; plain SQLite is acceptable.

VMware connectivity:

* pyVmomi;
* vSphere REST where convenient.

## Frontend

Preferred:

* React;
* TypeScript;
* Vite;
* Tailwind;
* shadcn/ui.

Alternative:

Next.js if the agent ecosystem builds it faster.

## Visualization

* Recharts for charts;
* Lucide icons;
* optional React Flow for topology during stretch goals.

## AI

Simple adapter using:

```text
POST /v1/chat/completions
```

or equivalent provider abstraction.

---

# 6. Repository Structure

```text
vcf-doctor/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── discovery/
│   │   ├── collectors/
│   │   ├── normalize/
│   │   ├── diagnostics/
│   │   ├── snapshots/
│   │   ├── diff/
│   │   ├── assistant/
│   │   └── models/
│   └── tests/
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── api/
│   │   └── types/
│   └── ...
│
├── fixtures/
├── docs/
│   ├── ARCHITECTURE.md
│   └── DEMO.md
├── docker-compose.yml
└── README.md
```

---

# 7. Core Contracts

These interfaces should be defined before agents branch into parallel work.

## 7.1 Resource

```python
class Resource:
    id: str
    type: str
    name: str
    source: str
    parent_id: str | None
    properties: dict
    relationships: list
```

Example:

```json
{
  "id": "host:vc01:esx03",
  "type": "host",
  "name": "esx03.lab.local",
  "source": "vcenter:vc01",
  "parent_id": "cluster:vc01:wld01",
  "properties": {
    "connectionState": "connected",
    "powerState": "poweredOn",
    "maintenanceMode": false,
    "cpuMhz": 64000,
    "memoryBytes": 824633720832
  }
}
```

---

## 7.2 Collector Interface

```python
class Collector:
    id: str
    resource_types: list[str]

    def test_connection(self) -> ConnectionResult:
        ...

    def collect(self) -> list[Resource]:
        ...
```

---

## 7.3 Finding

```python
class Finding:
    id: str
    check_id: str
    severity: Literal["info", "warning", "critical"]
    title: str
    summary: str
    resource_id: str | None
    evidence: dict
    recommendation: str | None
```

---

## 7.4 Diagnostic Check

```python
class DiagnosticCheck:
    id: str
    name: str
    description: str

    def evaluate(
        self,
        resources: list[Resource]
    ) -> list[Finding]:
        ...
```

---

## 7.5 Snapshot

```python
class Snapshot:
    id: str
    created_at: datetime
    label: str
    source_ids: list[str]
    resources: list[Resource]
```

---

## 7.6 Change

```python
class Change:
    change_type: Literal["added", "removed", "modified"]
    resource_id: str
    resource_type: str
    resource_name: str
    property_changes: dict
    significance: Literal["low", "medium", "high"]
```

---

## 7.7 Assistant Evidence Package

```python
class AssistantContext:
    question: str
    findings: list[Finding]
    changes: list[Change]
    resources: list[Resource]
    allowed_actions: list[str]
```

The prompt must instruct the LLM:

```text
Only make factual statements about the environment that are supported by the supplied evidence.
```

---

# 8. MVP vCenter Collector

Collect:

## vCenter

* name;
* version;
* build.

## Datacenters

* name.

## Clusters

* name;
* host count;
* DRS status if easy;
* HA status if easy.

## Hosts

* name;
* connection state;
* power state;
* maintenance mode;
* CPU capacity;
* memory capacity.

## VMs

* name;
* power state;
* host;
* cluster;
* networks;
* datastore relationships.

## Datastores

* name;
* capacity;
* free space;
* accessibility.

## Networks

* name;
* type where available.

Do not attempt exhaustive VMware modeling.

---

# 9. MVP Diagnostic Checks

Implement approximately 8–12 checks.

Required:

```text
HOST_DISCONNECTED
HOST_MAINTENANCE_MODE
HOST_NOT_RESPONDING
DATASTORE_HIGH_USAGE
DATASTORE_INACCESSIBLE
VM_POWERED_OFF
VM_ORPHANED_OR_INACCESSIBLE
CLUSTER_HOST_COUNT_CHANGE
NETWORK_REMOVED
RESOURCE_REMOVED
```

Useful additional checks:

```text
SNAPSHOT_STALE
HOST_COUNT_LOW
VCENTER_CONNECTION_FAILURE
```

The goal is a visibly interesting dashboard, not comprehensive monitoring.

---

# 10. Semantic Diff

Do not perform a raw JSON diff.

Compare resources by deterministic IDs.

Classify:

```text
added
removed
modified
```

Ignore noisy properties.

Important tracked properties:

### Host

```text
connectionState
powerState
maintenanceMode
cluster
```

### VM

```text
powerState
host
networks
datastores
```

### Datastore

```text
accessible
capacity
freeSpace
```

### Network

```text
exists
```

### Cluster

```text
host membership
```

Assign significance:

```text
host disconnected       high
network removed         high
datastore inaccessible  high
VM host migration       low
VM power change         medium
maintenance mode        medium
```

---

# 11. UI Specification

The UI is part of the hackathon entry.

It should feel like a product, not a developer demo.

## Layout

Persistent left navigation:

```text
VCF Doctor

Overview
Health
Changes
Inventory
Snapshots
Assistant

Connections
Settings
```

Top bar:

```text
Centennial Lab
Last scan: 14 seconds ago
● Connected
[Scan Now]
```

---

# 12. Overview Page

Hero health score/card:

```text
Environment Health

92

47 checks passed
2 warnings
1 critical
```

Cards:

```text
Resources
124

Hosts
5 / 5 connected

VMs
42 powered on

Storage
71% available
```

Important findings:

```text
CRITICAL
ESX03 disconnected
First detected 2m ago

WARNING
Datastore01 91% full
```

Recent changes:

```text
ESX03
connected → disconnected

web01
ESX02 → ESX04

app-network
removed
```

---

# 13. Health Page

Filterable findings:

```text
All | Critical | Warning | Info
```

Finding cards.

Selecting a finding opens a details drawer:

```text
ESX03 disconnected

Severity: Critical
Detected: 2:14 PM

Evidence

connectionState
connected → disconnected

powerState
poweredOn

Previous healthy snapshot
1:55 PM

Related changes
NSX transport state ...
```

Buttons:

```text
Explain
Investigate
Generate Script
```

---

# 14. Changes Page — Time Machine

Header:

```text
What Changed?
```

Snapshot selectors:

```text
FROM
Baseline — 1:55 PM

TO
Current — 2:15 PM
```

Summary:

```text
12 changes

3 high impact
4 medium
5 informational
```

Timeline/cards.

Example:

```text
HIGH

ESX03 connection state

connected
   ↓
disconnected
```

---

# 15. Snapshots Page

Display:

```text
Current
Demo Baseline
Before Upgrade
Monday Morning
```

Button:

```text
[Capture Snapshot]
```

Allow label entry.

---

# 16. Inventory Page

Tree or grouped cards:

```text
vCenter
  Datacenter
    Cluster
      Host
        VM
```

Stretch:

interactive topology using React Flow.

---

# 17. Assistant UI

Right-side drawer or dedicated page.

Example starter prompts:

```text
Explain this finding
What changed around this failure?
How should I investigate this?
Generate a PowerCLI investigation script
Generate REST API commands
```

Show a visible evidence indicator:

```text
Using:
1 finding
3 related changes
5 resources
```

This reinforces evidence-grounded AI.

---

# 18. LLM Safety/System Prompt

Use this as the assistant system prompt:

```text
You are the VCF Doctor assistant.

VCF Doctor has supplied deterministic observations from a VMware environment.

Treat supplied findings, resources, changes, and evidence as authoritative.

Do not invent VMware resources, states, versions, alarms, configuration values, log entries, API responses, or environmental facts that do not appear in the evidence.

Clearly distinguish:

1. Observed facts
2. Inferences
3. Suggested investigation
4. Suggested remediation

When generating scripts or commands:

- make them reviewable rather than automatically executable;
- prefer read-only investigation before modification;
- clearly identify commands that modify infrastructure;
- include comments explaining the purpose of each important command;
- do not assume credentials or endpoints that are not provided.

If evidence is insufficient, state what additional evidence should be collected.
```

---

# 19. Three-Hour Execution Plan

## T+0 — T+15

Freeze interfaces and repository.

Create branches/worktrees.

Agents begin immediately.

Do not spend kickoff debating architecture.

---

# 20. Parallel Workstreams

## Agent A — Core Backend / Orchestrator

Own:

```text
FastAPI
models
collector registry
diagnostic registry
snapshot API
diff API
```

Avoid UI and VMware API specifics.

### Prompt

```text
Build the core backend for VCF Doctor from the supplied specification.

You own backend architecture and API orchestration.

Implement:

- FastAPI application;
- Pydantic models for Resource, Finding, Snapshot, Change;
- Collector interface and registry;
- DiagnosticCheck interface and registry;
- SQLite snapshot persistence;
- semantic diff engine;
- endpoints for:
  GET /api/health
  GET /api/resources
  GET /api/findings
  GET /api/snapshots
  POST /api/snapshots
  GET /api/changes
  POST /api/scan

Use clean modular Python.

Do not implement VMware-specific API access.
Create a fixture collector so the application functions without vCenter.

Optimize for hackathon delivery rather than enterprise abstraction.

Add tests around snapshot persistence and semantic diff.

Do not modify frontend/.
```

---

# 21. Agent B — vSphere Collector

Own:

```text
collectors/vsphere/
```

### Prompt

```text
Implement the VCF Doctor vSphere collector.

Use pyVmomi or vSphere REST APIs, whichever yields the simplest reliable implementation.

Input:

- vCenter hostname
- username
- password
- TLS verification option

Collect and normalize:

- vCenter identity/version
- datacenters
- clusters
- ESXi hosts
- VMs
- datastores
- networks

Return VCF Doctor Resource objects conforming exactly to the supplied schema.

Resource IDs must be stable between scans.

Capture useful relationships:

VM -> host
VM -> network
VM -> datastore
host -> cluster
cluster -> datacenter

Important host properties:

connectionState
powerState
maintenanceMode

Important VM properties:

powerState
host
networks

Important datastore properties:

capacity
freeSpace
accessible

Do not build UI.
Do not build snapshot persistence.
Do not implement diagnostics.

Provide fixture tests or mocks where possible.
```

---

# 22. Agent C — Diagnostics + Diff Intelligence

Own:

```text
diagnostics/
diff/
```

### Prompt

```text
Implement deterministic diagnostic checks and semantic change classification for VCF Doctor.

All findings must derive from normalized Resource data.

Implement at minimum:

HOST_DISCONNECTED
HOST_MAINTENANCE_MODE
DATASTORE_HIGH_USAGE
DATASTORE_INACCESSIBLE
VM_POWERED_OFF
CLUSTER_HOST_COUNT_CHANGE
NETWORK_REMOVED
RESOURCE_REMOVED

Each check returns structured Finding objects containing:

- check_id
- severity
- title
- summary
- resource_id
- evidence
- deterministic recommendation

Also implement semantic significance classification for snapshot differences.

Examples:

host connection state change -> high
datastore accessibility loss -> high
network removal -> high
VM migration -> low
VM power state change -> medium
maintenance mode -> medium

Ignore noisy/unimportant property changes.

Add unit tests using synthetic normalized resource graphs.

Do not call any LLM.
```

---

# 23. Agent D — Slick UI

This deserves your strongest frontend-oriented model.

Own:

```text
frontend/
```

### Prompt

```text
Build a polished production-looking web UI for an application named VCF Doctor.

Technology:

React
TypeScript
Vite
Tailwind
shadcn/ui
Lucide icons
Recharts where useful

Visual character:

- modern infrastructure operations console;
- polished enough to demo on a large conference screen;
- clean dark/light neutral palette;
- strong typography;
- generous spacing;
- not excessively dense;
- subtle animation;
- excellent empty/loading/error states.

Navigation:

Overview
Health
Changes
Inventory
Snapshots
Assistant
Connections
Settings

Build these pages:

OVERVIEW
- health score
- critical/warning/pass counts
- infrastructure summary cards
- latest findings
- latest changes
- last scan indicator
- Scan Now button

HEALTH
- filterable finding list
- severity badges
- resource information
- finding detail drawer
- Explain / Investigate / Generate Script buttons

CHANGES
- FROM snapshot selector
- TO snapshot selector
- semantic changes grouped by significance
- visually show old -> new state

SNAPSHOTS
- snapshot cards
- capture snapshot dialog
- snapshot labels/timestamps

INVENTORY
- grouped or hierarchical environment inventory

ASSISTANT
- conversation UI
- evidence context display
- predefined prompts

CONNECTIONS
- Add vCenter dialog:
  hostname
  username
  password
  TLS verify
- future SDDC Manager option shown but disabled or marked experimental

Use mock API data initially, but isolate all API access in a small client layer so real endpoints can replace mocks.

Prioritize visual quality and demo polish.

Do not modify backend/.
```

---

# 24. Agent E — LLM Assistant

Own:

```text
backend/app/assistant/
```

### Prompt

```text
Implement the optional VCF Doctor AI assistant.

Requirements:

VCF Doctor must run without an LLM configured.

Implement a provider abstraction supporting an OpenAI-compatible endpoint.

Configuration:

VCF_DOCTOR_LLM_BASE_URL
VCF_DOCTOR_LLM_API_KEY
VCF_DOCTOR_LLM_MODEL

Implement:

POST /api/assistant

Request includes:

question
findings
changes
resources

Construct an evidence-grounded prompt.

The model must distinguish:

Observed facts
Inference
Investigation
Remediation

The model must be explicitly prohibited from inventing environmental facts.

Support common tasks:

explain
investigate
generate-script

For generate-script, accept a requested format:

PowerCLI
Python
shell
REST

Never execute generated scripts.

Return structured output where possible.

Also implement a mock/no-LLM provider for demos and tests.

Do not modify frontend.
```

---

# 25. Agent F — Fixtures / Demo Harness

This can save the whole hackathon if conference networking or credentials misbehave.

### Prompt

```text
Build a deterministic demo environment and fixture generator for VCF Doctor.

Create two realistic normalized VCF/vSphere snapshots:

Snapshot A: healthy baseline

Snapshot B:
- one host changes connected -> disconnected
- one VM migrates hosts
- one datastore rises above 90% usage
- one network disappears
- one VM powers off

Ensure the fixture data exercises:

diagnostic checks
semantic diff
health dashboard
change timeline
assistant evidence packages

Provide a simple CLI or startup flag:

VCF_DOCTOR_DEMO_MODE=true

In demo mode the application should load fixture data instead of requiring vCenter.

Do not build application architecture outside fixture/demo support.
```

---

# 26. Integration Checkpoint — T+90 Minutes

Stop feature work briefly.

Merge or cherry-pick:

```text
core backend
vSphere collector
UI shell
fixture mode
```

Verify this path:

```text
browser
  ↓
frontend
  ↓
FastAPI
  ↓
fixture collector
  ↓
resources
  ↓
findings
```

If broken, fix this before continuing.

---

# 27. T+90–T+150

Focus on the complete vertical demo.

Required workflow:

```text
Connect
   ↓
Scan
   ↓
Health
   ↓
Capture Baseline
   ↓
Scan Again
   ↓
What Changed?
   ↓
Explain
```

Everything else is secondary.

---

# 28. T+150–T+180

Freeze feature development.

Only:

* UI polish;
* integration bugs;
* demo reliability;
* README;
* demo script;
* screenshots;
* fallback demo mode.

Do not start a major new feature during the final 30 minutes.

---

# 29. Definition of Done at Three Hours

A successful MVP satisfies:

* [ ] Application starts with one command.
* [ ] Demo mode works with no VMware dependencies.
* [ ] User can configure vCenter.
* [ ] vCenter scan produces normalized inventory.
* [ ] Inventory is visible.
* [ ] At least eight deterministic checks work.
* [ ] Findings dashboard looks polished.
* [ ] Snapshot can be captured.
* [ ] Two snapshots can be compared.
* [ ] Changes are semantic rather than raw JSON.
* [ ] Change significance is displayed.
* [ ] Optional LLM can explain findings.
* [ ] Optional LLM can generate an investigation script.
* [ ] Application operates without LLM connectivity.
* [ ] Generated remediation cannot execute automatically.
* [ ] Demo can be completed from fixture data if lab connectivity fails.

---

# 30. Hour 4 Roadmap

Once the MVP is stable, split again.

## Track A — SDDC Manager Discovery

Add:

```text
SDDC Manager connection
   ↓
workload domains
   ↓
vCenters
   ↓
NSX Managers
   ↓
optional credential import
```

### Prompt

```text
Implement an experimental SDDC Manager discovery provider for VCF Doctor.

The user supplies:

hostname
username
password
TLS settings

Discover:

- workload domains
- vCenter targets
- NSX Manager targets
- ESXi targets where useful

Represent discovered systems through the existing target registry.

If SDDC Manager APIs permit retrieving managed credentials, make credential import an explicit opt-in action.

Never persist plaintext credentials in snapshots.

Keep credentials in an in-memory/session credential store unless an existing secure credential abstraction already exists.

The UI/backend must distinguish:

discovered target
authenticated target
unavailable target

Do not redesign the existing Resource model.
```

---

# 31. Track B — NSX Collector

### Prompt

```text
Add an NSX collector to VCF Doctor using the existing Collector interface.

Collect a deliberately small useful subset:

- NSX Manager identity
- transport nodes
- segments
- Tier-0 gateways
- Tier-1 gateways

Normalize these into Resource objects.

Implement deterministic checks for:

- transport node degraded/down
- segment realization failure where API data permits
- T0/T1 non-up state where meaningful

Add relationships where straightforward:

segment -> transport infrastructure
T1 -> T0

Do not attempt exhaustive NSX modeling.
```

---

# 32. Track C — Correlation Engine

### Prompt

```text
Build a deterministic correlation layer for VCF Doctor.

Goal:

associate findings with changes that occurred within a configurable time window.

Example:

Finding:
HOST_DISCONNECTED esx03

Nearby changes:
host MTU changed
transport-node state changed
VMs migrated

Return structured correlation candidates.

Do not claim causation.

Use language such as:

related change
temporally correlated
potentially relevant

The LLM may later interpret correlations, but this engine must remain deterministic.
```

---

# 33. Track D — Topology Visualization

### Prompt

```text
Extend the VCF Doctor Inventory page with an interactive topology visualization using React Flow.

Display:

vCenter
datacenter
cluster
host
VM
datastore
network

Use the existing resource relationship model.

Features:

- pan/zoom
- resource type icons
- health coloring/badges
- click resource -> details drawer
- highlight resources related to a selected finding

Optimize for conference demo visual impact rather than supporting huge production environments.
```

---

# 34. Hour 5 Roadmap

Choose only the most demo-worthy working features.

Priority:

1. SDDC Manager bootstrap
2. topology
3. deterministic correlation
4. NSX
5. deeper AI

Do not chase completeness.

---

# 35. Stretch: "Investigate" Workflow

The best stretch capability is not more chat.

It is guided evidence collection.

Example:

```text
Finding:
HOST_DISCONNECTED

[Investigate]
```

Doctor performs deterministic follow-up checks:

```text
vCenter reports host disconnected
Host previously connected
18 VMs were resident
Datastore paths affected
Management network relationship found
```

Then AI receives the expanded evidence.

### Prompt

```text
Implement an Investigation framework.

A finding may register deterministic follow-up probes.

Example:

HOST_DISCONNECTED may gather:

- previous host state
- cluster membership
- affected VMs
- accessible datastores
- recent related snapshot changes

Return an Investigation object containing:

finding
evidence
related_resources
related_changes
recommended_next_probes

Do not use an LLM to collect evidence.

The assistant may consume the resulting Investigation object afterward.
```

---

# 36. Stretch: Script Generation

Allow operator selection:

```text
PowerCLI
Python
govc
curl/REST
```

Output:

```text
INVESTIGATION
```

and separately:

```text
MODIFICATION
```

Make modifying commands visually dangerous.

Example:

```text
READ ONLY
```

green/neutral badge.

```text
MODIFIES ENVIRONMENT
```

warning badge.

---

# 37. Master Prompt for Lead Agent

Use one primary agent as architectural lead.

```text
You are the technical lead for a five-hour hackathon project named VCF Doctor.

Read docs/SPEC.md before making architectural decisions.

Your responsibilities:

1. protect the contracts defined in the specification;
2. integrate work from parallel agents;
3. prevent unnecessary abstraction;
4. keep the application runnable;
5. prioritize the end-to-end demo path above all else.

The required demo path is:

connect or demo mode
-> scan
-> health findings
-> capture snapshot
-> introduce changed state
-> second snapshot
-> What Changed?
-> AI explanation
-> generated investigation script

Do not allow parallel agents to redefine shared contracts without compelling reason.

At every integration point prefer working code over architectural elegance.

During the first three hours, reject feature additions that threaten the required vertical slice.

During hours four and five, prioritize:

SDDC Manager discovery
topology visualization
correlation
NSX collector

Maintain a TODO/STATUS.md recording:

working
broken
in progress
stretch

Run tests and launch the application after every significant merge.
```

---

# 38. UI Polish Prompt

Give this to a frontend agent around T+120 after functionality exists.

```text
Perform a visual polish pass on VCF Doctor.

Do not change API contracts or core functionality.

Make the interface look like a polished infrastructure operations product suitable for a conference demo.

Focus on:

visual hierarchy
spacing
typography
consistent card design
severity presentation
loading states
empty states
error states
subtle transitions
responsive layout
dashboard information density
finding detail presentation
snapshot comparison clarity

The user should understand within five seconds:

1. whether the environment is healthy;
2. what the most important problem is;
3. what changed recently.

Avoid gratuitous gradients, excessive glassmorphism, neon cyberpunk styling, and generic AI-chat aesthetics.

The product should feel like an operations console, not a hackathon template.
```

---

# 39. Demo Reliability Prompt

At T+150:

```text
Act as a release engineer for VCF Doctor.

Do not add features.

Make the hackathon demo extremely reliable.

Test:

fresh install
application startup
demo mode
vCenter connection failure
backend unavailable
LLM unavailable
snapshot creation
snapshot comparison
finding display
assistant mock fallback

Ensure the entire application can be demonstrated with:

VCF_DOCTOR_DEMO_MODE=true

Document the exact demo startup commands in docs/DEMO.md.

Fix obvious runtime errors and broken states.

Do not refactor functioning code merely for cleanliness.
```

---

# 40. Final Demo Script

```text
Everyone who operates infrastructure has heard:

"It worked yesterday."

VCF Doctor answers three questions:

What's wrong?
What changed?
What should I do about it?

This is my environment.

[Overview]

Everything here is generated deterministically from the VMware APIs.

I'll capture a baseline.

[Capture Snapshot]

Now something changes.

[Trigger or load changed state]

VCF Doctor detects the problem.

[Health]

ESX03 is disconnected.

It also knows this host was healthy in our previous snapshot.

[Changes]

Instead of showing me a JSON diff, VCF Doctor tells me the operationally significant changes.

And if I want help interpreting that evidence...

[Explain]

The LLM isn't deciding whether my environment is healthy.

It is reasoning over facts VCF Doctor already established.

And it can turn that evidence into something actionable.

[Generate PowerCLI Investigation]

It authors the investigation, but it does not execute anything against the environment.

Today I connected directly to vCenter.

The architecture also allows SDDC Manager or VCF Operations to become discovery roots for the rest of a VCF environment.

VCF Doctor:

What's wrong, what changed, and what should I do about it?
```

---

# 41. Anti-Goals

Do not spend the hackathon building:

* authentication infrastructure;
* RBAC;
* durable encrypted secret management;
* production TLS architecture;
* comprehensive NSX support;
* every VCF API;
* auto-remediation;
* vector databases;
* generic RAG;
* autonomous agents;
* Kubernetes deployment;
* production observability;
* enterprise multi-tenancy.

Those are product-roadmap concerns.

---

# 42. Post-Hackathon Roadmap

## Phase 1 — MVP

vCenter collector
health checks
snapshots
semantic diff
optional AI

## Phase 2 — VCF Awareness

SDDC Manager discovery
Ops discovery
NSX collector
Automation collector
VCF lifecycle data

## Phase 3 — Investigation Engine

finding-specific probes
change correlation
dependency relationships
historical timelines

## Phase 4 — Deep Troubleshooting

VCF Operations metrics
logs
events
alarms
NSX operational state
vSAN health

## Phase 5 — Remediation Studio

generated PowerCLI
generated API workflows
Terraform suggestions
Ansible suggestions
change review
approval workflow

## Phase 6 — Continuous Doctor

scheduled snapshots
drift detection
alerts
baseline policies
Git-backed desired state

## Phase 7 — MCP

Expose VCF Doctor itself as an MCP server.

Instead of giving an AI direct arbitrary access to VCF APIs, expose higher-level safe tools:

```text
get_environment_health
get_findings
get_recent_changes
investigate_finding
compare_snapshots
get_resource
generate_remediation
```

VCF Doctor then becomes a deterministic safety/evidence layer between AI agents and VCF.

That is likely the strongest long-term architecture.
