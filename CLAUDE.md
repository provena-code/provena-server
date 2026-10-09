# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ProvenaServer is a FastAPI app that records and serves programming-activity log data collected by a VS Code extension, for verifying student work and collecting research data. It's built on two git submodules:

* `provena/` — [provena-core](https://github.com/provena-code/provena-core), TypeScript logic for building provenance history from logs (annotating each character with its original source). Not currently called by the server directly — clients build their own provenance histories for now — but `src/provena/bridge/node_bridge.py` can shell out to its compiled `provena/dist/App.js` via Node subprocesses.
* `toolbox/` — [ProgSnapToolkit](https://github.com/CSSPLICE/ProgSnapToolbox), which provides the ProgSnap2 data model, spec-driven table generation, and the SQL read/write layer. It's a separately installable Python package (`progsnap2`) that this server depends on.

It also connects to two sibling client repos (not in this repo): `provena-vscode` (writes student history to the server) and `provena-client` (instructor-facing web UI that reads from the server).

## Setup

Both submodules must be checked out (`git submodule update --init --recursive`). The `toolbox` package must be installed (editable) separately, e.g. `pip install -e ./toolbox[api,dev]`, since `requirements.in` at the repo root is currently just a TODO note, not an installed requirements file.

The dev environment is the repo-local conda env at `./.conda` (Python 3.11, with `progsnap2` installed editable plus `pytest`, `httpx`, `mysqlclient`, `Authlib`). The system `python` on PATH is a different interpreter without these packages, so use `./.conda/python.exe`.

Before running the server, create these from their `.example.yaml` counterparts in `src/provena/config/` (the real files are gitignored):
* `read_config.yaml` — DB config for read/analytics endpoints
* `write_config.yaml` — DB config for logging endpoints
* `auth_config.yaml` — OAuth backend + token settings for authentication (see Architecture below)

**Provena officially supports only MySQL.** The toolbox can log to other backends (SQLite, CSV, git), but provena's own queries use MySQL-specific SQL (`ON DUPLICATE KEY UPDATE`, `CONCAT`) and rely on MySQL semantics. In particular, the default `utf8mb4_0900_ai_ci` collation compares strings case- and accent-insensitively, and strict mode rejects over-length strings. Don't add SQLite-only workarounds, and don't assume SQLite behavior when reasoning about queries.

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
`toolbox/pyproject.toml` sets `pythonpath = ["src"]` for pytest. Run a single test with the usual `pytest path/to/test_file.py::test_name`. Run the toolbox suite only from inside `toolbox/`: its conftest `rmtree`s a *relative* `./test_data/` at session start, so running it from the repo root would delete the dev DBs in the root `test_data/`.

Run the server's own test suite (`tests/`, configured in the root `pyproject.toml`) from the repo root:
```
./.conda/python.exe -m pytest            # add --cov for branch coverage of src/provena
```
It needs a MySQL server: copy `tests/test_config.example.yaml` to `tests/test_config.yaml` (gitignored) and fill in the URL, or set `PROVENA_TEST_MYSQL_URL`. Each run creates its own `provena_test_<random>` database, points the app at it via `PROVENA_CONFIG_DIR`, and drops it afterwards, so it never touches the dev/course DB in `src/provena/config/`. `tests/conftest.py` does this in `pytest_configure` and imports `provena.main` there, because of the import-time side effects below; don't import provena at the top of `conftest.py`. Rows are deleted after every test (the app commits internally, so per-test rollback isn't possible). Helpers live in `tests/support/`: `provena_helpers.py` (credential headers backed by real tokens/API keys, `event(...)`, `seed_events(...)`) and the provena-independent `databases.py`/`progsnap2_events.py`. The plan, the testability constraints, and the log of behavior decisions are in `docs/tasks/testing.md`.

Load-test with Locust: `locustfile.py` at the repo root exercises `/events`, `/submit`, `/get_event_count`, and `/read/sessions/{id}/last_synced_order` against a running server (defaults to `http://127.0.0.1:8001/`). See `locust.md` for the scenario spec it was generated from.

## Architecture

* `src/provena/config/configs.py` loads the ProgSnap2 spec (`src/provena/progsnap2-provena.yaml`, a project-specific variant of the standard spec) and builds the shared `api_config` (write) and `read_config` (read) objects, plus the generated `MainTableEvent` pydantic model, from the two YAML config files. Nearly every other module imports from here rather than re-deriving config.
* `src/provena/main.py` is the FastAPI entrypoint. It auto-discovers routers by walking `provena.api` with `pkgutil.walk_packages` and including any submodule that exposes a `router` — new endpoint modules just need to define `router = APIRouter()` and they'll be picked up automatically, no manual registration. It also installs global exception handlers that, on validation/parse/unhandled errors, try to log the failed request as a `LoggingError` event in the DB (via `add_error_event`/`add_malformatted_events`) rather than just returning a bare 500.
* `src/provena/api/logging/logging.py` — write-side endpoints (`/events`, `/submit`, `/get_event_count`) called by the VS Code extension. Uses a `SQLWriter` (from `progsnap2`) obtained via FastAPI `Depends`. Code states are content-addressed: `generate_code_hash` (canonicalized, MD5) produces `CodeStateID`s so identical code across events/submissions dedupes. `/submit` fans a single submission out into one event per (subject × code-state-section). `/events`/`/get_event_count` are gated by `require_student_role`; `/submit` by the stricter `require_submit_permission`, since it's invoked by the autograder rather than the student's own session (see `provena.auth.roles` below) — a plain student login is deliberately not sufficient there.
* `src/provena/api/read/` — instructor-facing read endpoints called by the web client (`assignments.py`, `edits.py`, `sessions.py`, `subjects.py`, `submissions.py`), each a self-contained `APIRouter` module. `common.py` sets up the shared read-side `SQLIOFactory`/reader and gates every router on `require_instructor_role` (see `provena.auth.roles` below).
* Both the write and read sides go through `progsnap2`'s `IOFactory`/`SQLIOFactory` (from the `toolbox` submodule) to get a `writer`/`reader` session scoped to the request; endpoint code should generally not talk to SQLAlchemy directly except through the table/column accessors those provide.
* `openapi.json` at the repo root is a local snapshot of the generated OpenAPI schema, used as a reference by things like `locust.md`/`locustfile.py`. It's gitignored, not committed, so it may be stale or missing.
* Lots of work happens at import time: `configs.py` loads all three YAML configs from fixed paths, `api/logging/logging.py` creates the ProgSnap2 tables, `main.py` calls `provena.db.base.init_app_tables()` (creates/migrates the hand-written app tables), and `api/read/common.py` reflects (and caches) the DB schema. The read side relies on `api.logging` being imported before `api.read` (alphabetical order in `main.py`'s `walk_packages`). Keep this in mind when adding modules or tests.
* `config/configs.py` reads the three YAML configs from the directory in the `PROVENA_CONFIG_DIR` env var, defaulting to `src/provena/config/`. The test suite uses this to point the app at a throwaway `provena_test_*` database.
* `src/provena/auth/` — authentication. Hand-written SQLAlchemy 2.0-style declarative models (`models.py`: `User`, `OAuthIdentity`, `Token`) living in the same database as the logging tables but managed independently on the shared `provena.db.base.Base` (created by `init_app_tables`, not the ProgSnap2 spec generator) — see the "known architecture wart" note below. `config.py` loads `auth_config.yaml` (wired into `provena.config.configs.auth_config`). `backends/` holds pluggable login methods behind a small `AuthBackend` ABC (`base.py`); only `google.py` (Authlib-based) exists so far, resolved via `backends/registry.py`'s `active_backend` setting — exactly one backend is active per deployment, so callers never branch on which one. `tokens.py` issues/validates/revokes opaque, DB-backed tokens (only a SHA-256 hash is stored) with a per-`client_type` expiry policy: `cli` tokens (VS Code) slide forward on each use, `web` tokens have a short fixed TTL. `redirects.py` allowlist-checks the client-supplied redirect target to avoid an open-redirect/token-leak. The actual endpoints (`/auth/login`, `/auth/google/callback`, `/auth/logout`) live in `src/provena/api/auth/auth.py` so they're picked up by `main.py`'s router auto-discovery. See `docs/tasks/complete/auth.md` for the full design rationale.
* All datetimes in `provena.auth` are naive UTC, not timezone-aware — `DateTime(timezone=True)` is a no-op on both SQLite and MySQL (neither preserves tzinfo through a round trip), so mixing aware/naive comparisons after a DB read is an easy bug. Follow the existing `_utcnow()` helpers rather than calling `datetime.now(timezone.utc)` directly in this code.
* `src/provena/auth/roles.py` — authorization on top of the authentication layer above. Two fixed roles (`student`, `instructor`; instructor is a superset — it also satisfies student-only checks), each independently configured in `auth_config.yaml`'s `roles` section as `whitelist` (exact emails), `pattern` (glob against email, e.g. `*@ncsu.edu`), or `open` (anyone, no credential required at all — logs a startup warning if used on `instructor`). `require_instructor_role`/`require_student_role` accept either a matching OAuth login or that role's API key; `require_submit_permission` (only on `/submit`) is stricter — a plain student login doesn't satisfy it, only an instructor credential or `roles.student.submit_api_keys` (a narrow key meant for the autograder, distinct from `roles.instructor.api_keys`, which grants full privilege and is meant to stay private). Failures are `401 {"detail": "reauth_required"}` (no credential at all, consistent with the token-expiry signal) vs. `403 {"detail": "insufficient_role"}` (a valid identity that just lacks the role) — kept distinct so a client doesn't loop into a pointless re-login on the latter.

* `src/provena/db/` — shared infrastructure for all hand-written (non-ProgSnap2) tables. `base.py` has the engine, `SessionLocal`, the declarative `Base`, and `init_app_tables()`, which runs `create_all` and then `migrations.run_migrations`. New app tables go on this `Base`, in the feature package that owns them (e.g. `auth/models.py`, `assignments/models.py`); their module must be imported in `init_app_tables` or `create_all` silently skips them. Models always describe the *current* schema (what a fresh DB gets); `migrations.py` holds idempotent inspect-then-`ALTER` upgrades for existing DBs in older shapes. Switch to Alembic once there are more than a couple.
* `src/provena/assignments/models.py` — `AssignmentMapping` (table `LinkAssignmentMap`, name kept from when it was a spec-generated link table): which files each subject submitted per assignment, maintained by `api/read/logic/mapping.py`. Its unique key is on a MySQL-generated SHA-256 of `CodeStateSection` (`CodeStateSectionHash`), since a key on the full path would exceed InnoDB's 3072-byte index limit.

## Known architecture wart: everything lives in the logging DB

The only *generated* database schema is the ProgSnap2 logging DB, generated from `src/provena/progsnap2-provena.yaml` via the `toolbox` spec machinery (`PS2DataConfig`/`SQLWriter`/`SQLIOFactory`). That schema is designed for append-only event logging, not general relational app data, which made non-logging (instructor-facing) features awkward to bolt on. `provena.auth`'s hand-written SQLAlchemy models (see above) and `AssignmentMapping` are the exceptions to this and the intended pattern going forward for other non-logging concerns (e.g. a future instructor-facing website backend). The ProgSnap2 spec (`progsnap2-provena.yaml`) is reserved for the ProgSnap2 format itself: don't add app tables or SQL-specific features (indexes, generated columns, etc.) to it or to the toolbox's spec machinery — normal declarative ORM tables living in the same DB, not shoehorned into the ProgSnap2 spec. See `docs/tasks/` for design docs on this kind of work.

## Docs / tasks

Design docs for in-progress or planned work live in `docs/tasks/`. Completed ones move to `docs/tasks/complete/` (e.g. `auth.md`). Check there, and update the relevant doc, when doing significant design work, rather than relying only on chat history.
