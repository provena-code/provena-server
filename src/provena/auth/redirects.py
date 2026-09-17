from urllib.parse import urlsplit, urlunsplit

"""
Validates and rewrites the client-supplied `client_redirect_uri` used to send
the browser back to the client (VS Code's loopback server, or a web app's own
callback route) once login completes.

Since this URI is attacker-controllable input that we redirect a real
just-issued token to, it must be checked against an allowlist -- otherwise
this becomes an open redirect that leaks tokens to arbitrary sites.

Allowlist pattern syntax (in auth_config.yaml's redirect_allowlist):
* "scheme://host:port" matches that exact origin.
* "scheme://host" matches that host on the scheme's default port.
* "scheme://host:*" matches that host on any port (for the VS Code loopback
  server, which binds an arbitrary local port).
"""


def is_allowed_redirect_uri(uri: str, allowlist: list[str]) -> bool:
    candidate = urlsplit(uri)
    if candidate.scheme not in ("http", "https") or not candidate.hostname:
        return False

    for pattern in allowlist:
        if pattern.endswith(":*"):
            base = urlsplit(pattern[:-2])
            if base.scheme == candidate.scheme and base.hostname == candidate.hostname:
                return True
        else:
            base = urlsplit(pattern)
            if (
                base.scheme == candidate.scheme
                and base.hostname == candidate.hostname
                and base.port == candidate.port
            ):
                return True
    return False


def append_token_to_redirect(uri: str, raw_token: str) -> str:
    scheme, netloc, path, query, fragment = urlsplit(uri)
    query = f"{query}&token={raw_token}" if query else f"token={raw_token}"
    return urlunsplit((scheme, netloc, path, query, fragment))
