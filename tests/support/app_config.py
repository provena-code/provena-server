"""
Writes the three app configs (write/read/auth) for a test database, and the
known credentials they contain. Doesn't import provena: these files have to
exist before provena.config.configs is first imported.
"""

import os

import yaml

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_EXAMPLE_CONFIG_DIR = os.path.join(_REPO_ROOT, "src", "provena", "config")

INSTRUCTOR_EMAIL = "prof@instructor.test"
STUDENT_EMAIL = "student1@student.test"
STUDENT_EMAIL_PATTERN = "*@student.test"
INSTRUCTOR_API_KEY = "test-instructor-key"
SUBMIT_API_KEY = "test-submit-key"
REDIRECT_ALLOWLIST = ["http://127.0.0.1:*", "https://webapp.test"]


def _load_example(name: str) -> dict:
    with open(os.path.join(_EXAMPLE_CONFIG_DIR, f"{name}.example.yaml"), "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def auth_config() -> dict:
    # Spelled out rather than copied from the example: tests depend on these
    # exact roles and keys.
    return {
        "active_backend": "google",
        "session_secret_key": "test-session-secret",
        "redirect_allowlist": REDIRECT_ALLOWLIST,
        "backends": {
            "google": {
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "redirect_uri": "http://testserver/auth/google/callback",
            },
        },
        "token": {"cli_ttl_days": 90, "web_ttl_hours": 12},
        "roles": {
            "instructor": {"type": "whitelist", "emails": [INSTRUCTOR_EMAIL], "api_keys": [INSTRUCTOR_API_KEY]},
            "student": {"type": "pattern", "pattern": STUDENT_EMAIL_PATTERN, "submit_api_keys": [SUBMIT_API_KEY]},
        },
    }


def write_app_configs(config_dir: str, database_url: str, root_path: str) -> None:
    """
    Writes write/read/auth_config.yaml into `config_dir`, all pointing at
    `database_url`. The write/read configs start from the committed examples
    (so spec metadata, indexed columns etc. match a real deployment) with
    only the database swapped.
    """
    write = _load_example("write_config")
    write["database_config"].update(sqlalchemy_url=database_url, root_path=root_path)

    read = _load_example("read_config")
    read.update(sqlalchemy_url=database_url, root_path=root_path)

    os.makedirs(config_dir, exist_ok=True)
    for name, data in [("write_config", write), ("read_config", read), ("auth_config", auth_config())]:
        with open(os.path.join(config_dir, f"{name}.yaml"), "w", encoding="utf-8") as file:
            yaml.safe_dump(data, file, sort_keys=False)
