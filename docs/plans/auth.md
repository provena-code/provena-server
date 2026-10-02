# Auth: design notes

Status: server-side Google OAuth + token issuance implemented, plus
role-based authorization (`require_instructor_role`/`require_student_role`/
`require_submit_permission`). See "Implementation status" at the bottom for
what exists vs. what's still open.

## Goal

Add authentication to ProvenaServer. The server (not the client) must hold OAuth
client secrets and complete the OAuth exchange, then hand the client (VS Code
extension or web app) a server-issued token to use on subsequent requests.

Requirements as stated so far:
* Modular: pluggable auth "backends" — OAuth providers (starting with Google)
  and simpler mechanisms (e.g. a static whitelist of allowed users/emails) —
  but only **one backend is active/default per server deployment** at a time
  (set via `auth_config.yaml`), not a live multi-provider picker shown to
  users. "Modular" means swappable per deployment/config, not simultaneous
  multi-provider UI.
* Start with Google OAuth end-to-end; design so swapping in a different OAuth
  provider or a whitelist-only mode later (per deployment) doesn't require
  reworking the core.
* Two client types: a VS Code extension and a web app. Both need to complete a
  login and end up with a token they attach to API requests. Neither client
  needs to know or care which backend is active — they hit generic
  `/auth/login` / `/auth/logout` endpoints and the server does whatever the
  configured backend requires.

## Decisions so far

* **Library**: Authlib for the Google OAuth2/OIDC handshake only (each
  backend's job is to produce a verified `(provider, external_id, email,
  name)`); we own the User/OAuthIdentity/Token SQLAlchemy models and issuance
  logic ourselves rather than adopting a framework like `fastapi-users`. A
  whitelist backend would skip the OAuth handshake and just check a submitted
  email/identifier against an allowlist to reach that same last step.
* **Token format**: opaque, DB-backed tokens (a `Token`/session table), not
  JWTs. Rationale: the server already hits a shared DB on every request
  regardless, traffic is modest (~100 concurrent users per `locust.md`), and
  instant revocation / immediate role changes matter more here than shaving
  one DB lookup.
* **DB placement**: new tables in the same physical database as the logging
  data (same `sqlalchemy_url`), defined with hand-written SQLAlchemy
  declarative models — not generated from the ProgSnap2 spec. See "Why this
  doesn't fit the existing DB" below.
* **VS Code token delivery**: loopback local server pattern — the extension
  starts a temporary localhost HTTP server, uses its URL as part of the OAuth
  redirect target, and the server sends the browser there with the token once
  login completes (same family of pattern as the GitHub CLI / many VS Code
  auth extensions).

## Why this doesn't fit the existing DB

See the "Known architecture wart" note in `CLAUDE.md`. The only DB today is the
ProgSnap2 logging DB, generated from `src/provena/progsnap2-provena.yaml` via
the `toolbox` spec machinery — it's schema-generated for append-only event
logs, keyed around `SubjectID`/`EventID`/etc. Auth concepts (users, identities,
sessions/tokens, roles) don't map onto that and shouldn't be forced into it.

Plan: introduce a second, hand-written set of SQLAlchemy ORM models (declarative
`Base`, normal `Table`/relationship definitions) for everything auth-related —
users, linked OAuth identities, tokens/sessions, and (later) roles/whitelist
entries — living in the same database as the logging tables, but managed
separately (plain SQLAlchemy `create_all` or Alembic, not the ProgSnap2 spec
generator). This is the first hand-written table set in the app, so it's a
chance to set a pattern for future non-logging tables.

## Client-facing API (backend-agnostic)

Clients never see backend-specific routes. The generic surface:

* `GET /auth/login?client_redirect_uri=<uri>&client_type=cli|web&state=<opaque>`
  — starts a login using whichever backend is configured as active. If that
  backend is OAuth-based, the server redirects the browser to the provider
  (Google); if it's a non-OAuth backend like whitelist, this would instead
  need to serve/accept a simple form (not built yet — deferred, per the web
  app not needing provider UI today). `client_redirect_uri` says where to
  send the browser once login completes — see "Redirecting back to the
  client" below. `state` is optional, opaque to the server, and returned
  unchanged on that same final redirect — see "Client-side CSRF (`state`)"
  below.
* Provider callback (`/auth/google/callback`, etc.) — internal, backend-
  specific, registered with the provider itself (e.g. in the Google Cloud
  Console). Clients never hit this directly.
* `POST /auth/logout` — given a valid token (however the client normally
  authenticates), revokes it (deletes/expires the DB row).

## Token flow (draft)

1. Client (extension or web app) directs the user's browser to `/auth/login`
   (with `client_redirect_uri` — see below).
2. Server redirects to Google; Google redirects back to the server's fixed,
   provider-registered callback with an auth code; server exchanges it
   (server-side secret) for Google's tokens and verifies the ID token /
   fetches the user's profile.
3. Server looks up or creates a local `User` + linked `OAuthIdentity` record,
   issues its own opaque DB-backed token, and redirects the browser to the
   `client_redirect_uri` from step 1 with the token and a few other values
   attached as query params: `token`, `email` (also the intended `SubjectID`
   for logging), `expires_at` (ISO 8601, naive UTC with an explicit `Z`
   suffix), and `name` if Google returned a display name. Kept intentionally
   small -- a client that later needs fresher/other profile info should call
   a `/auth/me`-style endpoint (not yet built) rather than trust a
   login-time snapshot indefinitely.
4. Client stores that token and sends it on future requests (likely
   `Authorization: Bearer <token>`), replacing/extending today's placeholder
   `X-API-Key` check in `src/provena/api/read/common.py`.

## Client-side CSRF (`state`)

There are two independent "state" concerns in this flow, easy to conflate:

* **Server ↔ Google**: already fully handled by Authlib internally (its own
  session-stored nonce, checked in `authorize_access_token`). Invisible to
  clients.
* **Server ↔ client**: not handled by anything above -- without it, a stray
  or spoofed request landing on the client's own callback (the VS Code
  loopback server, or the web app's callback route) could be accepted as if
  it were a real login completion. This is the client's own responsibility:
  generate a random `state` value before starting the flow, pass it as
  `/auth/login?...&state=<value>`, remember it, and verify the `state` that
  comes back on the final redirect matches before trusting the accompanying
  `token`. The server treats this value as opaque -- it's carried through the
  login session (alongside `client_redirect_uri`/`client_type`) and returned
  unchanged; the server does not generate, interpret, or validate it itself.

### Redirecting back to the client

* **VS Code extension**: starts a temporary loopback HTTP server on an
  arbitrary local port before opening the browser, and passes
  `client_redirect_uri=http://127.0.0.1:<port>/callback` to `/auth/login`. Once
  the server finishes the OAuth exchange it 302s the browser to that URI with
  the token; the extension's loopback listener picks it up and shuts itself
  down. The system browser window can then show a plain "you can close this
  tab" page.
* **Web app**: functionally the same flow, but "getting the token back is
  easier" — `client_redirect_uri` just points at a route already served by the
  web app itself (e.g. `/auth/finish`), which reads the token from the
  URL and stores it, no loopback server needed. Since there's only one active
  backend, the web app doesn't need a provider-choice UI — a login action is
  just "navigate to `/auth/login?client_redirect_uri=<my own callback route>`".
* **Security**: the server must not blindly redirect anywhere the caller says.
  `client_redirect_uri` needs to be checked against an allowlist configured in
  `auth_config.yaml` (e.g. `http://127.0.0.1:*` / `http://localhost:*` for the
  VS Code case, plus the specific web app origin(s)) — otherwise this becomes
  an open redirect that leaks tokens to arbitrary sites. Exact allowlist
  matching rules are still to be worked out.

## Token validation & "needs reauth"

Per the VS Code scenario, the client needs to distinguish "your token is
missing/expired/invalid, please log in again" from other failures, so it can
trigger the login flow automatically rather than surfacing a generic error.
Plan: authenticated endpoints return a consistent, machine-readable signal on
auth failure (e.g. `401` with a specific `detail`/error-code value such as
`{"detail": "reauth_required"}`), distinct from e.g. a `403` for
authorization failures (out of scope for now, but worth reserving the
distinction). Both clients check for that specific signal and, on seeing it,
kick off the `/auth/login` flow again rather than treating it as a generic
error.

Token lifetime itself (fixed expiry vs. sliding/refreshed-on-use) is still an
open question — see below.

## Scope of this effort

The first pass was **authentication only**: getting a user logged in with a
verified identity (User/OAuthIdentity/Token tables + a Google OAuth backend,
plus the whitelist-as-alternative-login-method backend below). Authorization —
roles, permissions, what an authenticated user is allowed to do — is the
subject of the "Roles & permissions" section below, now in progress as a
follow-up on top of the authentication layer above.

## Roles & permissions (authorization)

Status: design in progress, pending a few confirmations (see "Open questions"
at the end of this section) before implementation.

### Roles are fixed in code, not a configurable set

Two roles, matching the instructor's actual need -- not a general per-endpoint
permission matrix, which was considered and deliberately dropped as
unnecessary complexity:

* `student`: may write (`/events`, `/get_event_count`, `/submit`).
* `instructor`: may read (`/read/*`) *and* write -- i.e. everything a student
  can do, plus instructor-only endpoints. So `require_student_role` (below)
  passes for a qualifying instructor too, not just a qualifying student.

### Role membership is configurable, per role, in `auth_config.yaml`

Each role is independently configured as exactly one of:

* `whitelist` -- an exact list of allowed emails (e.g. specific
  instructors/TAs).
* `pattern` -- a glob-style pattern matched against the user's email, e.g.
  `*@ncsu.edu` for "any address at this school can write" (matched
  case-insensitively via Python's `fnmatch`).
* `open` -- no restriction at all. Any authenticated user qualifies, *and* (to
  satisfy "if student roles are set to any then it should be ok to not
  provide a token") no credential at all -- no API key, no login -- is
  required either. This is allowed on the `instructor` role too, but a
  startup warning is logged given the stakes of leaving all student data
  readable with zero gatekeeping.
* No blacklist mechanism -- not needed, per the instructor's explicit call.

Draft shape:

```yaml
roles:
  instructor:
    type: whitelist
    emails: ["prof@ncsu.edu", "ta@ncsu.edu"]
    api_keys: ["instructor-key-for-some-admin-script"]
  student:
    type: pattern
    pattern: "*@ncsu.edu"
    api_keys: ["autograder-key"]
```

### API keys: two kinds, by privilege, not by role

Confirmed: students never use API keys themselves (that's only ever an
instructor exercising something, e.g. for testing); the only real external
service caller is the autograder. So the split is by *privilege level*, not
by role:

* `roles.instructor.api_keys` -- full instructor privilege (and, since
  instructor ⊇ student, these also satisfy `require_student_role` and
  `require_submit_permission`). Meant to stay private -- instructor/dev use,
  testing, admin scripts.
* `roles.student.submit_api_keys` -- a narrow, separate credential that
  satisfies *only* `require_submit_permission` (the `/submit` gate below),
  nothing else. Deliberately scoped down since this is the one meant to live
  inside autograder code, which is less trusted/secure than an instructor's
  own machine.

There is no general-purpose "student role" API key -- a plain student
identity is always proven via OAuth login, never a key.

This replaces today's single, repo-wide `testing_api_keys` (on
`PS2APIConfig`, read from `write_config.yaml`), which this server's code
stops reading (the field itself is part of the `toolbox` submodule and isn't
touched).

### `require_instructor_role` / `require_student_role`

Replaces today's placeholder `require_api_key` in
`src/provena/api/read/common.py`. Passes if, in order:

1. The role is configured as `open` (short-circuits everything else -- no
   credential required at all), or
2. A valid `roles.instructor.api_keys` key is presented (`X-API-Key` header)
   -- always sufficient, for either dependency, or
3. A valid bearer token resolves to a `User` whose email satisfies the
   instructor role's whitelist/pattern -- also always sufficient for either
   dependency, or
4. *(`require_student_role` only)* the student role is `open`, or a valid
   bearer token's email satisfies the student role's whitelist/pattern.

Failure modes, kept deliberately distinct: `401 {"detail": "reauth_required"}`
when there's no valid credential presented at all (consistent with the
existing token-expiry signal, so a client knows to trigger `/auth/login`);
`403` when there *is* a valid identity but it doesn't satisfy the role (e.g. a
logged-in student hitting an instructor-only endpoint) -- a client shouldn't
loop into a pointless re-login for this one.

### `require_submit_permission` (`/submit` only)

Confirmed: `/get_event_count` is gated like `/events` (plain
`require_student_role` -- the student's own VS Code session may call it
directly). Only `/submit` gets the stricter gate, since it's invoked by the
autograder, not the student's own session -- there's no user sitting at a
browser to complete a login for that request, and a plain student OAuth token
doesn't vouch for the autograder's legitimacy. `require_submit_permission`
passes if, in order:

1. The student role is `open` (no credential required at all -- the
   instructor doesn't care), or
2. An instructor-level credential is presented (instructor API key, or an
   OAuth login matching the instructor role) -- instructors can always do
   everything, or
3. A valid `roles.student.submit_api_keys` key is presented.

A plain student-matching OAuth token alone is **not** accepted here, by
design. If the student role is not `open` and `submit_api_keys` is empty,
`/submit` becomes effectively uncallable by anything but an instructor
credential -- log a startup warning in that case rather than failing silently
at request time.

### Non-goals

Role checks gate *whether a request is allowed at all*; they don't verify
that the authenticated identity matches the `SubjectID` claimed inside the
request body, which remains self-reported by the client as it is today (e.g.
nothing stops an authenticated student from posting events tagged with a
different student's `SubjectID`). Enforcing "this token's email must match
the `SubjectID` of the events it's posting" is a plausible future tightening,
not part of this pass.

## Whitelist: two distinct mechanisms

"Whitelist" covers two separate concerns, both in scope, kept as independent
pieces in the modular design:

1. **Whitelist as an authentication backend** — a pluggable login method in
   its own right (e.g. for local/dev use, or environments where a full Google
   OAuth round trip isn't wanted): a submitted email/identifier is checked
   against an allowed list and, if present, treated the same as a verified
   OAuth identity for the purposes of issuing a token.
2. **Whitelist as authorization** (restricting what an OAuth-authenticated
   user can do, e.g. instructor access) — this is now the `whitelist` role
   type in "Roles & permissions" below, not a separate mechanism.

## Config: `auth_config.yaml`

Google OAuth client credentials (Cloud Console client id/secret, redirect
URIs, etc.) and other auth settings will follow the existing
`read_config.yaml`/`write_config.yaml` pattern: a gitignored `auth_config.yaml`
created from a checked-in `auth_config.example.yaml`, living alongside the
other config files in `src/provena/`.

## Deployment model (context)

Each deployment of this server is run by one instructor for one class, and
authenticates against that instructor's institution's identity provider — for
us, Google (NC State). Someone else deploying this code for their own class
might want Microsoft or GitHub instead. So: a given running server only ever
has one active backend (per the "single default provider" decision above),
but the backend implementation needs to be swappable/addable without
reworking the core, since different deployments will want different
providers. This is the concrete reason "modular" matters here — it's about
portability across deployments, not a multi-provider picker within one
deployment.

## Token lifetime policy (decided)

Different policies for the two token "purposes", since they have different
risk profiles and UX needs:

* **VS Code / CLI tokens** (write-only logging traffic, lower sensitivity):
  minimize re-login. Long-lived with **sliding expiration** — each successful
  authenticated use pushes out the expiry (up to some max session age), so an
  actively-used extension effectively never has to re-authenticate; only a
  long-idle token expires.
* **Web app tokens** (instructor-facing read access to student data, higher
  sensitivity): standard web session best practices — a shorter, fixed-ish
  expiry rather than indefinite sliding renewal.

Both are the same `Token` table/mechanism; the policy (sliding vs. fixed, and
the TTL) is just a couple of fields set based on which client type requested
the token (passed at `/auth/login` time, e.g. `client_type=cli|web`).

## Logout scope (decided)

Logout revokes only the current token/session, not all sessions for a user.
Per the instructor: logging out is mainly for switching users (e.g. a shared
machine), which is rare, so a "log out everywhere" isn't needed now.

## Implementation status

Built (server side):

* `src/provena/auth/models.py` — `User`, `OAuthIdentity`, `Token` (SQLAlchemy
  2.0 declarative style, `Mapped`/`mapped_column`), tables `auth_users`,
  `auth_oauth_identities`, `auth_tokens`. Own engine/session in
  `src/provena/auth/db.py`, reusing `api_config.database_config.sqlalchemy_url`
  but with a separate declarative `Base` from the ProgSnap2 tables. Created via
  `Base.metadata.create_all` (wrapped in try/except, matching the existing
  resilience pattern in `logging.py`'s DB init) at import time in
  `src/provena/api/auth/auth.py`.
* `src/provena/auth/tokens.py` — `issue_token`/`resolve_token`/`revoke_token`.
  Tokens are stored only as a SHA-256 hash; the raw value is returned once, at
  issuance. `resolve_token` enforces expiry and applies the sliding renewal
  for `client_type="cli"`. TTLs come from `auth_config.token`
  (`cli_ttl_days`/`web_ttl_hours`).
  * **Gotcha hit during implementation**: `DateTime(timezone=True)` is a
    no-op on both SQLite and MySQL (neither preserves tzinfo through a round
    trip), so an aware `datetime.now(timezone.utc)` compared against a value
    read back from the DB raises `TypeError`. Fixed by standardizing on naive
    UTC everywhere in this module (see the `_utcnow()` helpers in
    `models.py`/`tokens.py`) rather than relying on DB-level tz-awareness.
* `src/provena/auth/redirects.py` — `is_allowed_redirect_uri`/
  `append_token_to_redirect`, implementing the allowlist described above.
  Pattern syntax: `scheme://host:port` (exact), `scheme://host` (default
  port), `scheme://host:*` (any port, for the VS Code loopback case).
* `src/provena/auth/backends/` — `base.py` (`AuthBackend` ABC,
  `ExternalIdentity` dataclass), `google.py` (Authlib-based
  `GoogleOAuthBackend`), `registry.py` (`get_active_backend`/`get_backend`,
  reading `auth_config.active_backend`).
* `src/provena/auth/dependencies.py` — `get_current_token`/`get_current_user`
  FastAPI dependencies, reading `Authorization: Bearer <token>` and raising
  `401 {"detail": "reauth_required"}` on anything invalid/missing/expired.
* `src/provena/api/auth/auth.py` — the actual endpoints, auto-registered by
  `main.py`'s router discovery:
  * `GET /auth/login?client_redirect_uri=<uri>&client_type=cli|web&state=<opaque>`
    (`client_type` defaults to `web`; `state` is optional and is the client's
    own CSRF nonce, not the server's -- see "Client-side CSRF (`state`)"
    above)
  * `GET /auth/google/callback` (internal, provider-registered, hidden from
    the OpenAPI schema)
  * `POST /auth/logout` (requires a valid bearer token; revokes only that
    token)
* `src/provena/auth_config.example.yaml` — the checked-in template; real
  `auth_config.yaml` is gitignored like the other `*config.yaml` files.
* `main.py` now adds Starlette's `SessionMiddleware` (keyed by
  `auth_config.session_secret_key`), which backs both Authlib's own OAuth
  state/CSRF handling and our own stashing of `client_redirect_uri`/
  `client_type` across the redirect to Google and back.
* `src/provena/auth/config.py` — `RoleConfig`/`InstructorRoleConfig`/
  `StudentRoleConfig`/`RolesConfig` pydantic models for `auth_config.yaml`'s
  `roles` section (now a required field). Validates `whitelist` requires a
  non-empty `emails` list and `pattern` requires a `pattern` string; logs a
  startup warning if `roles.instructor.type` is `open`, and another if
  `roles.student.submit_api_keys` is empty while `roles.student.type` isn't
  `open` (meaning `/submit` would be uncallable by the autograder).
* `src/provena/auth/roles.py` — `matches_role` (whitelist/pattern/open
  matching, case-insensitive) plus the three dependencies:
  `require_instructor_role`, `require_student_role` (instructor is a
  superset, so an instructor credential satisfies this too), and
  `require_submit_permission` (used only on `/submit`; deliberately does
  *not* accept a plain student OAuth login -- only an instructor credential,
  `roles.student.submit_api_keys`, or the student role being `open`). `401
  {"detail": "reauth_required"}` when no valid credential is presented at
  all; `403 {"detail": "insufficient_role"}` when there's a valid identity
  that just doesn't have the role.
* Wired in: `src/provena/api/read/common.py`'s old placeholder
  `require_api_key`/`X-API-Key`-only check is gone, replaced by
  `require_instructor_role` (imported from `provena.auth.roles`); all four
  `read/*.py` routers updated to match. `src/provena/api/logging/logging.py`:
  `/events` and `/get_event_count` now take `dependencies=[Depends(require_student_role)]`,
  `/submit` takes `dependencies=[Depends(require_submit_permission)]`.
* `src/provena/auth_config.example.yaml` and the local dev `auth_config.yaml`
  both updated with a `roles` section. The old `testing_api_keys` field on
  `write_config.yaml`/`PS2APIConfig` (part of the `toolbox` submodule) is no
  longer read anywhere in `provena`; it's harmless to leave in an existing
  `write_config.yaml` but can be removed.
* Verified with two standalone scripts exercising the auth/token/redirect and
  role-matching/dependency logic against throwaway SQLite DBs (not
  committed) -- all pass, including the 401-vs-403 distinction, the
  instructor-is-a-superset-of-student behavior, `require_submit_permission`
  correctly rejecting a plain student token, and both startup warnings
  firing. Full end-to-end exercise of the FastAPI endpoints (`TestClient`,
  real Google exchange) wasn't possible in this pass since the local dev
  `write_config.yaml`/`read_config.yaml` point at a MySQL instance that isn't
  running in this environment -- a pre-existing local-environment gap
  unrelated to this change.

Not built yet / explicitly deferred:

* **Clients**: neither the VS Code extension's loopback-server login flow nor
  the web app's login button/callback page exist — this pass is server-only.
* **Whitelist backend**: only Google is implemented; the whitelist mechanism
  as an *authentication* backend (an alternative to OAuth, not the
  whitelist *role type* above, which is built) is still just a design note.
* **Real Google OAuth credentials**: `auth_config.yaml` needs an actual Cloud
  Console project/client id/secret before login can work end-to-end; nothing
  here creates or validates those.
* **SubjectID ↔ identity enforcement**: role checks gate whether a request is
  allowed at all, not whether the claimed `SubjectID` in the request body
  matches the authenticated user's identity -- see "Non-goals" above.
* **Migrations**: schema changes currently rely on `create_all` (fine while
  there are no rows yet); if `auth_users`/`auth_oauth_identities`/
  `auth_tokens` need to change shape after real data exists, that'll need an
  actual migration (e.g. Alembic), not just editing `models.py`.
