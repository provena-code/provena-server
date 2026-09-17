# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ProvenaServer is a FastAPI app that records and serves programming-activity log data collected by a VS Code extension, for verifying student work and collecting research data. It's built on two git submodules:

* `provena/` — [provena-core](https://github.com/thomaswp/provena-core), TypeScript logic for building provenance history from logs (annotating each character with its original source). Not currently called by the server directly — clients build their own provenance histories for now — but `src/provena/bridge/node_bridge.py` can shell out to its compiled `provena/dist/App.js` via Node subprocesses.
* `toolbox/` — [ProgSnapToolkit](https://github.com/CSSPLICE/ProgSnapToolbox), which provides the ProgSnap2 data model, spec-driven table generation, and the SQL read/write layer. It's a separately installable Python package (`progsnap2`) that this server depends on.

It also connects to two sibling client repos (not in this repo): `provena-vscode` (writes student history to the server) and `provena-client` (instructor-facing web UI that reads from the server).

## Setup

Both submodules must be checked out (`git submodule update --init --recursive`). The `toolbox` package must be installed (editable) separately, e.g. `pip install -e ./toolbox[api,dev]`, since `requirements.in` at the repo root is currently just a TODO note, not an installed requirements file.

Before running the server, create these from their `.example.yaml` counterparts in `src/provena/`:
* `read_config.yaml` — DB config for read/analytics endpoints
* `write_config.yaml` — DB config for logging endpoints
* `auth_config.yaml` — OAuth backend + token settings for authentication (see Architecture below)

`read_config.yaml`/`write_config.yaml` must point at the same database; they're separate because the toolkit separates logging (write) from reading/analytics (read). `auth_config.yaml` reuses that same database's `sqlalchemy_url` for its own hand-written tables, but is otherwise independent config.

## Common commands

Run the API locally (from repo root, Windows):
```
run_api.bat
```
This sets `PYTHONPATH=./src` and runs `uvicorn --app-dir ./src provena.main:app --reload --reload-dir ./src/ --port 8001`.

Run in production (Linux, via `run_api_prod.sh` / the `provena.service` systemd unit): activates `./venv`, sets `PYTHONPATH`, and runs uvicorn on port 5000 with `--root-path /provena`.

Run the toolbox test suite (from `toolbox/`, which has its own pytest config):
```
pytest
```
`toolbox/pyproject.toml` sets `pythonpath = ["src"]` for pytest. Run a single test with the usual `pytest path/to/test_file.py::test_name`. There is no test suite yet for the `provena` server package itself (`src/provena`).

Load-test with Locust: `locustfile.py` at the repo root exercises `/events`, `/submit`, `/get_event_count`, and `/read/sessions/{id}/last_synced_order` against a running server (defaults to `http://127.0.0.1:8001/`). See `locust.md` for the scenario spec it was generated from.

## Architecture

* `src/provena/configs.py` loads the ProgSnap2 spec (`src/provena/progsnap2-provena.yaml`, a project-specific variant of the standard spec) and builds the shared `api_config` (write) and `read_config` (read) objects, plus the generated `MainTableEvent` pydantic model, from the two YAML config files. Nearly every other module imports from here rather than re-deriving config.
* `src/provena/main.py` is the FastAPI entrypoint. It auto-discovers routers by walking `provena.api` with `pkgutil.walk_packages` and including any submodule that exposes a `router` — new endpoint modules just need to define `router = APIRouter()` and they'll be picked up automatically, no manual registration. It also installs global exception handlers that, on validation/parse/unhandled errors, try to log the failed request as a `LoggingError` event in the DB (via `add_error_event`/`add_malformatted_events`) rather than just returning a bare 500.
* `src/provena/api/logging/logging.py` — write-side endpoints (`/events`, `/submit`, `/get_event_count`) called by the VS Code extension. Uses a `SQLWriter` (from `progsnap2`) obtained via FastAPI `Depends`. Code states are content-addressed: `generate_code_hash` (canonicalized, MD5) produces `CodeStateID`s so identical code across events/submissions dedupes. `/submit` fans a single submission out into one event per (subject × code-state-section).
* `src/provena/api/read/` — instructor-facing read endpoints called by the web client (`assignments.py`, `edits.py`, `sessions.py`, `subjects.py`, `submissions.py`), each a self-contained `APIRouter` module. `common.py` sets up the shared read-side `SQLIOFactory`/reader and an `X-API-Key` header check (`require_api_key`) — currently a placeholder until real auth/roles exist.
* Both the write and read sides go through `progsnap2`'s `IOFactory`/`SQLIOFactory` (from the `toolbox` submodule) to get a `writer`/`reader` session scoped to the request; endpoint code should generally not talk to SQLAlchemy directly except through the table/column accessors those provide.
* `openapi.json` is a checked-in snapshot of the generated OpenAPI schema, used as a reference by things like `locust.md`/`locustfile.py` rather than regenerated on the fly.
* `src/provena/auth/` — authentication. Hand-written SQLAlchemy 2.0-style declarative models (`models.py`: `User`, `OAuthIdentity`, `Token`) living in the same database as the logging tables but managed independently (`Base.metadata.create_all`, not the ProgSnap2 spec generator) — see the "known architecture wart" note below. `config.py` loads `auth_config.yaml` (wired into `provena.configs.auth_config`). `backends/` holds pluggable login methods behind a small `AuthBackend` ABC (`base.py`); only `google.py` (Authlib-based) exists so far, resolved via `backends/registry.py`'s `active_backend` setting — exactly one backend is active per deployment, so callers never branch on which one. `tokens.py` issues/validates/revokes opaque, DB-backed tokens (only a SHA-256 hash is stored) with a per-`client_type` expiry policy: `cli` tokens (VS Code) slide forward on each use, `web` tokens have a short fixed TTL. `redirects.py` allowlist-checks the client-supplied redirect target to avoid an open-redirect/token-leak. The actual endpoints (`/auth/login`, `/auth/google/callback`, `/auth/logout`) live in `src/provena/api/auth/auth.py` so they're picked up by `main.py`'s router auto-discovery. See `docs/plans/auth.md` for the full design rationale.
* All datetimes in `provena.auth` are naive UTC, not timezone-aware — `DateTime(timezone=True)` is a no-op on both SQLite and MySQL (neither preserves tzinfo through a round trip), so mixing aware/naive comparisons after a DB read is an easy bug. Follow the existing `_utcnow()` helpers rather than calling `datetime.now(timezone.utc)` directly in this code.

## Known architecture wart: everything lives in the logging DB

The only *generated* database schema is the ProgSnap2 logging DB, generated from `src/provena/progsnap2-provena.yaml` via the `toolbox` spec machinery (`PS2DataConfig`/`SQLWriter`/`SQLIOFactory`). That schema is designed for append-only event logging, not general relational app data, which made non-logging (instructor-facing) features awkward to bolt on. `provena.auth`'s hand-written SQLAlchemy models (see above) are the first exception to this and the intended pattern going forward for other non-logging concerns (e.g. future authorization/roles) — normal declarative ORM tables living in the same DB, not shoehorned into the ProgSnap2 spec. See `docs/plans/` for design docs on this kind of work.

## Docs / plans

Design docs for in-progress or planned features live in `docs/plans/`. Check there (and update the relevant doc) when doing significant design work rather than only relying on chat history.
