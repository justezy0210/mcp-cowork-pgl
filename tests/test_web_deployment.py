import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def setup_tool(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/enable_web.py"
    spec = importlib.util.spec_from_file_location("enable_web", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "PROJECT", tmp_path)
    local = tmp_path / ".local"
    local.mkdir(mode=0o700)
    values = {
        "compose.local-runner.yaml": {
            "services": {
                "hub": {
                    "env_file": ["/private/discord.env"],
                    "environment": {"HUB_DISCORD_DIRECT_USER": "alice"},
                    "ports": ["127.0.0.1:8080:8080"],
                }
            }
        },
        "web.firebase.pending.json": {"users": {}},
        "firebase-admin.mcp-cowork-pgl.json": {"private_key": "synthetic-test-key"},
    }
    for name, value in values.items():
        p = local / name
        p.write_text(json.dumps(value))
        p.chmod(0o600)
    monkeypatch.setattr(sys, "argv", ["enable_web.py", "--apply"])
    previous_umask = os.umask(0o077)
    os.umask(previous_umask)
    yield module, local, values
    os.umask(previous_umask)


def test_refuses_empty_docker_host_before_reading_credentials(setup_tool, monkeypatch):
    tool, local, values = setup_tool
    calls = []

    def invoke(args, **kwargs):
        calls.append(args)
        return ""

    monkeypatch.setattr(tool, "invoke", invoke)
    with pytest.raises(SystemExit, match="1"):
        tool.main()
    assert len(calls) == 1
    assert "ps" in calls[0] and "up" not in calls[0]
    assert not (local / "backups").exists()
    assert (
        json.loads((local / "compose.local-runner.yaml").read_text())
        == values["compose.local-runner.yaml"]
    )


def test_dry_run_and_failed_backup_do_not_install_or_restart(setup_tool, monkeypatch, capsys):
    tool, local, values = setup_tool
    calls = []

    def invoke(args, **kwargs):
        calls.append(args)
        if "ps" in args:
            return "existing-hub-id"
        if args[-1] == tool.PREFLIGHT:
            return '{"preflight":"ok"}'
        if "pg_dump" in args:
            raise RuntimeError("synthetic-secret-diagnostic")
        pytest.fail("Unexpected mutation during preflight or failed backup")

    monkeypatch.setattr(tool, "invoke", invoke)
    monkeypatch.setattr(sys, "argv", ["enable_web.py"])
    tool.main()
    assert len(calls) == 2
    assert not (local / "backups").exists()
    monkeypatch.setattr(sys, "argv", ["enable_web.py", "--apply"])
    with pytest.raises(SystemExit):
        tool.main()
    assert not any(tool.INSTALL in call or "up" in call for call in calls)
    assert (
        json.loads((local / "compose.local-runner.yaml").read_text())
        == values["compose.local-runner.yaml"]
    )
    output = capsys.readouterr()
    assert "synthetic-secret" not in output.out + output.err
    assert "postgresql-backup" in output.err


def test_apply_backs_up_and_preserves_compose_settings(setup_tool, monkeypatch):
    tool, local, values = setup_tool
    calls = []

    def invoke(args, **kwargs):
        calls.append((args, kwargs))
        if "ps" in args:
            return "existing-hub-id"
        if "pg_dump" in args:
            kwargs["output"].write(b"synthetic-backup")
        return "ok"

    monkeypatch.setattr(tool, "invoke", invoke)
    tool.main()
    settings = json.loads((local / "compose.local-runner.yaml").read_text())
    hub = settings["services"]["hub"]
    assert hub["env_file"] == ["/private/discord.env"]
    assert hub["ports"] == ["127.0.0.1:8080:8080"]
    assert hub["environment"]["HUB_DISCORD_DIRECT_USER"] == "alice"
    assert hub["environment"]["HUB_WEB_CONFIG"] == tool.WEB_PATH
    assert hub["environment"]["GOOGLE_APPLICATION_CREDENTIALS"] == tool.KEY_PATH
    old = list((local / "backups").glob("compose-before-web-*.json"))
    assert len(old) == 1 and json.loads(old[0].read_text()) == values["compose.local-runner.yaml"]
    assert (local / "compose.local-runner.yaml").stat().st_mode & 0o777 == 0o600
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in (local / "backups").iterdir())
    restart = [args for args, _ in calls if "up" in args]
    assert len(restart) == 1 and restart[0][-1] == "hub"
    assert all(flag in restart[0] for flag in ["--no-deps", "--no-build", "never"])
    installation = next(kwargs for args, kwargs in calls if args[-1] == tool.INSTALL)
    assert installation["payload"]["credential"] == values["firebase-admin.mcp-cowork-pgl.json"]
    assert all("synthetic-test-key" not in str(args) for args, _ in calls)


def test_private_file_install_preserves_key_and_backs_up_configuration(
    setup_tool, tmp_path, monkeypatch
):
    tool, _, _ = setup_tool
    key = tmp_path / "key.json"
    config = tmp_path / "web.json"
    payload = {
        "credential": {"private_key": "synthetic-key"},
        "config": {"users": {}},
        "environment": {"GOOGLE_APPLICATION_CREDENTIALS": str(key), "HUB_WEB_CONFIG": str(config)},
    }

    def install():
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        exec(compile(tool.INSTALL, "<INSTALL>", "exec"), {})

    install()
    assert key.stat().st_mode & 0o777 == config.stat().st_mode & 0o777 == 0o600
    payload["config"]["users"] = {"firebase-alice": "alice"}
    install()
    backups = list(tmp_path.glob("web.json.before-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == {"users": {}}
    payload["credential"] = {"private_key": "different-synthetic-key"}
    with pytest.raises(AssertionError, match="Never replace"):
        install()
    assert json.loads(key.read_text())["private_key"] == "synthetic-key"
