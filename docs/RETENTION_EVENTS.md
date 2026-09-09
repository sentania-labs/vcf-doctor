# Retention tiers, change log, events (contract)

## Retention policy (settings KV `retention_policy`, GUI on Settings)

```json
{"recent_days": 14, "hourly_days": 30, "daily_days": 365, "timezone": ""}
```

Applied per connection after every scan and at startup (idempotent):

- age < recent_days: keep every scheduled snapshot;
- recent_days <= age < hourly_days: keep the one nearest each hour mark, prune the rest;
- hourly_days <= age < daily_days: keep the one nearest each day mark, which is
  midnight in the policy's `timezone`;
- age >= daily_days: prune.

`timezone` is an IANA zone name, editable in Settings > Retention. Empty (the
default) follows the server's own zone, reported as `server_timezone` by
`GET /api/settings`, and `VCF_DOCTOR_RETENTION_TIMEZONE` sets the default for a
fresh install. Day marks are local midnights so the daily survivor lands under
the day the Snapshots page groups it by; hour marks stay on the UTC hour, which
is the same instant in every whole-hour zone. A zone this machine does not know
falls back to UTC with a warning rather than failing the retention pass.

Manual snapshots (`scheduled = 0`) are never pruned; scheduled snapshots
follow the tiers whether or not they carry a label. `SnapshotSummary.tier` is
`recent | hourly | daily | manual`. The old `retention` count setting is
removed from the API and the GUI and is no longer read by the code.

Snapshot resource blobs are stored gzip-compressed (`resources_gz` BLOB);
existing rows are migrated at startup in place, in batches, without blocking
startup for more than a few seconds per thousand rows.

## Change log (persisted)

Every scan computes `diff(previous, current)` and writes the rows to a
`changes` table: id, connection_id, from_snapshot_id, to_snapshot_id,
observed_at (to snapshot time), resource_id, resource_type, resource_name,
change_type, significance, summary, property_changes (JSON). Retention of
change rows: daily_days. Endpoints:

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
it is not configurable or part of `GET /api/settings`. A database upgraded to the change-log
release mid-life has snapshots older than that stamp, and the log cannot
describe that era. Both readers say so rather than showing an empty window:

- `GET /api/findings/{id}/related` sets `window.log_starts_at` when the finding
  was first observed before the log begins. It then diffs the two snapshots
  around first observation (`window.basis = "pre_log_bracketing_pair"`), or
  the newest differing pair when retention has pruned one of those two
  (`"pre_log_differing_pair"`), and lists the logged rows about the finding's
  neighbourhood after that diff as a separate block, then caps the combined list.
- the Overview feed recovers the part of its 24 h window that predates the log
  by diffing the snapshots that do cover it, newest first and bounded.

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
A result that reaches the 20,000-item vCenter safety limit is split into
smaller time windows. If the
minimum window still reaches the limit, its interval is persisted, shown on
the Events page, and retried on later scans. Overlapping gaps are coalesced;
gaps covered by the checkpoint window are not queried separately. Recorded
gaps expire when their end precedes the retention cutoff. Before retry selection,
surviving gaps are trimmed to that cutoff so a prolonged outage does not trigger
queries for expired history.

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
