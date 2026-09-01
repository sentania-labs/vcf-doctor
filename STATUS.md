# STATUS

Updated by the lead agent after every integration.

## Working
- Phase 0 skeleton: contracts, FastAPI, Vite/React/Tailwind shell served by
  the backend, SQLite, Makefile, Dockerfile, compose, CI workflow.
- Backend Phase 1 integrated and seen working live in fixture mode: multi
  connection CRUD, APScheduler per connection, scan pipeline with lock and
  retention, 11 diagnostic checks, semantic diff, overview, Anthropic
  assistant with SSE streaming and mock fallback. 124 tests.
- vSphere collector unit tested with mocks; unreachable/refused hosts fail
  fast with readable messages.

- Frontend integrated and seen working against the real API in fixture
  mode: all eight pages render with zero console errors; scripted walk
  covers Scan Now, finding drawer, streamed Explain, PowerCLI script with
  READ ONLY / MODIFIES ENVIRONMENT badges, Changes, Capture Snapshot,
  Settings save, Add vCenter test (refused host).

## In progress
- Live vCenter verification (Phase 3 step 2) is owed; no vCenter run yet.
- Live Anthropic call with a real key not yet exercised (mock verified).

## Broken
- nothing known

## Stretch
- SDDC Manager discovery, NSX, topology, correlation, cross-vCenter diff.
