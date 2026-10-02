import base64
import importlib.util
import json
from pathlib import Path

import pytest


def module(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / filename
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize(
    "path",
    [
        "/healthz",
        "/v1/jobs",
        "/v1/web/../jobs",
        "/v1/web/%2fjobs",
        "/v1/web//jobs",
        "/v1/web/jobs\r\nX: x",
    ],
)
def test_forced_command_rejects_paths_before_connecting(path, monkeypatch):
    relay = module("ssh_web_relay", "scripts/ssh_web_relay.py")
    monkeypatch.setattr(
        relay.http.client, "HTTPConnection", lambda *a, **kw: pytest.fail("must not connect")
    )
    with pytest.raises(ValueError):
        relay.forward(
            {"method": "GET", "path": path, "authorization": "Bearer test.token"}, "fixed-hub", 8080
        )


def test_forced_command_only_forwards_fixed_target_and_selected_headers(monkeypatch):
    relay = module("ssh_web_relay", "scripts/ssh_web_relay.py")
    calls = []

    class Connection:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

        def request(self, *args, **kwargs):
            calls.append((args, kwargs))

        def getresponse(self):
            return self

        status = 403

        def getheader(self, *args):
            return "application/json"

        def read(self, size):
            return b'{"error":{"code":"FORBIDDEN"}}'

        def close(self):
            pass

    monkeypatch.setattr(relay.http.client, "HTTPConnection", Connection)
    result = relay.forward(
        {
            "method": "POST",
            "path": "/v1/web/admin/users/alice.lab/grants",
            "authorization": "Bearer test.token",
            "body": "e30=",
            "host": "attacker",
            "headers": {"X-Admin": "true"},
        },
        "fixed-hub",
        8080,
    )
    assert calls[0][0] == ("fixed-hub", 8080)
    assert "X-Admin" not in calls[1][1]["headers"]
    assert calls[1][1]["body"] == b"{}"
    assert result["status"] == 403
    assert json.loads(base64.b64decode(result["body"]))["error"]["code"] == "FORBIDDEN"


def test_relay_build_keeps_functions_and_credentials_out_of_public(tmp_path):
    tool = module("build_web", "scripts/build_web.py")
    config = {
        "firebase": {
            "apiKey": "public",
            "projectId": "example",
            "appId": "public",
            "authDomain": "example.firebaseapp.com",
        },
        "users": {"private-subject": "alice"},
        "admin_users": ["alice"],
    }
    source = tmp_path / "private.json"
    source.write_text(json.dumps(config))
    target = tmp_path / "release"
    tool.build(source, target, relay=True)
    settings = json.loads((target / "firebase.json").read_text())
    assert settings["hosting"]["public"] == "public"
    assert settings["hosting"]["rewrites"][0]["source"] == "/v1/web/**"
    assert (target / "functions/index.js").is_file()
    public = json.loads((target / "public/config.json").read_text())
    assert public["api_base_url"] == ""
    assert "users" not in public and "admin_users" not in public
    assert not (target / "public/functions").exists()
    assert all(
        b"private-subject" not in p.read_bytes()
        for p in (target / "public").rglob("*")
        if p.is_file()
    )
