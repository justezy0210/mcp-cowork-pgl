import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cowork_hub.api import create_app
from cowork_hub.models import Error
from cowork_hub.web_auth import FirebaseVerifier, WebConfig, WebIdentity, load_web_config


def settings(**changes):
    return WebConfig.model_validate(
        {
            "firebase": {
                "apiKey": "public-web-key",
                "authDomain": "lab-test.firebaseapp.com",
                "projectId": "lab-test",
                "appId": "public-app-id",
            },
            "users": {"google-alice": "alice", "google-bob": "bob", "google-admin": "admin"},
            "allowed_origins": ["https://lab-test.web.app"],
            **changes,
        }
    )


def claims(subject="google-alice", **changes):
    return {
        "uid": subject,
        "sub": subject,
        "aud": "lab-test",
        "iss": "https://securetoken.google.com/lab-test",
        "email_verified": True,
        "firebase": {"sign_in_provider": "google.com"},
        **changes,
    }


def verify(value):
    if not value.startswith("google-"):
        raise Error("UNAUTHENTICATED", "Invalid test credential", 401)
    return claims(value)


def auth(value="google-alice"):
    return {"Authorization": "Bearer " + value}


def test_first_web_token_without_existing_mcp_token_and_owner_isolation(rig):
    job = rig.submit(cpus=1)
    app = create_app(rig.hub, background=False, web_config=settings(), web_verify=verify)
    with TestClient(app) as client:
        assert client.get("/v1/web/me", headers=auth()).json() == {"user_id": "alice"}
        response = client.post("/v1/web/tokens", headers=auth(), json={"name": "226-Codex"})
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        issued = response.json()
        assert rig.hub.authenticate(issued["token"])["id"] == "alice"
        listing = client.get("/v1/web/tokens", headers=auth())
        assert len(listing.json()) == 1
        assert "token_hash" not in listing.text and issued["token"] not in listing.text
        assert client.get("/v1/web/tokens", headers=auth("google-bob")).json() == []
        assert (
            client.delete("/v1/web/tokens/" + issued["id"], headers=auth("google-bob")).status_code
            == 404
        )
        assert (
            client.post(
                "/v1/web/tokens", headers=auth(), json={"name": "x", "user_id": "bob"}
            ).status_code
            == 422
        )
        assert client.delete("/v1/web/tokens/" + issued["id"], headers=auth()).json()["revoked"]
        assert client.get("/v1/identity", headers=auth(issued["token"])).status_code == 401
        assert client.get("/v1/identity", headers=auth(rig.alice)).status_code == 200
        assert rig.state(job) == "DISPATCHING"


def test_web_credentials_do_not_grant_mcp_admin_or_worker_access(rig):
    app = create_app(rig.hub, background=False, web_config=settings(), web_verify=verify)
    with TestClient(app) as client:
        for value in (rig.alice, rig.admin, rig.worker):
            assert client.get("/v1/web/tokens", headers=auth(value)).status_code == 401
        assert client.get("/v1/web/tokens").status_code == 401
        assert client.get("/v1/web/me", headers=auth("google-stranger")).status_code == 403
        assert client.get("/v1/web/me", headers=auth("google-admin")).status_code == 403
        assert client.get("/v1/tokens", headers=auth()).status_code == 401
        assert client.get("/v1/jobs", headers=auth()).status_code == 401
        assert (
            client.post("/v1/admin/users", headers=auth(), json={"id": "attacker"}).status_code
            == 401
        )
        with rig.hub.store.transaction() as db:
            db.execute("UPDATE principals SET enabled=0 WHERE id='alice'")
        assert client.post("/v1/web/tokens", headers=auth(), json={"name": "x"}).status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "other-project"},
        {"iss": "https://securetoken.google.com/other-project"},
        {"email_verified": False},
        {"email_verified": "true"},
        {"firebase": {"sign_in_provider": "password"}},
        {"firebase": None},
        {"sub": "google-bob"},
    ],
)
def test_rejects_inappropriate_identity_claims(rig, changes):
    identity = WebIdentity(rig.hub, settings(), verify=lambda _: claims(**changes))
    with pytest.raises(Error) as caught:
        identity.authenticate("signed-but-inappropriate")
    assert caught.value.status == 401


def test_disabled_web_static_files_and_no_private_config_exposure(rig):
    with TestClient(create_app(rig.hub, background=False)) as client:
        page = client.get("/")
        assert page.status_code == 200 and "개인 토큰" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["cache-control"] == "no-store"
        assert client.get("/web/app.js").status_code == 200
        assert client.get("/web/config.json").json() == {"enabled": False}
        assert (
            client.post("/v1/web/tokens", json={"name": "x"}, headers=auth(rig.alice)).status_code
            == 503
        )
        assert rig.hub.management.list_tokens("alice") == []
    with TestClient(
        create_app(rig.hub, background=False, web_config=settings(), web_verify=verify)
    ) as client:
        response = client.get("/web/config.json")
        assert response.json()["firebase"]["projectId"] == "lab-test"
        assert "users" not in response.json()
        assert "google-alice" not in response.text
        assert "allowed_origins" not in response.json()


def test_cors_is_limited_to_configured_origins_and_bearer_auth(rig):
    app = create_app(rig.hub, background=False, web_config=settings(), web_verify=verify)
    with TestClient(app) as client:
        allowed = client.options(
            "/v1/web/tokens",
            headers={
                "Origin": "https://lab-test.web.app",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert allowed.headers["access-control-allow-origin"] == "https://lab-test.web.app"
        assert "access-control-allow-credentials" not in allowed.headers
        denied = client.options(
            "/v1/web/tokens",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert denied.status_code == 400
        assert "access-control-allow-origin" not in denied.headers


def test_sdk_checks_revocation_and_redacts_errors(monkeypatch):
    firebase_admin = pytest.importorskip("firebase_admin")
    from firebase_admin import auth as firebase_auth

    config = settings()
    app = object()
    monkeypatch.setattr(firebase_admin, "initialize_app", lambda *args, **kwargs: app)
    calls = []

    def verified(token, *, app, check_revoked):
        calls.append((app, check_revoked))
        if token == "invalid-secret-value":
            raise firebase_auth.InvalidIdTokenError("invalid-secret-value")
        return claims()

    monkeypatch.setattr(firebase_auth, "verify_id_token", verified)
    verifier = FirebaseVerifier(config)
    assert verifier("valid")["uid"] == "google-alice"
    assert calls == [(app, True)]
    with pytest.raises(Error) as caught:
        verifier("invalid-secret-value")
    assert caught.value.status == 401
    assert "invalid-secret-value" not in str(caught.value)


def test_emulator_and_insecure_configuration_are_not_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")
    with pytest.raises(RuntimeError, match="emulator"):
        FirebaseVerifier(settings())
    for changes in (
        {"allowed_origins": ["*"]},
        {"allowed_origins": ["http://192.168.10.41:8080"]},
        {"api_base_url": "https://hub.example;unsafe"},
        {"users": {"first": "alice", "second": "alice"}},
    ):
        with pytest.raises(ValueError):
            settings(**changes)
    path = tmp_path / "web.json"
    path.write_text(json.dumps({"secret": "must-not-appear"}))
    monkeypatch.setenv("HUB_WEB_CONFIG", str(path))
    with pytest.raises(RuntimeError) as caught:
        load_web_config()
    assert "must-not-appear" not in str(caught.value)


def test_hosting_export_excludes_account_mapping_and_preserves_existing_output(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/build_web.py"
    spec = importlib.util.spec_from_file_location("build_web", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / "private.json"
    config.write_text(settings(api_base_url="https://hub.example.org").model_dump_json())
    output = tmp_path / "hosting"
    assert module.build(config, output)["published"] is False
    public = json.loads((output / "config.json").read_text())
    assert public["enabled"] and "users" not in public
    assert "google-alice" not in "".join(p.read_text() for p in output.iterdir())
    hosting = json.loads((output / "firebase.json").read_text())["hosting"]
    assert hosting["public"] == "."
    assert any(
        item["key"] == "Cache-Control" and item["value"] == "no-store"
        for item in hosting["headers"][0]["headers"]
    )
    with pytest.raises(FileExistsError):
        module.build(config, output)
