"""Firebase web identity is separate from the hub's MCP and worker credentials."""

import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from .models import Error, Identifier, Input


class FirebaseConfig(Input):
    apiKey: str = Field(min_length=1, max_length=256)
    authDomain: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9.-]+$")
    projectId: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9-]+$")
    appId: str = Field(min_length=1, max_length=256)


def checked_url(value, *, origin=False):
    parsed = urlsplit(value)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if (
        not parsed.hostname
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and local))
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or re.search(r"[\s;'\"<>]", value)
        or (origin and parsed.path not in ("", "/"))
    ):
        raise ValueError("Use an HTTPS URL, or localhost HTTP for local testing")
    return value.rstrip("/")


class WebConfig(Input):
    firebase: FirebaseConfig
    users: dict[str, Identifier] = Field(default_factory=dict, max_length=1000)
    allowed_origins: list[str] = Field(default_factory=list, max_length=16)
    api_base_url: str = Field(default="", max_length=2048)

    @field_validator("users")
    @classmethod
    def subjects(cls, value):
        if any(not uid or len(uid) > 128 or re.search(r"[\x00-\x1f\x7f]", uid) for uid in value):
            raise ValueError("Invalid Firebase UID")
        if len(set(value.values())) != len(value):
            raise ValueError("Bind at most one Firebase account to each hub user")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def origins(cls, value):
        return [checked_url(origin, origin=True) for origin in value]

    @field_validator("api_base_url")
    @classmethod
    def api_url(cls, value):
        return checked_url(value) if value else ""

    def public(self):
        return {
            "enabled": True,
            "firebase": self.firebase.model_dump(),
            "api_base_url": self.api_base_url,
        }


def load_web_config():
    path = os.getenv("HUB_WEB_CONFIG")
    if not path:
        return None
    try:
        return WebConfig.model_validate(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        raise RuntimeError(
            "Invalid HUB_WEB_CONFIG; check the administrator configuration file"
        ) from None


class FirebaseVerifier:
    def __init__(self, config):
        if os.getenv("FIREBASE_AUTH_EMULATOR_HOST"):
            raise RuntimeError("Firebase Auth emulator is not allowed for hub web authentication")
        self.config = config
        self.app = None
        self.lock = threading.Lock()

    def __call__(self, token):
        try:
            import firebase_admin
            from firebase_admin import auth, credentials
            from firebase_admin.exceptions import FirebaseError
            from google.auth.exceptions import DefaultCredentialsError, GoogleAuthError
        except ImportError:
            raise Error("WEB_AUTH_UNAVAILABLE", "Install the hub web extra", 503) from None
        try:
            with self.lock:
                if self.app is None:
                    self.app = firebase_admin.initialize_app(
                        credentials.ApplicationDefault(),
                        {"projectId": self.config.firebase.projectId, "httpTimeout": 10},
                        name="cowork-web-" + str(id(self)),
                    )
            # Includes signature, project, expiry, disabled-account and revocation checks.
            return auth.verify_id_token(token, app=self.app, check_revoked=True)
        except (auth.CertificateFetchError, DefaultCredentialsError, GoogleAuthError):
            raise Error(
                "WEB_AUTH_UNAVAILABLE", "Web authentication is temporarily unavailable", 503
            ) from None
        except (
            auth.InvalidIdTokenError,
            auth.RevokedIdTokenError,
            auth.UserDisabledError,
            ValueError,
        ):
            raise Error("UNAUTHENTICATED", "Sign in again with Google", 401) from None
        except FirebaseError:
            raise Error(
                "WEB_AUTH_UNAVAILABLE", "Web authentication is temporarily unavailable", 503
            ) from None


class WebIdentity:
    def __init__(self, hub, config, verify=None):
        self.hub, self.config = hub, config
        self.verify = verify or (FirebaseVerifier(config) if config else None)

    def authenticate(self, token):
        if not self.config:
            raise Error("WEB_NOT_CONFIGURED", "Web login has not been configured", 503)
        if not token or len(token) > 16384:
            raise Error("UNAUTHENTICATED", "Google sign-in is required", 401)
        claims = self.verify(token)
        subject = claims.get("uid")
        firebase = claims.get("firebase")
        if (
            not isinstance(subject, str)
            or not subject
            or claims.get("sub") != subject
            or claims.get("aud") != self.config.firebase.projectId
            or claims.get("iss")
            != "https://securetoken.google.com/" + self.config.firebase.projectId
            or claims.get("email_verified") is not True
            or not isinstance(firebase, dict)
            or firebase.get("sign_in_provider") != "google.com"
        ):
            raise Error("UNAUTHENTICATED", "A verified Google sign-in is required", 401)
        user_id = self.config.users.get(subject)
        if not user_id:
            raise Error("WEB_ACCOUNT_NOT_LINKED", "Ask the administrator to link your account", 403)
        with self.hub.store.transaction(write=False) as db:
            row = db.execute(
                "SELECT id FROM principals WHERE id=? AND role='user' AND enabled=1", (user_id,)
            ).fetchone()
        if not row:
            raise Error("WEB_ACCOUNT_NOT_LINKED", "The linked hub user is unavailable", 403)
        return {"user_id": user_id}
