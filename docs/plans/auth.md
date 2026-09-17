# Auth: design notes

Status: server-side Google OAuth + token issuance implemented. See
"Implementation status" at the bottom for what exists vs. what's still open.

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

* `GET /auth/login?client_redirect_uri=<uri>` — starts a login using whichever
  backend is configured as active. If that backend is OAuth-based, the server
  redirects the browser to the provider (Google); if it's a non-OAuth backend
  like whitelist, this would instead need to serve/accept a simple form (not
  built yet — deferred, per the web app not needing provider UI today).
  `client_redirect_uri` says where to send the browser once login completes —
  see "Redirecting back to the client" below.
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
   `client_redirect_uri` from step 1 with the token attached (query param or
   fragment).
4. Client stores that token and sends it on future requests (likely
   `Authorization: Bearer <token>`), replacing/extending today's placeholder
   `X-API-Key` check in `src/provena/api/read/common.py`.

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

This effort is **authentication only**: getting a user logged in with a
verified identity (User/OAuthIdentity/Token tables + a Google OAuth backend,
plus the whitelist-as-alternative-login-method backend below). Authorization —
roles, permissions, what an authenticated user is allowed to do (e.g.
instructor access to `read/` endpoints, mapping a user to `CourseID`s they
manage) — is explicitly out of scope for now and will be designed/built as a
separate follow-up once login itself works. The `User` model should be
designed so a role/permissions concept can be added later without reshaping
it, but we're not building that layer yet.

## Whitelist: two distinct mechanisms

"Whitelist" covers two separate concerns, both in scope, kept as independent
pieces in the modular design:

1. **Whitelist as an authentication backend** — a pluggable login method in
   its own right (e.g. for local/dev use, or environments where a full Google
   OAuth round trip isn't wanted): a submitted email/identifier is checked
   against an allowed list and, if present, treated the same as a verified
   OAuth identity for the purposes of issuing a token.
2. **Whitelist as authorization** (restricting what an OAuth-authenticated
   user can do, e.g. instructor access) — this is part of the out-of-scope
   authorization/roles layer above, not built now, but the plan should avoid
   modeling authentication in a way that would make adding it later awkward.

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
  * `GET /auth/login?client_redirect_uri=<uri>&client_type=cli|web` (default
    `web`)
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
* Verified with a standalone script exercising the models/tokens/redirects
  logic against a throwaway SQLite DB (not committed) — issuance, resolution,
  sliding expiry, hard expiry, revocation, redirect allowlist matching, and
  `AuthConfig.from_yaml` against the example file all pass. Full end-to-end
  exercise of the FastAPI endpoints (`TestClient`, real Google exchange)
  wasn't possible in this pass since the local dev `write_config.yaml`/
  `read_config.yaml` point at a MySQL instance that isn't running in this
  environment — that's a pre-existing local-environment gap unrelated to this
  change, not something introduced by it.

Not built yet / explicitly deferred:

* **Clients**: neither the VS Code extension's loopback-server login flow nor
  the web app's login button/callback page exist — this pass is server-only.
* **Whitelist backend**: only Google is implemented; the whitelist mechanism
  (both as an alternative login backend and, later, as an authorization
  layer) is still just a design note above.
* **Authorization/roles**: out of scope per the earlier decision — `/read/`
  endpoints still use the placeholder `X-API-Key` check in
  `src/provena/api/read/common.py`, not the new token system. Wiring real
  users into those endpoints (and deciding what "instructor access" means) is
  a separate follow-up.
* **Real Google OAuth credentials**: `auth_config.yaml` needs an actual Cloud
  Console project/client id/secret before login can work end-to-end; nothing
  here creates or validates those.
* **Migrations**: schema changes currently rely on `create_all` (fine while
  there are no rows yet); if `auth_users`/`auth_oauth_identities`/
  `auth_tokens` need to change shape after real data exists, that'll need an
  actual migration (e.g. Alembic), not just editing `models.py`.
