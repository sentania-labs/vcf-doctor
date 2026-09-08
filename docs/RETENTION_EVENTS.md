# Retention tiers, change log, events (contract)

## Retention policy (settings KV `retention_policy`, GUI on Settings)

```json
{"recent_days": 14, "hourly_days": 30, "daily_days": 365}
```

Applied per connection after every scan and at startup (idempotent):

- age < recent_days: keep every scheduled snapshot;
- recent_days <= age < hourly_days: keep the one nearest each hour mark, prune the rest;
- hourly_days <= age < daily_days: keep the one nearest each day mark (00:00 UTC);
- age >= daily_days: prune.

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

Diff additions: `bootTime` tracked (host medium, vm low, summary
"rebooted <old> -> <new>").

## Events and tasks

Per scan, the vSphere collector also fetches vCenter events and tasks. The
window starts at the connection's last complete capture checkpoint with a
60-second overlap and ends at the current snapshot. A failed or incomplete
query does not advance the checkpoint, so the next scan retries the gap. The
recovery window is bounded by the configured event retention period.
Normalized `Event`:

```
id (str, "<connection_id>:<vc event key>"), connection_id, time (iso),
source ("event" | "task"), type (vim class name, e.g. VmPoweredOffEvent),
category ("info" | "warning" | "error" | "user"), message (fullFormattedMessage),
user (str | null), resource_id (str | null, mapped via moref when the entity
is in the snapshot), resource_name (str | null), resource_type (str | null)
```

Stored in an `events` table and deduplicated on id. Event storage is independent
from snapshot retention and is controlled by the Settings card:

```json
{"retention_hours": 48, "row_cap": 250000}
```

Both values apply per connection after the next scan. Existing databases gain
the setting and supporting tables during startup. A result that reaches the
20,000-item vCenter safety limit is split into smaller time windows. If the
minimum window still reaches the limit, its interval is persisted, shown on
the Events page, and retried on later scans.

Pruning is followed by bounded `incremental_vacuum` maintenance. Settings shows
its last run, reclaimed page count, and last error. A scan never runs a full
database vacuum.

- `GET /api/events?connection_id=&since=&until=&resource_id=&category=&q=&limit=`
  newest first, default last 24 h, limit 500.
- `GET /api/events/status?connection_id=` returns the last complete checkpoint
  and any incomplete intervals awaiting retry.
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
- Settings: "Retention" card with the three day counts; explanatory text that
  manual snapshots are never pruned.
- New Events page (nav after Changes): time range presets (1 h, 24 h, 7 d),
  category and text filter, connection scoped, virtualized list or paging.
- Finding drawer: "Events in this window" section (events between the
  previous and current snapshot for the finding's resource, then the rest of
  the connection), passed into the assistant context.
- Changes page: default view is now the persisted log for the selected
  connection (time range presets), with the FROM/TO snapshot compare kept
  as a second tab.
