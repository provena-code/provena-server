from provena.auth.backends.base import AuthBackend
from provena.auth.backends.google import GoogleOAuthBackend
from provena.configs import auth_config

_backends: dict[str, AuthBackend] = {}


def _build_backend(name: str) -> AuthBackend:
    if name == "google":
        cfg = auth_config.backends.google
        if cfg is None:
            raise ValueError("active_backend is 'google' but no backends.google section was found in auth_config.yaml")
        return GoogleOAuthBackend(client_id=cfg.client_id, client_secret=cfg.client_secret)
    raise ValueError(f"Unknown auth backend: {name!r}")


def get_backend(name: str) -> AuthBackend:
    if name not in _backends:
        _backends[name] = _build_backend(name)
    return _backends[name]


def get_active_backend() -> AuthBackend:
    return get_backend(auth_config.active_backend)
