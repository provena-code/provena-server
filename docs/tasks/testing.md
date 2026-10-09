# Testing plan for the `provena` server package

Status: **planning done; T0 in progress**. No tests exist yet for
`src/provena`. The `toolbox` submodule (ProgSnapToolkit) has its own suite
(`toolbox/tests`), which is out of scope except where section 4 suggests
moving shared infrastructure there.

This doc is the durable record for this work: the infrastructure decisions,
the sub-task breakdown, and the **decisions log** (section 8), where answers
to "what *should* this do?" questions get recorded so they outlive chat
history.

---

## 1. Ground rules (decided)

* **MySQL only.** Provena officially supports only MySQL. The toolbox can
  log to SQLite, CSV, or git, but provena's own SQL is MySQL-specific
  (`ON DUPLICATE KEY UPDATE`, `CONCAT`), and MySQL's collation and strictness
  change query results. Every test that touches a DB runs against a real
  MySQL database. There is no SQLite lane. (D1)
* **Real DB, not mocks**, for anything that touches SQL.
* **Test through HTTP** (`TestClient`) where the behavior is HTTP-visible,
  and assert on both the response and the resulting DB rows. Call internal
  functions directly only for pure logic or to set up preconditions.
* **Bugs found by tests are fixed in separate PRs.** The test lands as
  `xfail(strict=True)` asserting the *intended* behavior, so it flips loudly
  once fixed. (D3)
* **Coverage is a low bar, not a target.** Branch coverage is reported via
  `pytest-cov` and the uncovered lines are used as a to-do list. There's no
  CI gate for now. Coverage only shows a line *ran*, not that a test would
  notice it breaking. The better strength check is the one we're already
  doing: do the tests catch the real bugs listed in section 6? Mutation
  testing (`mutmut`) is the systematic version of that and is an optional
  later step (T8). (D4)
* **The Node bridge is out of scope.** It isn't currently called. (D6)

---

## 2. Testability notes: what makes this codebase tricky to test

1. **Config is loaded at import time.** `provena/config/configs.py` reads the three
   YAML configs the moment anything imports it, and almost everything
   imports it. **Fix (T0, done):** the env var `PROVENA_CONFIG_DIR` overrides
   the directory those files are read from. The test suite writes its own
   configs to a temp dir and sets the variable before the first
   `import provena`, which in practice means in `conftest.py` at module
   level / `pytest_configure`, not in a normal fixture. (D2)
2. **DB work also happens at import time.**
   * `api/logging/logging.py` creates the ProgSnap2 tables
     (`initialize_database`).
   * `api/auth/auth.py` runs `Base.metadata.create_all`.
   * `auth/db.py` creates an engine.
   * `api/read/common.py` builds the read factory. Because `read_config` has
     no `metadata`, this opens a reader immediately, which **reflects the
     schema once and caches it forever** (`SQLReaderTableManager`).

   The read side only works because `main.py`'s `walk_packages` imports
   `api.logging` (which creates the tables) before `api.read`, purely by
   alphabetical order. With the env var pointing at a fresh test DB, all of
   this lands in the test DB, which is fine. Moving it into a `lifespan`
   hook would be cleaner, but the read-side reflection would have to become
   lazy at the same time. That's best done *after* the tests exist to catch
   regressions (T7). (D2)
3. **Not everything goes through dependency injection.** The error-logging
   paths (`add_malformatted_events` / `add_error_event`, called from
   `main.py`'s exception handlers) use the module-global
   `db_writer_factory`. This is why tests swap the *config*, not individual
   `dependency_overrides`.
4. **Sessions commit internally.** `SQLWriter.add_events`, `issue_token`,
   `resolve_token`, and `update_mapping_table` all call `session.commit()`
   themselves. That rules out wrapping each test in a transaction and rolling
   it back. **Isolation strategy:** create one throwaway database per test
   session, and run `DELETE FROM` on every table after each test.
   * `DELETE` beats `TRUNCATE` here, because TRUNCATE is DDL in MySQL and
     slower on tiny tables.
   * The `auth_*` tables have foreign keys, so delete children first or
     wrap the deletes in `SET FOREIGN_KEY_CHECKS=0`.
   * The ProgSnap2 `Metadata` table is re-seeded or left alone.
5. **Auth config is a shared mutable object.** `roles.py` and `tokens.py`
   share the `auth_config` instance from `provena.config.configs`. To vary the role
   setup, use `monkeypatch.setattr` on that object's attributes, which is
   undone automatically after each test.
6. **The local MySQL server's semantics** (checked: MySQL 8.4.3):
   * Collation `utf8mb4_0900_ai_ci` is **case- *and* accent-insensitive**.
     `SubjectID = 'abc'` matches `'ABC'`, and `'café'` matches `'cafe'`.
     This affects every ID filter, the `CodeStateSection` suffix matching,
     and the `LinkAssignmentMap` unique constraint. `0900` collations are
     NO PAD, so trailing spaces *are* significant.
   * `STRICT_TRANS_TABLES` is on, so over-length strings **error** instead
     of silently truncating. This matters for `truncate_entry` / link-table
     writes and long `CodeStateSection` paths (`path_str_length: 512`).
   * Timestamps are stored and compared as **strings**
     (`ClientTimestamp >= start`), so ordering depends on clients sending a
     consistent ISO format.
   * `ONLY_FULL_GROUP_BY` is on. Aggregate queries that work elsewhere can
     fail here.
7. **Time.** Token expiry goes through `provena.auth.tokens._utcnow()` (naive
   UTC). Monkeypatch it to control "now"; no extra dependency needed.
8. **External services.** Google OAuth sits behind the `AuthBackend` ABC
   plus the `_backends` cache in `auth/backends/registry.py`. A `FakeBackend`
   placed in that cache drives the whole login flow with no network. The
   Google-specific checks (`email_verified`, missing `sub`/`email`) are
   unit-tested by stubbing Authlib's `authorize_access_token`.

### Environment facts

* The repo-local conda env `./.conda` (Python 3.11) has `pytest`, `httpx`,
  `mysqlclient`, `Authlib`, and editable `progsnap2`. The system `python` on
  PATH is a different interpreter.
* The MySQL user `provena` has `ALL PRIVILEGES ON provena\_%.*`, so it can
  create and drop `provena_test_*` databases itself. No admin step is
  needed.
* Dev configs point at a **real course database**. Tests must never use
  them. The test fixture refuses to run against any database whose name
  doesn't start with `provena_test_`.
* The toolbox's own tests `rmtree` a *relative* `./test_data/`. Run them
  only from inside `toolbox/`.

---

## 3. Test DB configuration (decided: local MySQL, container-ready)

The suite reads one small, gitignored file, **`tests/test_config.yaml`**,
copied from a committed `tests/test_config.example.yaml`:

```yaml
# Server URL *without* a database name. The suite creates its own
# provena_test_<random> database here and drops it afterwards.
mysql_server_url: mysql://provena:CHANGE-ME@localhost
database_prefix: provena_test_   # must start with "provena_test_" (safety guard)
keep_database: false             # true = don't drop after the run, for debugging
```

The environment variable `PROVENA_TEST_MYSQL_URL` overrides
`mysql_server_url` (for CI). Switching to a container later just means
changing the URL, e.g. to a `mysql:8.4` service in GitHub Actions or a
local `docker run`. Nothing else in the suite needs to change, so starting
with the local instance doesn't lock anything in. (D2)

A random suffix per session means two runs at once (two terminals, or
`pytest-xdist` workers later) don't collide.

---

## 4. What could live in the toolbox instead (flagged)

Some test infrastructure is really about ProgSnap2 data, not about provena,
and would make more sense in ProgSnapToolkit long-term:

| Candidate | Why toolbox | Benefit to provena |
|---|---|---|
| **Spec-driven event factory**, e.g. `progsnap2.testing.make_event(spec, EventType.FileEdit, **overrides)`, which fills required columns from the spec | The spec already knows each event type's required columns. Hand-written factories in provena would duplicate it and drift whenever `progsnap2-provena.yaml` changes. | Valid events for any event type in one line |
| **Throwaway-DB helper**: create a uniquely named DB from a server URL, initialize the schema from a spec, clear all tables, drop | Any toolbox user writing to SQL needs this, including the toolbox's own tests (which currently use a shared relative `./test_data/` dir that gets `rmtree`d) | Removes most of provena's conftest |
| **`SQLReaderTableManager` refresh / lazy reflection** | The reflect-once cache is a toolbox behavior | Removes the import-order fragility (section 2, item 2) |

**Recommendation:** build these in provena first, under `tests/support/`,
and design them so they don't depend on provena. Once they've settled
(after T2–T3), move them to `progsnap2.testing` in one deliberate submodule
bump. Doing it the other way round means a toolbox PR plus a submodule bump
for every tweak while the API is still changing. *Your call. Logged as D7,
pending.*

---

## 5. Sub-tasks

Each sub-task is meant to be one reviewable PR. **T0 blocks the rest.**
After T0, T1–T4 can go in any order.

### T0: Test infrastructure (blocking)

Production change (done, pending review):
* `configs.py` reads its YAML files from `PROVENA_CONFIG_DIR` (default:
  `src/provena/config/`, so nothing changes for existing setups).

Test-side:
* A root `pyproject.toml` with `[tool.pytest.ini_options]`:
  `pythonpath = ["src"]`, `testpaths = ["tests"]`, and markers
  (`concurrency`, `slow`).
* `tests/test_config.example.yaml`, plus a `.gitignore` entry for
  `tests/test_config.yaml`.
* `tests/conftest.py`:
  * At startup (before importing provena): read the test config, create
    `provena_test_<random>`, write `write/read/auth_config.yaml` into a
    temp dir pointing at it, and set `PROVENA_CONFIG_DIR`. The auth config
    has known test API keys and roles (student pattern `*@student.test`,
    instructor whitelist `prof@instructor.test`). Drop the DB at session
    end. With no `tests/test_config.yaml`, exit with a clear message.
    Pre-create `linkassignmentmap` with the production DDL first (B7/D8).
  * `client` fixture: `TestClient(app)` used as a context manager.
  * Autouse `clean_db` fixture: `DELETE FROM` every table after each test,
    following the FK note in section 2.
  * Credential helpers: `student_headers(email=...)` /
    `instructor_headers()` issue real tokens through `issue_token` and
    return `{"Authorization": "Bearer ..."}`. `instructor_key_headers()` /
    `submit_key_headers()` return `{"X-API-Key": ...}`.
  * Data builders in `tests/support/` (see section 4): `make_event(...)`,
    `seed_events([...])` through a real `SQLWriter`.
* Smoke tests:
  * The app imports.
  * `/read/sessions/x/last_synced_order` returns `-1` on an empty DB.
  * The DB actually in use is the `provena_test_*` one.
* Root `requirements-dev.txt`: `pytest`, `pytest-cov`, `httpx`.
* `CLAUDE.md`: add how to run the suite.

### T1: Pure unit tests (no DB)

* `logging.py`: `get_canonical_string` (BOM, CRLF, NFC) and
  `generate_code_hash`: canonicalize on/off, strip only in the hash, and
  equivalent code from Windows and macOS clients hashing the same.
  `add_codestate_ids`: only events with `Code` get an ID.
* `auth/redirects.py`: the allowlist matrix (exact origin, default port,
  `:*`, scheme mismatch, `javascript:`/`data:`/no host, userinfo tricks
  like `http://127.0.0.1:80@evil.com`, hostname case, IPv6 loopback). Also
  `append_query_params` / `append_fragment_params` with an existing query
  or fragment.
* `auth/roles.py`: `matches_role` (whitelist case-insensitivity, glob edge
  cases like `x@sub.ncsu.edu` and `x@ncsu.edu.evil.com`, `open`) and
  `_matches_any_key` (empty or None candidate, empty list).
* `auth/config.py`: validators and the two startup warnings (`caplog`).
* `auth/backends/google.py`: `callback` with Authlib stubbed. Cover the
  success case, `email_verified: False`, missing `sub`/`email`, and the
  Authlib exception path that returns 400.

### T2: Auth and authorization

* **Tokens:** issue/resolve/revoke, only the hash is stored, unknown and
  expired tokens resolve to None, `cli` expiry slides and `web` doesn't, and
  the boundary at `expires_at == now`.
* **Authorization matrix** (parametrized). Endpoints × credentials:
  none, garbage bearer, expired token, student, non-matching email,
  instructor token, instructor key, submit key, wrong key, and both headers
  together. Each cell expects 200, 401 `reauth_required`, or 403
  `insufficient_role`. A second axis covers role configs (`student: open`,
  `instructor: open`). A route-coverage test walks `app.routes` and fails on
  any route that's neither in the matrix nor explicitly listed as public.
* **Login flow** with a `FakeBackend`:
  * `/auth/login` allowlist enforcement and session contents.
  * `/auth/google/callback`: first login creates `User` + `OAuthIdentity`;
    a repeat login reuses them; same email with a new subject links to the
    existing user; `web` delivers via fragment and `cli` via query; `state`
    is echoed; a callback without `/auth/login` returns 400.
  * Email case: Google returns `Prof@X.edu` once and `prof@x.edu` later. Is
    that one user? The collation says yes, but the unique constraint and
    the Python-side lookups may disagree.
  * `/auth/logout` revokes only the caller's own token.

### T3: Write endpoints

* `/events`: happy path, with `ServerTimestamp` added and
  `CodeStateID = hash(Code)`; an empty list; mixed column sets across
  events; enums; the `LogResult` shape.
* `/events` failure paths: a DB-level insert failure (e.g. an over-length
  value under strict mode) goes to the malformed-event fallback with
  `MISSING`; a validation failure goes to `validation_exception_handler`,
  which writes `LoggingError` + `LinkLoggingError`; non-JSON and non-list
  bodies.
* `/submit` fan-out: {1, N} subjects × {1, N} sections. Check
  `ParentEventID` links, `Score`/`ScoreDetails` on the parent only, and
  `CodeStateID` per section; `SubjectIDs: []` is rejected.
* `/get_event_count`: match by hash; suffix fallback (`a/b/x.py` vs
  `x.py`, and `xx.py` must *not* match); rename chains including cycles;
  `Submit` excluded; **repeat calls in one process** (bug B1).

### T4: Read endpoints

* `/read/assignments`, `/read/subjects`, `.../time_range`,
  `.../codestate_sections` (only sections with a `FileSave`),
  `/read/sessions/{id}/last_synced_order` (deliberately unauthenticated;
  `-1` when empty), `/read/assignments/{id}/subjects`.
* `/read/edits` and `/read/edits_in_range`: ordering by
  `(ClientTimestamp, Order)`; nulls stripped; rename history stitched
  across `FileRename`; the `last_codestate_id` cutoff, including the
  `"\u0000"` same-timestamp trick (check how MySQL compares it).
* `mapping.py` via `/read/update_mapping_table` and
  `.../code_state_sections`: latest-submission ranking (ties!), suffix
  matching, incremental updates via `LastValidTimestamp`, and upsert.

### T5: Concurrency and races

See section 7 for the reasoning. Concretely:
* A `live_server` fixture runs real uvicorn in a background thread on a
  free port. Tests hit it with `httpx.AsyncClient` + `asyncio.gather`, so
  requests really overlap in FastAPI's threadpool and the SQLAlchemy
  connection pools. `TestClient` largely serializes requests and won't
  reproduce these problems.
* Cases:
  * **Overlapping `/events` batches** for the same and for different
    sessions: no lost rows, and `last_synced_order` is the true max.
  * **Retried batches.** The main table has no uniqueness on `EventID`,
    so is a client retry (a timeout after the server already committed)
    duplicated? Pin current behavior, then decide (D-pending).
  * **First-login race:** two callbacks for the same brand-new user at
    once collide on `auth_users.email UNIQUE`. Is a 500 acceptable, or
    should the second one retry or fetch the existing user?
  * **Concurrent `/read/update_mapping_table`** and `code_state_sections`
    (each call triggers an update): no deadlock, no duplicate mapping rows.
  * **Pool exhaustion:** `pool_size: 10`, `max_overflow: 0`,
    `pool_timeout: 2` against FastAPI's 40 worker threads. What does a
    client see when the pool runs out, and does the error handler then
    deadlock trying to log the error through the same exhausted pool?
  * **Process-wide mutable state:** bug B1's shared default `set()` is
    also a thread-safety bug, and the `_backends` cache is filled lazily
    without a lock (harmless double-init, but worth a note).
* Marked `concurrency` and run by default. They're seconds, not minutes.
  Throughput and latency belong to Locust (`locustfile.py`), not pytest.

### T6: Real client payloads (later, D5)

Capture a few anonymized `/events` and `/submit` payloads from
`provena-vscode` and the autograder as JSON fixtures, then assert they're
accepted and stored as expected. This tests the actual wire format, not my
reconstruction of it. A capture mechanism is still to be discussed. One
cheap option is a temporary, opt-in request-body dump in a dev server.

### T7: Refactors enabled by tests (later)

* Move import-time DB initialization into a FastAPI `lifespan` hook, and
  make read-side schema reflection lazy (section 2, item 2).
* Fix the bugs in section 6, each in its own PR, removing its `xfail`.

### T8: Optional

* API contract snapshot: compare `app.openapi()` against a committed
  snapshot under `tests/`. The root `openapi.json` is gitignored.
* Mutation testing (`mutmut`) on `auth/` and `api/logging/` as a test-strength
  check.
* A GitHub Actions workflow with a `services: mysql:8.4` container.

---

## 6. Suspected bugs found while reading (tests should catch these)

IDs are stable so tests and PRs can reference them.

* **B1. Mutable default arg in `get_event_count_for_codestates`.** The
  default `set()` is shared across all requests for the life of the
  process. Later requests skip rename sources that earlier requests
  already "checked", so `/get_event_count` **undercounts**. The set also
  grows without bound and isn't thread-safe.
* **B2. `global_exception_handler` logs the request body as `None`.** It
  sets `request = None` and then calls `request.body()` on it.
* **B3. The `OperationalError` handler probably never fires.** It catches
  `MySQLdb.OperationalError`, but SQLAlchemy raises
  `sqlalchemy.exc.OperationalError` (not a subclass).
* **B4. The `/events` branch of `validation_exception_handler` may never
  run.** It depends on `request.scope["route"]` being set. To confirm.
* **B5. `log_submit`'s `logger.info(f"...", events)`** passes an extra
  argument with no `%s`, which produces a logging-format error.
* **B6. `mapping.py` has a stray `from select import select`** (the stdlib
  module). Harmless but confusing.
* **B7. The schema can't be created on a fresh MySQL DB.** *Confirmed*
  while checking the T0 config change. `LinkAssignmentMap`'s unique key
  `(SubjectID, AssignmentID, CodeStateSection)` is
  (255 + 255 + 512) chars × 4 bytes = 4088 bytes, over InnoDB's 3072-byte
  limit, so `CREATE TABLE` fails with error 1071.
  * `logging.py` logs and swallows the error. MySQL DDL isn't
    transactional, so the DB is left **half-created**: `codestates`, which
    sorts after the failure, is never created either.
  * Every restart retries and fails again, because
    `have_tables_been_created` is still False.
  * The existing course DB works only because its table was created by hand
    with a prefix index, `CodeStateSection(250)`.
  * The prefix index has its own edge case: two paths that share their
    first 250 chars (and are equal under the `ai_ci` collation) are treated
    as the *same* key, so the upsert in `mapping.py` would overwrite one
    file's mapping with another's.
  * A real fix belongs in the toolbox's `SQLWriterTableManager`, which
    would need to support MySQL prefix lengths on unique constraints (e.g.
    a `UniqueConstraint` → `Index(..., unique=True, mysql_length=...)`), or
    in a spec change. See D8.

Open behavior questions (not clearly bugs):
* **Q-a.** `/events` returns **200** for malformed input after logging it as
  an error. Is that intentional, so the client drops the batch instead of
  retrying?
* **Q-b.** IDs compare case- and accent-insensitively under MySQL's
  collation. Is that desired for `SubjectID`/`AssignmentID`/paths?
* **Q-c.** `SubjectID` isn't tied to the authenticated identity. This is a
  documented non-goal, so tests pin it as current behavior.

---

## 7. Why concurrency deserves its own sub-task

A FastAPI server runs plain `def` endpoints (all of ours) on a worker
threadpool, about 40 threads by default, each pulling a connection from a
SQLAlchemy pool. Many requests run at once, so three kinds of problems
appear that sequential tests can't see:

1. **Shared in-process state.** Module globals, caches, and mutable
   defaults (B1) are shared by all threads.
2. **Check-then-act races in the DB.** Code that reads then writes, like
   "user doesn't exist → insert user" or "read `last_update` → insert
   newer", breaks when two requests interleave. The cure is usually a
   unique constraint plus handling the conflict, or an atomic upsert.
   Tests show whether the conflict is handled or turns into a 500.
3. **Resource exhaustion.** More concurrent requests than pooled
   connections means timeouts. That's a correctness question too: what
   does the client see, and does the extension retry sensibly?

Races are nondeterministic, so a test can't prove their absence. The
practical approach:
* Find candidate races by reading the code (the list in T5).
* Write tests that *make the overlap likely*: many requests released at
  once, or a forced pause between the read and the write via
  `monkeypatch`.
* Assert invariants that must hold under any interleaving: no lost rows,
  no 500s, no duplicates.

For a known race, a deterministic version is usually better: two threads
coordinated with a `threading.Barrier`. Load testing (Locust) is a
separate concern: it measures speed and capacity, not correctness.

---

## 8. Process for behavior decisions

For each sub-task:
1. **Case list first.** I add a one-line-per-case list (*given → when →
   expect*) to this doc, each tagged:
   * `[clear]`: obvious from code or docs.
   * `[inferred]`: inferred intent; silence means agreement.
   * `[decide]`: ambiguous or looks like a bug. Comes with my proposed
     answer and a one-line rationale.
2. **You answer only the `[decide]` items, batched**, e.g. `D9: ok`,
   `D10: no, should 400`.
3. **I implement.** Unanswered items get a test pinning current behavior,
   tagged `# UNCONFIRMED (D#)`. Confirmed bugs get
   `xfail(strict=True, reason="B#: ...")`.
4. **Review.** In the PR, look at the `xfail`s, the `UNCONFIRMED` tags, and
   the authorization matrix first.
5. **Log it.** Every answer gets logged below.

### Decisions log

* **D1** (scope): Which DB backends are tested? **MySQL only.** It's the
  only officially supported backend for provena. 2026-10-09
* **D2** (T0): Production refactors for testability? **`PROVENA_CONFIG_DIR`
  now.** The `lifespan` move comes later, once tests exist (T7). Test DB:
  **the local MySQL instance**, via `tests/test_config.yaml`, with a
  per-session `provena_test_<random>` database. Container/CI only changes
  the URL. 2026-10-09
* **D3** (process): Bugs found by tests? **Fix in separate PRs**, with an
  `xfail(strict=True)` test in the meantime. 2026-10-09
* **D4** (process): Coverage? **Report branch coverage, no gate.** Use it
  as a low bar or to-do list. Mutation testing is optional later. 2026-10-09
* **D5** (scope): Real client payloads? **Yes, as a later sub-task (T6).**
  2026-10-09
* **D6** (scope): Node bridge? **Out of scope**, since it isn't currently
  used. 2026-10-09
* **D7** (infra): Shared test helpers in the toolbox? *Pending.* Proposal:
  build them in `tests/support/` first, then move them to
  `progsnap2.testing` once stable (section 4).
* **D8** (T0, B7): How should the test DB get a schema that matches
  production, given that the generator can't create `LinkAssignmentMap` on
  MySQL? *Pending.* Proposal: in T0, the conftest pre-creates
  `linkassignmentmap` with the production DDL (prefix index
  `CodeStateSection(250)`) before importing the app. The generator then
  skips the existing table (`create_all` is checkfirst) and creates the
  rest, so tests run against the same schema as production. Add a
  `xfail(strict=True)` test that the generator alone produces a complete
  schema on a fresh DB (B7). Fix B7 itself separately, probably in the
  toolbox.
