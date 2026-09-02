# Contributing

Thanks for helping. This page is the whole process; there is no separate
wiki.

## Run it locally

Prerequisites: Python 3.14+, [uv](https://docs.astral.sh/uv/), Node 22+.

```bash
make setup          # backend venv via uv, frontend npm ci
make lint           # ruff (backend) and tsc (frontend)
make test           # backend pytest
make run            # builds the frontend, serves everything on :8000
```

For hot reload use two terminals: `make dev-backend` (:8000) and
`make dev-frontend` (:5173, proxies `/api`). `make scan` runs the same
dependency, secret and repository scans CI runs; see
[docs/SECURITY.md](docs/SECURITY.md) for what it needs on your machine.

To work without a vCenter, start the backend with
`VCF_DOCTOR_TEST_FIXTURES=1` and create a connection with `"kind": "fixture"`
through the API. That hook is for tests and local development only.

## The bar for a pull request

1. **Tests for what you changed.** Backend changes come with pytest
   coverage; frontend changes must pass `npx tsc -b --noEmit` and be seen
   working in a browser. Say what you observed in the PR body.
2. **Reviewed before it opens.** Someone other than the author (a peer, a
   reviewer agent, or a genuinely separate self-review pass) reads the diff
   and tries to break it before the PR exists. The author's own "looks
   good" does not count.
3. **One round of Codex review.** An external Codex review runs
   automatically when the PR opens. Address that one round (fix what is
   valid, reply to what is not), then stop. Do not loop.
4. **CI green.** Lint, tests, dependency and secret scan, repository scan,
   image build and scan, and the container smoke test all gate the merge.
   Local `make lint`, `make test` and `make scan` predict them exactly
   because CI calls the same targets.
5. **Merge to main publishes.** A green main push builds, signs and pushes
   the image and cuts the next `v0.1.N` release. Treat main as shippable.

Write the PR body in operational terms: what changes for someone running
it, what the blast radius is, how to recover if it is wrong.

## House style

- **Every setting has a GUI control and a working default.** If it is
  configurable, it is configurable from Settings, and a fresh install runs
  with nothing pre-configured. Environment variables may override.
- **Deterministic first.** Health checks and diffs never depend on the
  assistant. The assistant explains; it does not decide.
- **No em-dashes.** Anywhere: code, comments, docs, commit messages. Use
  commas, colons, parentheses or a period.
- **Never commit a secret.** Not in code, fixtures, PR bodies, issues or
  screenshots. The secret scan checks history, so a leaked secret must be
  rotated, not just removed.
- **Scope discipline.** Fix the thing the PR is for. Anything else you
  notice becomes a GitHub issue, not extra commits on the same PR.
- **Read-only against vCenter.** Nothing in this codebase may change
  anything in a vCenter. Generated scripts are shown, never executed.

## Where things live

Backend: `backend/app` (FastAPI, SQLite, APScheduler; checks in
`diagnostics/checks`, diff engine in `diff`, collectors in `collectors`).
Frontend: `frontend/src` (React, Vite, Tailwind; pages in `pages`, Settings
cards in `components/settings`). Contracts: [docs/PROPERTIES.md](docs/PROPERTIES.md)
and [docs/RETENTION_EVENTS.md](docs/RETENTION_EVENTS.md).
