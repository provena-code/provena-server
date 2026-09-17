# provena-server

## Overview

This is a FastAPI that records and serves data collected from a vscode extension for the purposes of helping to verify student programming work and collect research data.

It builds on two submodules:
* [provena-core](https://github.com/thomaswp/provena-core) (`provena` folder): contains the core typescript logic for building a provenance history from the logs, annotating each character with its original source.
* * **Note**: This is not yet used directly. Currently the clients are responsible for building provenance histories. It may be used in the future.
* [ProgSnapToolkit](https://github.com/CSSPLICE/ProgSnapToolkit) (`toolbox` folder): contains the logic for logging data in the ProgSnap2 format (only write logic). It relies on a .yaml file to define the format and uses that to structure the database.

It also connects with two other client repos:
* [provena-vscode](https://github.com/thomaswp/provena-vscode): A vs-code client that writes a student's history to the server.
* [provena-client](https://github.com/thomaswp/provena-client): A web client for the instructor interface that can read data from the server to show student histories.

## Structure

* `api`: contains the core server endpoints
* * `logging`: logging endpoints, called by the provena-vscode extension
* * `read`: instructor-facing endpoints, called by the provena-client webapp.
* `bridge`: Code for running the `provena-core` typescript logic. Not current used on the server side.
* `read_config.yaml`: Configuration for reading from the database (SQLite or MySQL). See `toolbox/README.md` for details
* * **Note**: This should be created using `read_config.example.yaml` if it does not already exist.
* `write_config.yaml`: Configuration for creating writing to the database (SQLite or MySQL). See `toolbox/README.md` for details. The read and write configuration files should match (they exist separately because the Toolkit this is built on handles logging and reading/analytics separately).
* * **Note**: This should be created using `write_config.example.yaml` if it does not already exist.
* `progsnap2-provena.yaml`: A yaml definition of the ProgSnap2 logging format used by Provena, which differs somewhat from the original.
* `auth_config.yaml`: Configuration for authentication. See the Authentication section below, and `docs/plans/auth.md` for full design details.
* * **Note**: This should be created using `auth_config.example.yaml` if it does not already exist.
* `auth`: Authentication -- OAuth login backends, and the User/OAuthIdentity/Token database models (separate from the ProgSnap2 logging schema above).

## Authentication

The server (not the client) completes OAuth logins, since client secrets can't safely live in a VS Code extension or a web app. Exactly one auth backend is active per deployment (`active_backend` in `auth_config.yaml`); only Google is implemented so far, but the backend is pluggable so a different deployment could use a different provider without changing the client-facing endpoints (`/auth/login`, `/auth/logout`).

### `auth_config.yaml` fields

* `active_backend`: name of the single backend this deployment uses (currently only `"google"`).
* `session_secret_key`: signs the short-lived server-side session cookie used only *during* a login (OAuth state, plus the `client_redirect_uri`/`client_type` passed to `/auth/login`). This is unrelated to issued API tokens -- those are opaque, DB-backed, and never signed/encoded client-side. Generate a real value with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
* `redirect_allowlist`: which `client_redirect_uri` values `/auth/login` is allowed to send the browser back to once login completes (e.g. the VS Code extension's local loopback server, or the web app's own callback route). Entries are `scheme://host:port` (exact), `scheme://host` (default port), or `scheme://host:*` (any port -- needed for VS Code's loopback server, which binds an arbitrary local port each time). This allowlist is enforced entirely by our own server code and has nothing to do with Google's redirect URI settings (see below) -- it's what stops `/auth/login` from being an open redirect.
* `backends.google.client_id` / `client_secret`: the OAuth client credentials from the Google Cloud Console.
* `backends.google.redirect_uri`: this server's own callback URL, which must exactly match one of the "Authorized redirect URIs" registered for that client in the Cloud Console (see below).
* `token.cli_ttl_days` / `token.web_ttl_hours`: expiry policy for issued tokens, applied based on the `client_type` (`cli` or `web`) passed to `/auth/login`. `cli` tokens (VS Code) use a sliding expiry -- each authenticated use pushes `cli_ttl_days` back out, so an actively-used extension effectively never needs to re-login. `web` tokens (the instructor-facing web app) get a short, fixed expiry instead, per normal web session practice.

### Setting up the Google OAuth Client (Cloud Console)

Three separate Console settings are easy to conflate:

* **Authorized redirect URIs** (on the OAuth Client ID's credentials page) -- only ever needs **this server's own** callback URL, never anything client-side. Google redirects only to the server; the server then performs a *second*, separate redirect to whatever `client_redirect_uri` the login started with (the VS Code loopback server, or the web app's callback page) -- Google is never aware of that second hop or its allowlist. Add one exact entry per environment you run against, matching `backends.google.redirect_uri` in that environment's `auth_config.yaml`:
  * `http://localhost:8001/auth/google/callback` for local dev/testing.
  * The real public callback URL for production (depends on whatever reverse proxy/domain fronts the deployed server). No wildcards -- Google requires an exact match per entry.
* **Authorized JavaScript origins**: leave empty. This is only for client-side Google Identity Services JS (e.g. a "Sign in with Google" button rendered directly in browser JS), which this server doesn't use -- the whole OAuth exchange happens server-side via Authlib.
* **Authorized domains** (a different page: the OAuth *consent screen*, not the Client ID's redirect URIs): governs your app's consent-screen identity (homepage/privacy-policy links), not the redirect URI list, and doesn't accept `localhost`/IP entries -- Google separately exempts `localhost`/`127.0.0.1` redirect URIs from domain verification regardless of this list. For a small, single-class deployment, staying in "Testing" publishing status with specific test users added by email likely avoids needing full domain verification at all. Confirm the exact current requirements live in the Console, since Google's consent-screen policies shift over time.