import json
import os
import signal
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

import pytest
from test_local_runner import runtime as runtime
from test_setup_client import ROOT, module

from cowork_hub.connector import configure, start, status


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for the isolated service")


@pytest.fixture
def supervisor(tmp_path):
    if not Path("/usr/bin/supervisord").is_file():
        pytest.skip("Requires Supervisor for the isolated restart integration test")
    auto = module("setup_autostart")
    conf = tmp_path / "services"
    conf.mkdir()
    main = tmp_path / "supervisord.conf"
    socket = tmp_path / "supervisor.sock"
    main.write_text(f"""[unix_http_server]
file={socket}
chmod=0700
[supervisord]
nodaemon=true
logfile={tmp_path / "supervisor.log"}
pidfile={tmp_path / "supervisor.pid"}
childlogdir={tmp_path}
minfds=64
minprocs=10
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://{socket}
[include]
files={conf}/*.conf
""")
    # An unrelated service must not restart during Cowork's installation or reinstallation.
    (conf / "sentinel.conf").write_text(
        '[program:sentinel]\ncommand=/usr/bin/python3 -c "import time;time.sleep(600)"\nstartsecs=0\n'
    )
    processes = []

    def boot():
        process = subprocess.Popen(
            ["/usr/bin/supervisord", "-n", "-c", str(main)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(process)
        wait_for(lambda: auto.control(main, "pid", "sentinel", required=False).isdigit())
        return process

    def shutdown(process):
        auto.control(main, "shutdown")
        process.wait(timeout=20)

    process = boot()
    try:
        yield auto, (main, conf), process, boot, shutdown
    finally:
        for process in processes:
            if process.poll() is None:
                shutdown(process)


def test_existing_supervisor_restarts_connector_and_preserves_other_services(
    supervisor, runtime, tmp_path
):
    auto, layout, manager, boot, shutdown = supervisor
    hub, runner, _ = runtime
    config = tmp_path / "connector 100% 'quoted.json"
    configure(config, runner_config=runner)
    original = start(config)["pid"]
    sentinel = auto.control(layout[0], "pid", "sentinel")
    installed = auto.install(sys.executable, config, layout=layout)
    assert installed["autostart_configured"]
    managed = status(config)["pid"]
    assert managed != original
    assert auto.control(layout[0], "pid", installed["service"]) == str(managed)
    assert auto.control(layout[0], "pid", "sentinel") == sentinel
    assert auto.install(sys.executable, config, layout=layout) == installed
    assert status(config)["pid"] == managed

    # Unexpected process death is recovered without any installer, shell or MCP command.
    os.kill(managed, signal.SIGKILL)

    def restarted():
        value = status(config)
        return value["running"] and value["pid"] != managed

    wait_for(restarted)
    before_boot = status(config)["pid"]
    shutdown(manager)
    assert not status(config)["running"]
    boot()
    wait_for(lambda: status(config)["running"])
    assert status(config)["pid"] != before_boot
    wait_for(lambda: hub.management.list_connectors("tester")[0]["online"])
    assert hub.list_jobs("tester") == []


def test_conflicting_service_file_is_not_changed_or_activated(supervisor, runtime, tmp_path):
    auto, layout, _, _, _ = supervisor
    _, runner, _ = runtime
    config = tmp_path / "connector.json"
    configure(config, runner_config=runner)
    name, _, _ = auto.specification(sys.executable, config)
    target = layout[1] / (name + ".conf")
    target.write_text("preserve existing configuration\n")
    target.chmod(0o600)
    with pytest.raises(auto.AutostartError, match="SERVICE_FILE_CONFLICT"):
        auto.install(sys.executable, config, layout=layout)
    assert target.read_text() == "preserve existing configuration\n"
    assert not status(config)["running"]


def test_installer_registers_automatic_start_by_default(supervisor, runtime, tmp_path, monkeypatch):
    auto, layout, _, _, _ = supervisor
    _, runner, _ = runtime
    previous = json.loads(runner.read_text())
    setup = module("setup_client")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import install_connector

    monkeypatch.setattr(
        install_connector, "install", lambda *a, **kw: {"bin": str(Path(sys.executable).parent)}
    )
    monkeypatch.setattr(
        setup,
        "enable_autostart",
        lambda python, config, source: auto.install(python, config, layout=layout),
    )
    args = Namespace(
        root=tmp_path / "installed",
        hub_url=previous["hub_url"],
        node="A",
        client="cli",
        token_file=Path(previous["token_file"]),
        wheelhouse=None,
        no_autostart=False,
    )
    installed = setup.setup(args, ROOT)
    assert installed["autostart_configured"] and installed["manager"] == "supervisor"
    config = args.root / "state/connector.json"
    before = status(config)["pid"]
    sentinel = auto.control(layout[0], "pid", "sentinel")
    repeated = setup.setup(args, ROOT)
    assert repeated["environment_id"] == installed["environment_id"]
    assert status(config)["pid"] != before
    assert auto.control(layout[0], "pid", "sentinel") == sentinel


def test_supervised_connector_waits_for_hub_during_startup(supervisor, runtime, tmp_path):
    auto, layout, _, _, _ = supervisor
    hub, runner, _ = runtime
    config = tmp_path / "connector.json"
    configure(config, runner_config=runner)
    saved = runner.read_text()
    offline = json.loads(saved)
    offline["hub_url"] = "http://127.0.0.1:1"
    runner.write_text(json.dumps(offline))
    installed = auto.install(sys.executable, config, layout=layout)
    pid = status(config)["pid"]
    assert installed["autostart_configured"]
    runner.write_text(saved)
    wait_for(lambda: hub.management.list_connectors("tester")[0]["online"], timeout=20)
    assert status(config)["pid"] == pid


def test_autostart_only_does_not_reinstall_or_read_a_token(tmp_path, monkeypatch):
    setup = module("setup_client")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    config = state / "connector.json"
    config.write_text("{}")
    config.chmod(0o600)
    python = tmp_path / "client-0.1.0/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    calls = []

    def register(*args):
        calls.append(args)
        return {"autostart_configured": True, "service": "synthetic"}

    monkeypatch.setattr(setup, "enable_autostart", register)
    assert setup.autostart_only(tmp_path, ROOT)["autostart_configured"]
    assert calls == [(python, config, ROOT)]
    assert not (state / "user.token").exists()


def test_sudo_denial_is_reported_without_launching_privileged_helper(tmp_path, monkeypatch):
    setup = module("setup_client")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import setup_autostart

    monkeypatch.setattr(
        setup_autostart, "supervisor_layout", lambda: (tmp_path / "supervisord.conf", tmp_path)
    )
    monkeypatch.setattr(setup.os, "geteuid", lambda: 1234)
    monkeypatch.setattr(setup.os, "access", lambda *a: False)
    monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/sudo")
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: False)
    calls = []

    def denied(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(setup.subprocess, "run", denied)
    result = setup.enable_autostart(tmp_path / "python", tmp_path / "connector.json", ROOT)
    assert not result["autostart_configured"]
    assert "sudo" in result["reason"] and result["retry"].startswith("sudo ")
    assert calls == [["/usr/bin/sudo", "-n", "-v"]]


def test_missing_supervisor_does_not_offer_sudo_retry_or_write_helper(
    tmp_path, monkeypatch, capsys
):
    setup = module("setup_client")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import setup_autostart

    def missing():
        raise setup_autostart.AutostartError("SUPERVISOR_NOT_CONFIGURED")

    monkeypatch.setattr(setup_autostart, "supervisor_layout", missing)
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("No sudo or service commands without Supervisor"),
    )
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    config = state / "connector.json"
    config.write_text("{}")
    config.chmod(0o600)
    python = tmp_path / "client-0.1.0/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    result = setup.autostart_only(tmp_path, ROOT)
    assert not result["autostart_configured"] and "retry" not in result
    assert not list(state.glob("autostart-*.py"))
    import shlex

    assert shlex.split(result["restart"]) == [
        str(python),
        "-m",
        "cowork_hub.connector",
        "start",
        "--config",
        str(config),
    ]
    setup.report_autostart(result)
    output = capsys.readouterr().out
    assert result["restart"] in output and "한 번만 등록할 명령:" not in output


def test_direct_helper_reports_missing_supervisor_without_touching_config(
    tmp_path, monkeypatch, capsys
):
    auto = module("setup_autostart")

    def missing():
        raise auto.AutostartError("SUPERVISOR_NOT_CONFIGURED")

    monkeypatch.setattr(auto, "supervisor_layout", missing)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "setup_autostart.py",
            "--python",
            "/missing/python",
            "--config",
            str(tmp_path / "connector.json"),
        ],
    )
    assert auto.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "SUPERVISOR_NOT_CONFIGURED"
    assert not result["autostart_configured"] and result["message"]
    assert not list(tmp_path.iterdir())
