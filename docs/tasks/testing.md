# Testing plan for the `provena` server package

Status (2026-10-09): **T0–T5 are done and merged to `dev`. The bug fixes are
in progress.** See **section 9** for where things stand and what's next.
T6–T8 are later work. D7 (moving helpers to the toolbox) is deferred until
the toolkit gets its own test work. The `toolbox` submodule
(ProgSnapToolkit) has its own suite (`toolbox/tests`), which is out of scope
except where section 4 suggests moving shared infrastructure there.

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
   * `main.py` calls `provena.db.base.init_app_tables()`, which runs `create_all`
     for the hand-written app tables plus their migrations; `provena/db/base.py`
     creates an engine at import.
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
     and `LinkAssignmentMap`'s `SubjectID`/`AssignmentID` key columns. `0900` collations are
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
* On the toolbox's `provena` branch, its own suite isn't green: everything
  under `tests/analytics` fails to collect (a provena-specific enum change
  removed `CompileMessageType`), and 3 of the remaining 15 tests fail. All
  of this predates the testing work. Use `--ignore=tests/analytics` and
  compare against a baseline when changing toolbox code.

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
for every tweak while the API is still changing. **Deferred (D7):** they
stay in `tests/support/` for now.

---

## 5. Sub-tasks

Each sub-task is meant to be one reviewable PR. **T0 blocks the rest.**
After T0, T1–T4 can go in any order.

### T0: Test infrastructure (blocking) — done

Production changes:
* `configs.py` reads its YAML files from `PROVENA_CONFIG_DIR` (default:
  `src/provena/config/`, so nothing changes for existing setups).
* `init_app_tables(bind=...)` takes an optional engine, so tests can build
  fresh or legacy schemas in a separate database.

Test side, as built:
* Root `pyproject.toml`: `pythonpath = ["src", "."]`, `testpaths`,
  `--strict-markers`, `xfail_strict = true`, markers (`concurrency`,
  `slow`), and coverage settings (branch coverage of `src/provena`).
* `tests/test_config.example.yaml`; the real `tests/test_config.yaml` is
  gitignored. `PROVENA_TEST_MYSQL_URL` overrides the URL. With neither,
  pytest stops with a message saying what to do.
* `tests/conftest.py`, in `pytest_configure` (before any test module is
  collected): creates `provena_test_<random>`, writes the three configs to
  a temp dir, sets `PROVENA_CONFIG_DIR`, and imports `provena.main`, so the
  import-time setup runs once, in the right order. `pytest_unconfigure`
  disposes the app's engines and drops the DB (unless `keep_database`).
  Test auth config: student pattern `*@student.test`, instructor whitelist
  `prof@instructor.test`, known instructor and submit API keys
  (`tests/support/app_config.py`).
* Fixtures: `app`, `client` (`TestClient`, per test), `test_db_engine`,
  autouse `clean_db` (deletes all rows except `Metadata` after each test,
  FK checks off), and `scratch_database` (a factory for extra throwaway
  DBs, for schema tests).
* `tests/support/`:
  * `databases.py`: create/drop/clear throwaway DBs, with the
    `provena_test_` guard. No provena imports (toolbox candidate).
  * `progsnap2_events.py`: `make_event(spec, event_type, **overrides)`,
    which fills in what the spec requires; `None` removes a column. No
    provena imports (toolbox candidate).
  * `provena_helpers.py`: `student_headers()`, `instructor_headers()`,
    `instructor_key_headers()`, `submit_key_headers()`; `event(...)`
    (provena's spec) and `seed_events([...])`, which stores events the way
    `/events` does, minus HTTP and auth.
* `test_smoke.py`: routers registered; all three engines use the test DB;
  `last_synced_order` is `-1` on an empty DB; posted and seeded events are
  readable; the instructor key reaches `/read/*`; `clear_tables` keeps only
  `Metadata`.
* `test_app_tables.py` (B7 regression): startup creates the whole schema;
  a fresh `LinkAssignmentMap` is keyed on the hash; a legacy table (with a
  row) migrates to exactly the fresh DDL; `init_app_tables` is idempotent;
  paths sharing a 300-char prefix are distinct; duplicates are rejected;
  paths differing only in case are distinct (pins D8's side effect).
  Checked against a mutant: disabling the migration fails the legacy test.
* `requirements-dev.txt` (`pytest`, `pytest-cov`, `httpx`); `CLAUDE.md`
  says how to run the suite.

The full run takes about 2 s.

### T1: Pure unit tests (no DB) — done

`tests/unit/`:
* `test_code_hashing.py`: BOM (leading only), CRLF→LF (a lone CR is kept),
  NFC; the hash strips outer whitespace only; Windows, macOS, BOM, and
  trailing-newline variants hash the same; `add_codestate_ids` only touches
  events with `Code`, overwrites a client `CodeStateID`, and agrees with the
  hash `/get_event_count` uses.
* `test_redirects.py`: the allowlist matrix (scheme, port, `:*`, userinfo
  and fragment tricks, `javascript:`/`data:`, no scheme or host, hostname
  case, IPv6 loopback) and query/fragment building. `xfail`: B12, B19.
* `test_roles_and_config.py`: whitelist/pattern/open matching (case,
  subdomains, suffix tricks); key matching (case, whitespace, empty keys);
  config validators and both startup warnings. B13 `xfail`.
* `test_google_backend.py`: Authlib stubbed. Covers the verified profile,
  missing `email_verified` (accepted), `email_verified: False`, incomplete
  profiles, Authlib errors → 400, and the `hd` hint passed to login.
* `test_backend_registry.py`: building from config, caching, and errors.

### T2: Auth and authorization — done

`tests/auth/`:
* `test_tokens.py`, with a controllable clock:
  * only the hash is stored; expiry by client type; `cli` slides and `web`
    doesn't; still valid at exactly `expires_at`; expired → None;
  * expired rows aren't deleted (pinned);
  * revoke affects only that token.
* `test_authorization.py`:
  * a 12-route × 13-credential matrix whose expected outcomes are spelled
    out in `EXPECTED` (review this table);
  * public routes;
  * `student: open` and `instructor: open` across every route;
  * a route-coverage test that fails when a route is added without an
    entry. Cells awaiting B20 are `xfail` (`KNOWN_BUGS`).
* `test_login_flow.py`, with `FakeBackend` (`tests/support/fake_auth.py`)
  and an https `TestClient`, since the session cookie is Secure:
  * allowlist enforcement;
  * first login, repeat login, and a new subject with the same email;
  * email case (one user, first spelling kept);
  * a changed email at Google keeps the old one (pinned);
  * `cli` delivers via query and `web` via fragment; the default is `web`;
  * `state` is echoed;
  * a callback without `/auth/login` → 400, and a replay → 400;
  * a login with no role still gets a token (which then fails role checks);
  * logout only revokes the caller's own token.

### T3: Write endpoints — done

`tests/write/`:
* `test_events.py`:
  * Stored columns: server timestamps, `CodeStateID`s, raw `Code` kept, and
    mixed column sets. Unknown fields are dropped; a missing event-specific
    column is a warning.
  * Over-length values go to the LoggingError fallback, and one bad event
    sends the batch to per-event retries.
  * Validation failures, non-JSON bodies, and non-list bodies are logged.
  * Other routes return plain 422s.
  * Duplicate `EventID`s (D13): retries, explicit nulls, a reused ID,
    within-batch duplicates, and a case-only difference (a guard that must
    keep passing).
  * Pinned: any `SubjectID` accepted (Q-c).
  * `xfail`: B14, B15, B21, B22.
* `test_submit.py`:
  * Fan-out for 1 or N subjects × 1 or N sections; parent/child links;
    Score on the parent only; one shared ServerTimestamp.
  * `SubjectIDs: []` → 422. An empty `CodeState` is accepted, and the
    read side copes with it (D14). Omitting nullable fields → B23.
  * DB failures return `success: false`, with no fallback.
  * `xfail`: B5, B16, B23.
* `test_event_count.py`:
  * Matching: by hash (ignoring line endings), with the suffix fallback
    (whole path component, exact path, case-insensitive). A hash match
    suppresses the fallback.
  * Scope: only the given subjects; Submit excluded.
  * Renames: one rename, a chain, a cycle, and other subjects' renames
    ignored.
  * `xfail`: B1.
  * Each test uses unique paths so B1 can't make results depend on order.
* `test_error_handlers.py`: an unhandled exception → 500 plus a
  LoggingError event. `xfail`: B2, B3, B25.
* `tests/test_startup.py` (D9): a connection error at startup is
  tolerated. `xfail`: B26.
* Partial writes across separate commits (`_add_error_event`'s two
  commits, `google_callback` then `issue_token`) aren't forced yet. With
  B14 (now fixed), the link-table write always failed, so the partial state
  was the normal state. Worth adding now.

### T4: Read endpoints — done

`tests/read/`:
* `test_listing.py`:
  * assignments and subjects: distinct, nulls skipped, case variants
    collapse (Q-b);
  * time range: string comparison pinned, unknown subject → nulls;
  * codestate sections need a `File.Save`;
  * per-assignment stats: last submission and max score, child events
    without scores ignored. `xfail`: B8;
  * `last_synced_order`.
* `test_edits.py`:
  * ordering by timestamp and then `Order`; nulls stripped; events without
    a `ClientTimestamp` excluded;
  * renames: single, chain, the old name reused later, the new name used
    earlier, other subjects ignored;
  * the `last_codestate_id` cutoff, including same-timestamp events (the
    `"\u0000"` trick works under MySQL), across a rename, and an unknown
    ID;
  * `edits_in_range` bounds;
  * `xfail`: B10.
* `test_assignment_mapping.py`:
  * Matching: the basename, a whole path component, only the subject's own
    files, multi-file submissions, only the latest submission, and a file
    in several assignments.
  * Updates are idempotent. A resubmission moves `LastValidTimestamp`.
  * Only the latest submission is mapped, but rows from earlier updates
    are kept when a different file is resubmitted (D16).
  * Pinned: case variants collapse into one mapping (see the B7 note).
  * `update_mapping_table` returns `null`.
  * `xfail`: B9, B17.

### T5: Concurrency and races — done

`tests/concurrency/`: a session-scoped `live_server` fixture (uvicorn in a
thread, same app object) plus `httpx.AsyncClient` + `asyncio.gather`.
* 20 overlapping `/events` batches across 4 sessions: no lost or
  duplicated rows, correct `last_synced_order`.
* 10 concurrent `update_mapping_table` calls: all 200, no duplicate rows,
  no deadlocks.
* Five simultaneous first logins for one new user: one user, all succeed.
  That only holds because of B18 (see D17).
* Pool exhaustion (`slow`): 12 requests each holding a write connection for
  3 s against a pool of 10 with a 2 s timeout gives 10× 200 and 2 failures
  (500 today; 503 after B25), with no deadlock. B25 itself is checked
  quickly in `tests/write/test_error_handlers.py`.
* Not tested: B1's thread-safety (the B1 fix covers it), and retried
  batches beyond D13's sequential test.

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

## 6. Bugs found (each has an `xfail(strict=True)` test)

IDs are stable so tests and PRs can reference them.

Status (2026-10-09): B1–B3, B5, B8–B10, B12–B17, B19–B23, B25, and B26 have
`xfail` tests. B4 isn't a bug. B6 is cosmetic. B7 is fixed. B24 was withdrawn. B18 is a
performance and design issue, shown by the concurrency tests. B19–B26 are
behavior changes decided in section 8 (D9–D18), tracked as bugs so that
each one's test flips when it's done.

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
* **B4. ~~The `/events` branch of `validation_exception_handler` may never
  run.~~** Not a bug: `request.scope["route"]` is set, and the branch runs
  (`tests/write/test_events.py`).
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
  * **Fixed** (2026-10-09), per D8: `LinkAssignmentMap` was removed from the
    ProgSnap2 spec and is now a hand-written SQLAlchemy model
    (`provena/assignments/models.py`) whose unique key uses a MySQL-generated
    SHA-256 of the path (`CodeStateSectionHash`), so uniqueness is exact.
    `provena/db/migrations.py` upgrades existing tables in place: it adds the
    hash and `id` columns and swaps the old prefix key for the hash key.
    Regression tests: `tests/test_app_tables.py`.
  * Behavior change, smaller than it looks: the key is now exact (case- and
    accent-*sensitive*), but `update_mapping_table`'s `SELECT DISTINCT`
    already collapses case variants under the `ai_ci` collation before
    inserting, so only direct inserts see the difference.
  * Still open: startup (`logging.py`, and `init_app_tables` in `main.py`)
    still swallows schema-init errors, so a future DDL failure would again
    leave a half-created DB silently (D9).
  * Near miss: `codestates`' unique key `(CodeStateID, CodeStateSection)` is
    (255 + 512) × 4 = 3068 bytes, just under the limit. Widening either
    column would hit B7 again there.

* **B8. `/read/assignments/{id}/subjects` returns 500 when a subject's
  submissions have no `Score`.** `MaxScore` is a required `float` in the
  response model, but `Score` is nullable on `/submit`.
* **B9. A resubmission doesn't update the mapping's `CodeStateID`.** The
  upsert only sets `LastValidTimestamp`, so `CodeStateID` keeps the first
  submission's code. The model documents it as "the last submission".
* **B10. The `/read/edits` cutoff searches every subject's events.**
  `_get_end_client_timestamp` looks up `last_codestate_id` without filtering
  on `subject_id`. Identical code from another student (e.g. unmodified
  starter code) can set the cutoff.
* **B12. A non-numeric port in `client_redirect_uri` causes a 500.**
  `urlsplit(...).port` raises `ValueError` when an exact-port allowlist
  entry is checked. The request isn't let through; it just fails as a 500
  instead of a 400.
* **B13. A non-ASCII `X-API-Key` causes a 500.** `secrets.compare_digest`
  raises `TypeError` on non-ASCII `str`.
* **B14. Error details are never stored.** *Significant.*
  `_add_error_event` calls `add_link_table_entry('linkloggingerror')`. The
  toolbox's case-insensitive table lookup is broken for quoted names: SQLAlchemy's
  `quoted_name.lower()` returns the name unchanged, so the map is identity. The
  lookup fails and is logged and swallowed. Every `LoggingError` event in
  production has no `LinkLoggingError` row, so the error text and request
  body are lost.
  * **Fixed in provena** (2026-10-09, `fix/b14-logging-error-details`):
    `_add_error_event` now passes the exact name `'LinkLoggingError'`.
  * **Still open in the toolbox:** `SQLTableManager.get_table`'s lowercase
    map should use `str(name).lower()`. Fix it on the toolkit's main
    branch, then bring it into the `provena` branch.
  * Side effect: the pool-exhaustion test got slower (~6 s → ~10 s). Each
    error handler now also writes the link row, and that second checkout
    also waits with the event loop blocked (B18).
* **B15. A body that isn't valid JSON is logged without authentication.**
  JSON decoding fails before dependencies run, so `/events`' validation
  handler writes a `LoggingError` event (plus a link row, once B14 is
  fixed) for anonymous requests. Valid JSON that fails the model is
  checked *after* auth and gets a 401, so anonymous callers can't plant
  events, only error rows: a spam/DoS vector.
  * **Fixed** (2026-10-09, `fix/b15-unauthenticated-error-logging`): the
    handler runs `require_student_role` itself before logging, and returns
    its 401/403. The DB lookup runs on the event loop like the rest of the
    handler (B18).
* **B16. Multi-subject `/submit` reuses one `EventID`.** Each subject's
  parent Submit event is a copy of the same dict, so they share an
  `EventID`, and every subject's children point at that same
  `ParentEventID`.
* **B17. Late-synced logs never get mapped.** `update_mapping_table` only
  considers submissions newer than the newest mapping (across all
  subjects). Take a submission whose logs weren't synced yet: it maps to
  nothing. If any later submission is then mapped, it's never retried, even
  after the logs arrive.
* **B18. Async handlers do blocking DB work on the event loop.**
  `google_callback` and the three exception handlers in `main.py` are
  `async def` but make synchronous DB calls. While they run, the whole
  server stalls. Under pool exhaustion, each error handler can block every
  request for up to `pool_timeout` (2 s) waiting for a connection. The same
  thing currently *prevents* the first-login race (D17). Fix: make them
  plain `def`, or run the DB work in a threadpool. That fix would expose
  the race.

* **B19 (D10).** An explicit default port (`https://host:443`) should match
  an allowlist entry without a port, and vice versa.
* **B20 (D11).** A valid submit key on a student or instructor route should
  get 403 `insufficient_role`, not 401.
* **B21 (D12).** `/events` should overwrite a client-supplied
  `ServerTimestamp` and say so in the response's warnings.
* **B22 (D13).** Duplicate `EventID`s: see D13 for the intended behavior.
* **B23 (D15).** `/submit`'s `Score`, `ScoreDetails`, `TermID` and
  `CourseID` should default to `None`.
* **B24.** Withdrawn: see D16. Only the latest submission is meant to be
  mapped.
* **B25 (D18).** Pool exhaustion (`sqlalchemy.exc.TimeoutError`) should be
  a 503 with `Retry-After`.
* **B26 (D9).** A schema/DDL error at startup should stop the server. A
  connection error should still be tolerated.

(B11 was skipped: it became D15.)

Open behavior questions (not clearly bugs):
* **Q-a.** `/events` returns **200** for malformed input after logging it as
  an error. **Answered: intentional**, so the client drops the batch instead
  of retrying it forever. 2026-10-09
* **Q-b.** IDs compare case- and accent-insensitively under MySQL's
  collation. **Answered for `SubjectID`: desired**, since SubjectIDs are
  currently emails (documented in `CLAUDE.md`, including the caveat for new
  auth backends). For paths and the rest of the repo it's open:
  provena-code/provena-server#22. 2026-10-09
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
* **D7** (infra): Move the shared test helpers (`tests/support/databases.py`,
  `progsnap2_events.py`) to the toolbox as `progsnap2.testing`? **Not yet.**
  Do it when ProgSnapToolkit gets its own, more extensive testing work.
  Moving them earlier gains nothing, and until then changes only have to
  be made in one place. When they move, they should go into the toolkit's
  *main* branch, not the `provena` branch the submodule tracks, which has
  provena-specific changes that aren't compatible with main. Keep the
  helpers free of provena imports so they can move as-is. 2026-10-09
* **D8** (B7): How to fix `LinkAssignmentMap`'s too-long unique key?
  **Remove it from the ProgSnap2 spec and define it as a hand-written
  SQLAlchemy model** (like the auth tables), with a unique key on a hash of
  the path, plus an in-place migration for existing DBs. The spec, and the
  toolbox's spec machinery, stay focused on the ProgSnap2 format and don't
  grow SQL-specific features. Considered and rejected: a prefix index (lossy
  uniqueness; production's longest path is already 241 chars), and narrowing
  the ID columns. 2026-10-09
* **D9** (B7 follow-up): Fail loudly if schema initialization fails?
  **Yes for schema/DDL errors; keep tolerating connection errors**
  (`provena.service` has `Restart=always`). Covers both the ProgSnap2 tables
  (`logging.py`) and `init_app_tables`. Tracked as B26; only the
  `init_app_tables` side is tested (`tests/test_startup.py`). 2026-10-09
* **D10** (T1): Should an explicit default port match an allowlist entry
  without a port? **Yes**, in both directions, as `redirects.py`'s
  docstring says. → B19. 2026-10-09
* **D11** (T2): A valid submit key on a student or instructor route? **403
  `insufficient_role`**, not 401. → B20. 2026-10-09
* **D12** (T3): A client-supplied `ServerTimestamp`? **The server
  overwrites it, with a warning in the response.** → B21. 2026-10-09
* **D13** (T3/T5): Duplicate `EventID`s? EventIDs should be unique and the
  client should guarantee that, but never lose data if a duplicate turns up
  anyway. → B22. 2026-10-09
  1. A unique key on `EventID`. This likely needs a toolbox change, since
     the toolbox builds the main table from the spec.
  2. On a duplicate, compare the incoming event with the stored row:
     * an exact match is a retry: skip it, with a warning;
     * otherwise, store it under a new unique `EventID`, with a warning.

  Keep this logic in its own function. Design notes for the comparison:
  * **Avoid false matches.** A false match silently drops data, so it's the
    only dangerous direction; a false mismatch just stores an extra copy.
    * Compare in Python, on the values read back. Not in SQL, where the
      `ai_ci` collation equates `a`/`A` and `é`/`e`.
    * Ignore server-assigned columns (`ServerTimestamp`) and derived ones
      (`CodeStateID`, which follows from `Code`).
    * Treat a missing key and an explicit null as equal, since the model
      drops `None` before storing.
    * Floats (`Score`) and other round-tripped values may differ slightly.
      That direction is safe, but worth a test once implemented.
  * **Duplicates within one batch** need the same handling as against the
    DB.
  * **Concurrent retries** of the same batch could both pass a
    check-then-insert. The unique key makes the second insert fail, and
    that failure must route into the comparison, not into the
    malformed-event fallback.
  * Tests: `tests/write/test_events.py`, "Duplicate EventIDs".
    `test_difference_only_in_case_is_not_a_match` passes today and guards
    the fix.
* **D14** (T3): `/submit` with `CodeState: []`? **Allowed.** Some projects
  have no files; the autograder sends e.g. Score 0. Checked against the
  read side (stats, mapping, event count) by
  `test_empty_submission_works_with_the_read_side`, and nothing breaks.
  2026-10-09
* **D15** (T3): `/submit`'s nullable fields? **Default them to `None`.** Per
  the spec they aren't required columns. → B23. 2026-10-09
* **D16** (T4): Which submissions get mapped, and are stale rows deleted?
  * **Only the latest submission** per subject and assignment is mapped.
    Submit is assumed to come only from real, graded submissions, so the
    latest one is the one that matters. This may change later.
  * **Stale rows aren't deleted.** Updates are incremental, so a file
    mapped by an earlier update stays mapped after a later submission of
    different files. That's a known quirk, but deleting those rows would
    make files vanish from an assignment's history after a resubmission.
    A more robust model (e.g. viewing by individual submission rather
    than by assignment) is possible future work.
  * Briefly logged as B24 and then withdrawn. 2026-10-09
* **D17** (T5): The first-login race. **Make both requests succeed**, fixed in
  the same PR as B18. 2026-10-09
  * When it can happen: the same person's very first login, twice at once,
    e.g. VS Code and the web app finishing Google's redirect within
    milliseconds of each other. Today it can't happen at all, because the
    callback blocks the event loop (B18) and production runs one worker.
    It becomes possible once B18 is fixed or there are more workers.
  * What happens then: the second request gets a 500. The user retries and
    it works, since the user now exists.
  * Making both succeed is cheap: catch the `IntegrityError` from the user
    insert, roll back, and re-select the user by email. The same applies
    to the identity insert, which has its own unique key on
    `(provider, subject)`.
  * Do that fix together with B18, since it's the fix that exposes the race.
    Then turn `test_simultaneous_first_logins_create_one_user` into a real
    race test (the barrier approach from T5 works once the handler no
    longer blocks the event loop).
* **D18** (T5): Pool exhaustion? **503 with `Retry-After`.** → B25. Also
  check what the extension does on a 503. 2026-10-09

---

## 9. Where things stand (handoff, 2026-10-09)

Section 6's per-bug notes are updated by each fix PR; this section is the
overview. Update it when a batch of work lands.

### Merged to `dev`

* T0–T5 test suite (#5, via `feature/tests`).
* B14: error details stored (#3). B15: credentials checked before logging
  invalid `/events` bodies (#4).
* B7 fix (`LinkAssignmentMap` as a hand-written model with a hash key, plus
  a migration), from before the PR workflow.

### Open PRs (against `dev`), one per fix unless noted

| PR | Fixes | Notes |
|---|---|---|
| #6 | B5, B6, B8, B13, B23, plus `min_items` → `min_length` | Bundle of trivial fixes |
| #7 | B1 | |
| #8 | B2 | Adds `_RecordRequestBody` ASGI middleware. **Conflicts with #13** on one import line in `main.py`; rebase whichever merges second |
| #9 | B10 | |
| #10 | B16 | Check whether the instructor client groups Submits by `ParentEventID` |
| #11 | B12 + B19 | Same function, so bundled |
| #12 | B20 | |
| #13 | B21 | See #8 |
| #14 | B25 | Pool-exhaustion test drops to ~3 s |
| #15 | B26 | Startup now fails on schema errors: **deploy note** in the PR |
| #16 | Partial-write tests | Tests only; outcomes pinned as `[inferred]` OK |

### Still to do (no input needed)

* **B18 + D17**: run the blocking DB work in `google_callback` and the
  `main.py` exception handlers off the event loop (threadpool). Catch the
  duplicate-user `IntegrityError` and re-select. Make
  `test_simultaneous_first_logins_create_one_user` a real race test
  (barrier). **Wait until #8, #13, #14 and #15 are merged**; they all touch
  the same handlers.
* After all of the above merges: re-run the suite, and update section 6's
  status line and section 5's per-file `xfail` lists, which still describe
  the pre-fix state.

### Waiting on design discussion (GitHub issues)

* B22, unique `EventID`s: #17. Includes performance notes and the
  auto-increment `id` idea.
* B17, late-synced logs never mapped: #18. Leaning towards an
  upsert-only periodic full recompute behind `GET_LOCK`. Production fix =
  clear the table and recompute.
* B3, the dead `MySQLdb.OperationalError` handler (remove or fix?): #19.
* B9, the mapping's `CodeStateID` isn't updated on resubmission: #20.
* The toolbox side of B14 (`get_table`'s case-insensitive lookup works on
  readers but not writers): CSSPLICE/ProgSnapToolkit#1. Provena is
  unaffected now.
* `ServerTimestamp`s are the server's *local* time, compared as strings,
  so DST changes can reorder them: a comment on #18. The simplest fix is to
  run production in UTC.
* Case/accent-insensitive comparisons beyond `SubjectID`: #22.
* T6, real client payloads: #23.
* Client follow-ups for the new 503 (and nullable `MaxScore`):
  provena-code/provena-vscode#2 and provena-code/provena-client#2.

### Other answers (2026-10-09)

* **Production and the B7 migration:** not deployed yet; the DB is backed
  up. If the production table name is lowercase (Linux is
  case-sensitive), expect a leftover stale `linkassignmentmap` table, or
  with #15 a failed start. Handle it at deploy time.
* **Behaviors pinned as acceptable:** expired tokens aren't deleted; an
  email changed at Google keeps the old address for role checks; a login
  with no role still gets a token. All confirmed fine.
* **CI (T8):** not needed for now (solo project).
* **Cleanup:** keep the merged branches and the second worktree for now.

### Environment notes

* The bug-fix PRs were made from a second worktree,
  `../ProvenaServer-fixes`, to avoid clashing with branch switches in the
  main checkout. It reuses the main checkout's `.conda` env and needs its
  own copy of `tests/test_config.yaml`. Remove it with
  `git worktree remove ../ProvenaServer-fixes` when no longer needed.
* `gh` must be logged in as `thomaswp`. The `twprice_ncstate` enterprise
  account can't create PRs or issues in `provena-code`.
* `tests/test_startup.py` (in #15) re-imports `provena.main` and
  `provena.api.logging.logging` to test import-time startup, and restores
  their module state afterwards. If startup moves into a `lifespan` hook
  (T7), replace this with plain calls.
