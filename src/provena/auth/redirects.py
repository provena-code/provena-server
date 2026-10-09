from typing import Optional
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

"""
Validates and rewrites the client-supplied `client_redirect_uri` used to send
the browser back to the client (VS Code's loopback server, or a web app's own
callback route) once login completes.

Since this URI is attacker-controllable input that we redirect a real
just-issued token to, it must be checked against an allowlist -- otherwise
this becomes an open redirect that leaks tokens to arbitrary sites.

Allowlist pattern syntax (in auth_config.yaml's redirect_allowlist):
* "scheme://host:port" matches that exact origin.
* "scheme://host" matches that host on the scheme's default port, whether
  or not the URI spells it out (":443" for https, ":80" for http), and vice
  versa.
* "scheme://host:*" matches that host on any port (for the VS Code loopback
  server, which binds an arbitrary local port).
A URI with a malformed port (non-numeric or out of range) never matches.
"""


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _effective_port(parts: SplitResult) -> Optional[int]:
    """The explicit port, or the scheme's default. Raises ValueError for a
    malformed port (non-numeric or out of range)."""
    return parts.port if parts.port is not None else _DEFAULT_PORTS.get(parts.scheme)


def is_allowed_redirect_uri(uri: str, allowlist: list[str]) -> bool:
    candidate = urlsplit(uri)
    if candidate.scheme not in ("http", "https") or not candidate.hostname:
        return False
    try:
        candidate_port = _effective_port(candidate)
    except ValueError:
        return False

    for pattern in allowlist:
        if pattern.endswith(":*"):
            base = urlsplit(pattern[:-2])
            if base.scheme == candidate.scheme and base.hostname == candidate.hostname:
                return True
        else:
            base = urlsplit(pattern)
            try:
                base_port = _effective_port(base)
            except ValueError:
                continue  # a malformed allowlist entry matches nothing
            if (
                base.scheme == candidate.scheme
                and base.hostname == candidate.hostname
                and base_port == candidate_port
            ):
                return True
    return False


def append_query_params(uri: str, params: dict) -> str:
    scheme, netloc, path, query, fragment = urlsplit(uri)
    all_params = parse_qsl(query, keep_blank_values=True) + list(params.items())
    return urlunsplit((scheme, netloc, path, urlencode(all_params), fragment))


def append_fragment_params(uri: str, params: dict) -> str:
    """
    Like append_query_params, but adds `params` to the URL *fragment*
    instead of the query string. Used for the "web" client_type: a fragment
    is never sent to any server (it's stripped client-side before the
    request is made), so this keeps the token out of access logs/Referer
    headers for a real multi-hop web request. The page at `uri` reads it via
    `location.hash`. If `uri` already has a fragment (e.g. a hash-based SPA
    router path), `params` are appended after it with `&`.
    """
    scheme, netloc, path, query, fragment = urlsplit(uri)
    extra = urlencode(params)
    new_fragment = f"{fragment}&{extra}" if fragment else extra
    return urlunsplit((scheme, netloc, path, query, new_fragment))
