import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def https_client(app):
    """The login session cookie is Secure (https_only), so the login flow
    needs an https base URL for the cookie to come back."""
    with TestClient(app, base_url="https://testserver") as client:
        yield client
