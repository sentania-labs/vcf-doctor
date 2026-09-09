# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

## The database

PostgreSQL is the only supported database. There is no SQLite mode and no
file-backed fallback; `app/db.py` is the only place that opens a connection.

- The schema is numbered `.sql` files in `backend/app/migrations`, applied in
  order by `backend/app/migrate.py` and recorded in `schema_migrations`. Adding
  one is dropping in the next-numbered file; never edit a shipped one.
- Placeholders are `%s`, not `?`. Rows come back as dicts.
- `time` and `user` are PostgreSQL keywords and are quoted everywhere the
  `events` table is touched.
- Timestamps are ISO 8601 UTC text, not `timestamptz`, so lexical ordering is
  chronological. That is deliberate; see the `app/db.py` module docstring.
- More than one worker and more than one pod are supported. Anything that must
  have a single writer takes a PostgreSQL advisory lock (`db.try_advisory_lock`,
  `db.lock_in_transaction`, `db.acquire_scheduler_lock`), never a process lock.
- The database password is never an environment variable. It is read from
  `VCF_DOCTOR_DB_PASSWORD_FILE`; a `DATABASE_URL` carrying one is refused at
  startup.

## Running the tests

`make test` starts a disposable `postgres:16` container, runs pytest against it
and removes it. The suite drops and rebuilds the schema between tests, so it
refuses to run unless `VCF_DOCTOR_TEST_DATABASE_URL` names the database it may
do that to. Never point it at anything you care about.

`make run` and `make dev-backend` use a separate `vcf-doctor-dev-pg` container
that keeps its data. `docker compose up` runs the whole stack the way a user
does.

## The rest

- Every setting an operator would change has a control in Settings with a
  working default (`CONTRIBUTING.md`, house style). Deployment bindings, the
  database connection included, are the exception: the interface shows whether
  the database is reachable and nothing more.
- No em-dashes anywhere, including code comments and commit messages.
- `make lint`, `make test` and `make scan` are exactly what CI runs.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
