"""Personal-token page and authenticated web endpoints; no command execution surface."""

from pathlib import Path

from fastapi import Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from .connector_models import TokenCreate
from .onboarding import Enrollment, Onboarding
from .web_auth import WebIdentity


def security_headers(config=None):
    domain = "https://" + config.firebase.authDomain if config else ""
    api = config.api_base_url if config else ""
    return {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self' https://www.gstatic.com https://apis.google.com; "
            "style-src 'self'; img-src 'self' data:; "
            f"connect-src 'self' https://*.googleapis.com {domain} {api}; "
            f"frame-src {domain or 'https://accounts.google.com'}; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
        ),
    }


def mount_web(app, hub, config=None, verify=None, *, destinations=None):
    identity = WebIdentity(hub, config, verify)
    bearer = HTTPBearer(auto_error=False)

    def user(credential: HTTPAuthorizationCredentials | None = Depends(bearer)):
        return identity.authenticate(credential.credentials if credential else None)

    def google_user(credential: HTTPAuthorizationCredentials | None = Depends(bearer)):
        return identity.google_user(credential.credentials if credential else None)

    onboarding = Onboarding(hub, config, destinations)

    @app.get("/v1/web/onboarding")
    def onboarding_status(principal=Depends(google_user)):
        return onboarding.status(principal)

    @app.post("/v1/web/onboarding", status_code=201)
    def enroll(body: Enrollment, principal=Depends(google_user)):
        return onboarding.submit(principal, body)

    if config and config.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.allowed_origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def web_headers(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/web", "/v1/web")):
            response.headers.update(security_headers(config))
        return response

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/web/")

    @app.get("/web/config.json", include_in_schema=False)
    def public_config():
        return JSONResponse(config.public() if config else {"enabled": False})

    @app.get("/v1/web/me")
    def me(principal=Depends(user)):
        return principal

    @app.get("/v1/web/notifications")
    def notification_status(principal=Depends(user)):
        return hub.notification_status(principal["user_id"])

    @app.get("/v1/web/tokens")
    def tokens(
        limit: int = Query(100, ge=1, le=100),
        after: str = Query("", max_length=80),
        principal=Depends(user),
    ):
        return hub.management.list_tokens(principal["user_id"], limit, after)

    @app.post("/v1/web/tokens", status_code=201)
    def create_token(body: TokenCreate, principal=Depends(user)):
        return hub.management.create_token(principal["user_id"], body.name)

    @app.delete("/v1/web/tokens/{token_id}")
    def revoke_token(token_id: str, principal=Depends(user)):
        return hub.management.revoke_token(principal["user_id"], token_id)

    from .server_web_api import mount_servers

    mount_servers(app, hub, config, user, destinations=destinations, onboarding=onboarding)
    app.mount(
        "/web",
        StaticFiles(directory=Path(__file__).parent / "web/dist", html=True, check_dir=False),
        name="web",
    )
