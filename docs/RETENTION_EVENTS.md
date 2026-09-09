# Retention tiers, change log, events (contract)

## Retention policy (settings KV `retention_policy`, GUI on Settings)

Fields and validation are defined by [RetentionPolicy](../backend/app/models/snapshot.py);
deployment defaults come from [backend configuration](../backend/app/config.py).

Applied per connection after every scan and at startup (idempotent):

- age < recent_days: keep every scheduled snapshot;
- recent_days <= age < hourly_days: keep the one nearest each hour mark, prune the rest;
- hourly_days <= age < daily_days: for each local calendar day, keep the
  snapshot from that day nearest its starting midnight in the policy's `timezone`;
- age >= daily_days: prune.

`timezone` is an IANA zone name, editable in Settings > Retention. New installs
use the `TZ` environment value when set and UTC otherwise. The policy always
stores an explicit zone. Deployment overrides are listed in
[Environment variables](DEPLOYMENT.md#environment-variables). Day marks and
the Snapshots page day groups use this same zone, which the page names beside
the groups. Two operators therefore see the same day boundaries. Daily
retention never selects a snapshot across a local day boundary. Hour marks stay
on the UTC hour. Unknown zones are rejected by the Settings API. An invalid
stored policy falls back to deployment defaults; an invalid environment
timezone falls back to UTC.

Manual snapshots (`scheduled = 0`) are never pruned; scheduled snapshots
follow the tiers whether or not they carry a label. `SnapshotSummary.tier` is
`recent | hourly | daily | manual`; `SnapshotSummary.retention_day` is the
configured-zone calendar key used by the Snapshots page. The old `retention`
count setting is removed from the API and the GUI and is no longer read by the
code.

Snapshot resource blobs are stored gzip-compressed (`resources_gz` BLOB);
existing rows are migrated at startup in place, in batches, without blocking
startup for more than a few seconds per thousand rows.

## Change log (persisted)

Every scan computes `diff(previous, current)` and writes the rows to a
`changes` table: id, connection_id, from_snapshot_id, to_snapshot_id,
observed_at (to snapshot time), resource_id, resource_type, resource_name,
change_type, significance, summary, property_changes (JSON). Retention of
change rows: daily_days. A scan persists its snapshot and coverage marker only
after diffing succeeds, so a failed diff leaves the previous covered snapshot
as the source for the next scan. Endpoints:

- `GET /api/changes/log?connection_id=&since=&until=&min_significance=&resource_id=&limit=`
  returns the persisted rows newest first (default last 24 h, limit 500).
- `GET /api/changes` (on-demand diff between two snapshots) is unchanged.
- `since` and `until` accept any ISO 8601 datetime, with or without an offset;
  a naive value is read as UTC. An unencoded `+HH:MM` offset (which arrives as
  a space) is still understood.

The database records when each connection's change log started: its first diff saved
(an empty one included, so a quiet estate does not look like a late start)
stamps the snapshot it was taken from into a `log_since:<connection_id>` settings row. A
database that already had change rows gets the marker at startup from its
oldest surviving row for that same connection, using its source snapshot when
available and its observation time otherwise. This is an internal marker, and
it is not configurable or part of `GET /api/settings`. Retention also advances a
separate internal retained-history boundary in the same transaction that expires
change rows. Readers use the later of the first coverage marker and this retained
boundary, so a longer policy selected later cannot claim rows already pruned. A
database upgraded to the change-log release mid-life has snapshots older than
that effective boundary, and the log cannot describe that era. Readers recover
available snapshot history:

- `GET /api/findings/{id}/related` sets `window.log_starts_at` when the finding's
  first-observation interval starts before its connection's retained log begins.
  It then diffs the two snapshots around first observation
  (`window.basis = "pre_log_bracketing_pair"`), or
  the newest differing pair when retention has pruned one of those two
  (`"pre_log_differing_pair"`), and lists the logged rows about the finding's
  neighbourhood after that diff as a separate block, then caps the combined list.
  A retained row for the exact snapshot pair selected for recovery is not repeated.
  Genuinely later occurrences remain visible even when their summaries match
  the snapshot diff.
- the Overview feed recovers the part of its 24 h window that predates the log
  by diffing every snapshot pair in that time window, newest first. Recovered
  changes use the newer snapshot's observation time when ranked alongside
  logged changes for the five-row feed. If startup recovery had to use the
  oldest row's observation time because its source snapshot was pruned, both
  surfaces omit only logged changes structurally represented by a recovered
  diff that reaches the same target snapshot. Other changes from that pair and
  rows from distinct pairs remain visible.

Diff additions: `bootTime` tracked (host medium, vm low, summary
"rebooted <old> -> <new>").

## Events and tasks

Per scan, the vSphere collector also fetches vCenter events and tasks. The
window starts at the connection's last complete capture checkpoint with a
60-second overlap and ends at the current snapshot. A failed or incomplete
query does not advance the checkpoint, so the next scan retries the gap. A
successful empty query advances it too. The checkpoint window is bounded by
the event retention cutoff. Without a checkpoint, capture starts at that
cutoff; older history is not recovered. A generic task-history fetch failure
keeps fetched events but leaves the checkpoint unchanged without failing the
scan. Only `NotSupported` or `NoPermission` faults allow event-only capture to
complete the window. These faults persist a per-connection
`task_history_unavailable` flag, shown as a warning on the Events page. Task
access is checked again on each scan; a successful task query clears the flag.
Normalized `Event`:

```
id (str, "<connection_id>:<vc event key>"), connection_id, time (iso),
source ("event" | "task"), type (vim class name, e.g. VmPoweredOffEvent),
category ("info" | "warning" | "error" | "user"), message (fullFormattedMessage),
user (str | null), resource_id (str | null, mapped via moref when the entity
is in the snapshot), resource_name (str | null), resource_type (str | null)
```

Stored in an `events` table and deduplicated on id. Event storage is independent
from snapshot retention and is controlled by Settings > Events retention.
`event_policy` contains `retention_hours` and `row_cap`; defaults come from
[backend configuration](../backend/app/config.py), and accepted ranges are
defined by [EventPolicy](../backend/app/models/event.py).

Both limits apply per connection at startup and after each scan, including
when event capture fails. Time-based pruning runs first, then the row cap
keeps the newest remaining rows. Saving settings takes effect at the next
retention pass. Existing databases gain the defaults and supporting tables
automatically at startup, so existing history is subject to these limits.
A result that reaches the collector's 20,000-item safety cap is split into
smaller time windows. If the minimum window still reaches the cap, its
interval is persisted, shown on the Events page, and retried on later scans.
Overlapping gaps are coalesced; gaps covered by the checkpoint window are not
queried separately. Recorded gaps expire when their end precedes the retention
cutoff. Before retry selection, surviving gaps are trimmed to that cutoff so a
prolonged outage does not trigger queries for expired history.

A vCenter newer than the installed pyVmomi can reference a managed object
type pyVmomi does not define (vCenter 9.1 returns `ContentLibrary` entities;
pyVmomi 9.1.0.0 has no such type). pyVmomi fails the whole page on one such
reference, which used to fail the capture for that connection. The collector
registers a placeholder type for the name pyVmomi reports, logs a warning
naming the read (`ReadNextEvents` or `ReadNextTasks`), the type and the
pyVmomi version, rewinds and reads the window again. Known names in
[KNOWN_MISSING_TYPES](../backend/app/collectors/vsphere/events.py) are
registered before the first fetch. Rows for such an entity keep the
lower-cased type as `resource_type` (for example `contentlibrary`) and are not
joined to a snapshot resource. An unknown event class produces the same
initial pyVmomi error but cannot use a managed object placeholder. That capture
remains pending for retry, and its warning keeps the real event class name on
the first and later scans instead of reporting pyVmomi's internal `type` key.
The unsuccessful placeholder remains in pyVmomi's process-wide registry until
restart; the collector does not modify pyVmomi's private maps to remove it.
Hitting that cap in the smallest query window, or a failed task query, is
logged as a warning as well as being recorded as an incomplete interval.

Pruning is followed by bounded `incremental_vacuum` maintenance. Settings shows
its last run, reclaimed page count, and last error. A scan never runs a full
database vacuum. For existing databases, see the
[compaction upgrade notes](../README.md#upgrade-notes-event-compaction).

- `GET /api/settings` returns `event_policy` and `event_maintenance`;
  `PUT /api/settings` accepts partial `event_policy` updates.
- `POST /api/settings/events/compaction-migration` retries migration and runs
  bounded maintenance, returning its status. Check `last_error` even when the
  request succeeds.
- `GET /api/events?connection_id=&since=&until=&resource_id=&category=&q=&limit=`
  newest first, default last 24 h, limit 500.
- `GET /api/events/status?connection_id=` returns
  [EventCaptureStatus](../backend/app/models/event.py), including the checkpoint,
  retry intervals, and task-history availability described above.
- `AssistantContext` gains `events: list[Event] = []` (additive); the prompt
  renders them as an EVENTS block ("what vCenter recorded in the window").
- Fixture collector (tests only): `fixtures/events_b.json` holds about 25 realistic events
  spanning the A -> B changes (power off web03 by an admin, vMotion of
  app02, esx03 disconnect alarm, snapshot creation, host maintenance
  entered, reconfigure of app01, VLAN change task, NTP reconfigure), loaded
  on the second fixture scan with timestamps relative to scan time.

## Frontend

- Snapshots page: grouped by tier with date headers; FROM/TO pickers grouped
  the same way with a text filter; tier badge.
- Settings: "Retention" card with the three day counts and the day-mark
  timezone; explanatory text that manual snapshots are never pruned, and a note
  when the chosen zone differs from the browser's.
- New Events page (nav after Changes): time range presets (1 h, 24 h, 7 d),
  category and text filter, connection scoped, virtualized list or paging.
- Finding drawer: the window line names where the change log begins when the
  finding is older than it, instead of showing an empty window.
- Finding drawer: "Events in this window" section (events between the
  previous and current snapshot for the finding's resource, then the rest of
  the connection), passed into the assistant context.
- Changes page: default view is now the persisted log for the selected
  connection (time range presets), with the FROM/TO snapshot compare kept
  as a second tab.
