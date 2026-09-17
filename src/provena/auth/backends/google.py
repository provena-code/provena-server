from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request, status
from starlette.responses import Response

from provena.auth.backends.base import AuthBackend, ExternalIdentity


class GoogleOAuthBackend(AuthBackend):
    name = "google"

    def __init__(self, client_id: str, client_secret: str):
        self._oauth = OAuth()
        self._oauth.register(
            name="google",
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    async def login(self, request: Request, callback_url: str) -> Response:
        return await self._oauth.google.authorize_redirect(request, callback_url)

    async def callback(self, request: Request) -> ExternalIdentity:
        try:
            token = await self._oauth.google.authorize_access_token(request)
        except Exception as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Google login failed: {e}")

        user_info = token.get("userinfo")
        if not user_info or not user_info.get("sub") or not user_info.get("email"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Google did not return a verified profile.")

        return ExternalIdentity(
            provider=self.name,
            subject=user_info["sub"],
            email=user_info["email"],
            name=user_info.get("name"),
        )
