# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- The build and tag-driven release contract lives in `.github/workflows/ci.yml`.
  Operator procedure and recovery guidance live in `CONTRIBUTING.md` and
  `docs/DEPLOYMENT.md`.
- Use the Makefile gates locally: `make lint`, `make test`,
  `make build-frontend`, and `make scan`. Validate workflow changes with
  `actionlint`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
