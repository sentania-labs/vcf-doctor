# Demo runbook

## Primary path: live workload domain

The container is deployed by Argo from the lab deployment repo. Nothing
about a vCenter is configured ahead of time.

1. Open the UI. Connections page. Add vCenter: name, host, username,
   password, TLS verify off for the lab. Test Connection. Save.
   Default schedule: enabled, every 15 minutes. Set 5 for the demo.
2. Top bar: Scan Now. Overview populates with real inventory.
3. Snapshots: Capture Snapshot, label "Baseline".
4. Make the rehearsed lab change (power off the designated VM, or put the
   designated host in maintenance mode). Wait for it to settle in vCenter.
5. Scan Now again.
6. Changes: FROM Baseline, TO latest. Point at the semantic change and its
   significance.
7. Health: open the resulting finding. Explain. Then Generate Script
   (PowerCLI). Show the READ ONLY and MODIFIES ENVIRONMENT badges and that
   there is no execute control.
8. Undo the lab change after the demo.

## Fallback: fixture mode

If the lab is unreachable from the venue:

```bash
VCF_DOCTOR_DEMO_MODE=true make run
# or
VCF_DOCTOR_DEMO_MODE=true docker compose up
```

Startup creates "Demo Workload Domain" and captures snapshot A (healthy).
Every later Scan Now captures snapshot B (degraded): esx03 disconnected,
an NSX segment removed, a vSAN datastore at 91%, esx07 in maintenance,
web03 powered off, app02 migrated. The assistant answers from the mock
provider unless an Anthropic key is configured.

## Assistant

Set `ANTHROPIC_API_KEY` in the deployment, or enter the key on the Settings
page. Settings also switches the provider to Mock for an offline rehearsal.
Model defaults to claude-opus-5.

## Before going on stage

- `make lint && make test` green on the commit being demoed.
- Rehearse the live path once end to end, including the undo.
- Rehearse the fixture path once.
- Confirm the Anthropic key works with Settings > Test.
