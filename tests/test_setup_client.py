import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest
from test_local_runner import runtime as runtime

from cowork_hub.connector import status, stop
from cowork_hub.models import NodeCreate

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_standalone_installer_contains_only_client_files_and_runs_without_dependencies(tmp_path):
    archive = module("build_installer").build(ROOT, tmp_path / "cowork-setup.pyz")
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        assert "scripts/setup_client.py" in names
        assert "scripts/setup_autostart.py" in names
        assert "skills/cowork-jobs/SKILL.md" in names
        assert all(
            name.startswith(("src/", "scripts/", "skills/"))
            or name in ("__main__.py", "pyproject.toml", "requirements-client.txt")
            for name in names
        )
        assert not any(".local" in name or name.endswith((".token", ".pyc")) for name in names)
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(archive), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert (
        "--root" in result.stdout
        and "--token-file" in result.stdout
        and "--autostart-only" in result.stdout
        and "--update" in result.stdout
    )
    with pytest.raises(FileExistsError):
        module("build_installer").build(ROOT, archive)


def test_setup_registers_checks_and_resumes_without_jobs_or_extra_approval(
    runtime, tmp_path, monkeypatch, capsys
):
    hub, config_path, _ = runtime
    current = json.loads(config_path.read_text())
    setup = module("setup_client")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import install_connector

    # Reuse the test interpreter instead of downloading dependencies; actual connector and stdio MCP run.
    bin_path = tmp_path / "test-bin"
    bin_path.mkdir()
    python = bin_path / "python"
    python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o700)
    connector = bin_path / "cowork-connector"
    connector.write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' -m cowork_hub.connector "$@"\n'
    )
    connector.chmod(0o700)

    def install(prefix, source, **kwargs):
        prefix.mkdir(mode=0o700, exist_ok=True)
        install_connector.write_installation(
            prefix,
            {
                "source": str(source),
                "source_hash": "0" * 64,
                "complete": True,
            },
        )
        return {"bin": str(bin_path)}

    monkeypatch.setattr(install_connector, "install", install)
    args = Namespace(
        root=tmp_path / "new-client",
        hub_url=current["hub_url"],
        node="A",
        client="cli",
        token_file=Path(current["token_file"]),
        wheelhouse=None,
        no_autostart=True,
    )
    config = args.root / "state/connector.json"
    try:
        first = setup.setup(args, ROOT)
        assert first["installed"] and first["environment_status"] == "READY"
        assert first["discord_configured"] and status(config)["running"]
        old_pid = status(config)["pid"]
        old_config = config.read_bytes()
        old_runner = (args.root / "state/runner.json").read_bytes()
        old_token = (args.root / "state/user.token").read_bytes()
        changed = args.root / "source/src/cowork_hub/ssh_runner.py"
        changed.write_text("# previous client version\n")
        second = setup.setup(args, ROOT)
        assert second["environment_id"] == first["environment_id"]
        assert status(config)["pid"] != old_pid
        assert config.read_bytes() == old_config
        assert (args.root / "state/runner.json").read_bytes() == old_runner
        assert (args.root / "state/user.token").read_bytes() == old_token
        assert changed.read_bytes() == (ROOT / "src/cowork_hub/ssh_runner.py").read_bytes()
        assert not list(args.root.glob("source-backup-*"))
        assert not list(args.root.glob(".cowork-source-*"))
        update = Namespace(
            root=args.root, update=True, token_file=None, wheelhouse=None, no_autostart=True
        )
        setup.update_settings(update, ROOT)
        assert update.hub_url == current["hub_url"] and update.node == "A"
        with monkeypatch.context() as updating:
            updating.setattr(
                setup,
                "connect_client",
                lambda *a: pytest.fail("update must preserve MCP registrations"),
            )
            updating.setattr(
                setup.getpass,
                "getpass",
                lambda *a: pytest.fail("update must reuse the saved token"),
            )
            updated = setup.setup(update, ROOT)
        assert updated["environment_id"] == first["environment_id"]
        assert updated["mcp"] == "preserved"
        assert config.read_bytes() == old_config
        assert (args.root / "state/runner.json").read_bytes() == old_runner
        assert (args.root / "state/user.token").read_bytes() == old_token
        assert not list(args.root.glob(".cowork-source-*"))
        assert len(hub.management.list_connectors("tester")) == 1
        assert hub.list_jobs("tester") == []
        token = (args.root / "state/user.token").read_text().strip()
        assert token not in capsys.readouterr().out
        assert (args.root / "state/user.token").stat().st_mode & 0o077 == 0
        assert json.loads((args.root / "state/runner.json").read_text())["environment"][
            "workdir"
        ] == str(args.root)
    finally:
        if config.exists():
            stop(config)
            deadline = time.monotonic() + 5
            while status(config)["running"] and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not status(config)["running"]


def test_unapproved_server_fails_before_saving_token_or_installing(runtime, tmp_path):
    _, path, _ = runtime
    current = json.loads(path.read_text())
    setup = module("setup_client")
    args = Namespace(
        root=tmp_path / "no-permission",
        hub_url=current["hub_url"],
        node="B",
        client="cli",
        token_file=Path(current["token_file"]),
        wheelhouse=None,
    )
    with pytest.raises(setup.SetupError, match="사용 승인"):
        setup.setup(args, ROOT)
    assert not (args.root / "state/user.token").exists()
    assert not (args.root / "source").exists()


@pytest.mark.parametrize("probe_present", [False, True])
def test_gpu_registration_succeeds_without_working_nvidia_smi(
    runtime, tmp_path, monkeypatch, probe_present
):
    hub, config_path, _ = runtime
    current = json.loads(config_path.read_text())
    gpu_ids = ["GPU-first", "GPU-second", "GPU-third", "GPU-fourth"]
    hub.create_node(
        NodeCreate(
            id="B",
            cpus=1,
            memory_mib=256,
            gpus=[{"id": gpu_id, "model": "test", "memory_mib": 1024} for gpu_id in gpu_ids],
        )
    )
    hub.set_grants("tester", ["A", "B"])
    setup = module("setup_client")
    probe = tmp_path / "nvidia-smi"
    marker = tmp_path / "gpu-probe-called"
    if probe_present:
        probe.write_text(
            "#!/bin/sh\n: > " + shlex.quote(str(marker)) + "\n"
            "echo 'Failed to initialize NVML: Driver/library version mismatch' >&2\n"
            "exit 1\n"
        )
        probe.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    config = tmp_path / "gpu-client/connector.json"
    runner = config.with_name("runner.json")
    argv = [
        sys.executable,
        "-m",
        "cowork_hub.connector",
        "configure",
        "--config",
        config,
        "--hub-url",
        current["hub_url"],
        "--token-file",
        current["token_file"],
        "--node",
        "B",
        "--workdir",
        tmp_path,
    ]
    registered = setup.command(argv, "환경 등록", json_output=True)
    assert setup.command(argv, "환경 등록", json_output=True) == registered
    environment = next(
        e for e in hub.list_environments("tester") if e["id"] == registered["environment_id"]
    )
    assert set(environment["gpu_ids"]) == set(gpu_ids)
    assert json.loads(runner.read_text())["environment"]["gpu_ids"] == gpu_ids
    assert not marker.exists()
    assert environment["status"] == "READY"
    assert len(hub.list_environments("tester")) == 2
    assert len(hub.management.list_connectors("tester")) == 1
    assert hub.list_jobs("tester") == []


@pytest.mark.parametrize(
    "stderr",
    [
        "CONNECTOR_ERROR: private-diagnostic",
        "UNKNOWN_ERROR: private-diagnostic",
        "private-diagnostic mentions SSH_GPU_PROBE_FAILED",
        "SSH_GPU_PROBE_FAILED_EXTRA: private-diagnostic",
        "",
    ],
)
def test_command_failure_does_not_echo_unknown_output(monkeypatch, stderr):
    setup = module("setup_client")
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv,
            1,
            "SSH_GPU_PROBE_FAILED: private-stdout",
            stderr,
        ),
    )
    with pytest.raises(setup.SetupError) as caught:
        setup.command(["tool"], "환경 등록")
    message = str(caught.value)
    assert "환경 등록 실패" in message and "재시도" in message
    assert "private" not in message and "SSH_GPU_PROBE_FAILED" not in message


def test_token_validation_preserves_files_and_never_reports_secret(tmp_path):
    setup = module("setup_client")
    token = tmp_path / "token"
    token.write_text("secret-value-must-not-be-displayed!")
    token.chmod(0o600)
    with pytest.raises(setup.SetupError) as caught:
        setup.token_value(token)
    assert "secret-value" not in str(caught.value)
    linked = tmp_path / "link"
    linked.symlink_to(token)
    with pytest.raises(OSError):
        setup.token_value(linked)
    token.chmod(0o644)
    with pytest.raises(setup.SetupError, match="600"):
        setup.token_value(token)


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_client_registration_uses_argument_arrays_and_preserves_existing(
    tmp_path, monkeypatch, client
):
    setup = module("setup_client")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(setup.Path, "home", lambda: home)
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/test/" + name)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "get" in argv:
            return subprocess.CompletedProcess(
                argv, 1, "", "No MCP server found with name 'cowork'"
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(setup.subprocess, "run", run)
    root = tmp_path / "folder with ' spaces"
    skill = root / "source/skills/cowork-jobs"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes((ROOT / "skills/cowork-jobs/SKILL.md").read_bytes())
    result = setup.connect_client(root / "bin", root, "http://hub:8080", client)
    assert result == {"mcp": "installed", "skill": "installed"}
    add = next(c for c in calls if "add" in c)
    assert str(root / "bin/cowork-mcp") in add
    assert str(root / "state/user.token") in add
    assert ("-s" in add) == (client == "claude")
    destination = home / (".agents" if client == "codex" else ".claude") / "skills/cowork-jobs"
    assert destination.resolve() == skill
    calls.clear()
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "existing", ""),
    )
    assert setup.connect_client(root / "other-bin", root, "http://hub:8080", client) == {
        "mcp": "existing",
        "skill": "existing",
    }
    assert destination.resolve() == skill


@pytest.mark.parametrize(
    "base,client", [(".agents", "codex"), (".codex", "codex"), (".claude", "claude")]
)
@pytest.mark.parametrize(
    "kind", ["broken_link", "file", "empty_directory", "empty_skill", "invalid_utf8"]
)
def test_unusable_existing_skill_is_reported_and_preserved(
    tmp_path, monkeypatch, capsys, base, client, kind
):
    setup = module("setup_client")
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)
    target = tmp_path / base / "skills/cowork-jobs"
    target.parent.mkdir(parents=True)
    if kind == "broken_link":
        target.symlink_to(tmp_path / "missing-skill", target_is_directory=True)
    elif kind == "file":
        target.write_text("user file")
    else:
        target.mkdir()
        if kind == "empty_skill":
            (target / "SKILL.md").write_text("\n  ")
        elif kind == "invalid_utf8":
            (target / "SKILL.md").write_bytes(b"\xff")
    before = target.lstat()
    assert setup.link_skill(ROOT, client) == "unavailable"
    assert target.lstat() == before
    if kind == "file":
        assert target.read_text() == "user file"
    if base == ".codex":
        assert not (tmp_path / ".agents/skills/cowork-jobs").exists()
    output = capsys.readouterr().err
    assert str(target) in output and "자동 선택 준비 안 됨" in output


@pytest.mark.parametrize("broken_preferred", [False, True])
def test_valid_legacy_skill_is_used_without_duplicate_install(
    tmp_path, monkeypatch, capsys, broken_preferred
):
    setup = module("setup_client")
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)
    legacy = tmp_path / ".codex/skills/cowork-jobs"
    legacy.mkdir(parents=True)
    content = (ROOT / "skills/cowork-jobs/SKILL.md").read_bytes()
    (legacy / "SKILL.md").write_bytes(content)
    preferred = tmp_path / ".agents/skills/cowork-jobs"
    if broken_preferred:
        preferred.parent.mkdir(parents=True)
        preferred.symlink_to(tmp_path / "missing-skill", target_is_directory=True)
    assert setup.link_skill(ROOT, "codex") == "existing"
    assert (legacy / "SKILL.md").read_bytes() == content
    assert not preferred.exists()
    assert preferred.is_symlink() == broken_preferred
    assert "자동 선택 준비 안 됨" not in capsys.readouterr().err


def test_missing_packaged_skill_does_not_create_broken_link(tmp_path, monkeypatch):
    setup = module("setup_client")
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)
    with pytest.raises(setup.SetupError, match="SKILL.md"):
        setup.link_skill(tmp_path / "missing-source", "codex")
    assert not (tmp_path / ".agents/skills").exists()


def test_unreadable_skill_reports_warning_without_changing_permissions(
    tmp_path, monkeypatch, capsys
):
    setup = module("setup_client")
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)
    target = tmp_path / ".agents/skills/cowork-jobs"
    target.mkdir(parents=True)
    entry = target / "SKILL.md"
    entry.write_bytes((ROOT / "skills/cowork-jobs/SKILL.md").read_bytes())
    before = entry.stat()
    original_open = setup.Path.open

    def denied(path, *args, **kwargs):
        if path == entry:
            raise PermissionError("test permission error")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(setup.Path, "open", denied)
    assert setup.link_skill(ROOT, "codex") == "unavailable"
    assert entry.stat() == before
    assert str(target) in capsys.readouterr().err


@pytest.mark.parametrize("legacy_present", [False, True])
def test_inaccessible_skill_parent_warns_and_checks_legacy(
    tmp_path, monkeypatch, capsys, legacy_present
):
    setup = module("setup_client")
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)
    preferred = tmp_path / ".agents/skills/cowork-jobs"
    if legacy_present:
        legacy = tmp_path / ".codex/skills/cowork-jobs"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_bytes((ROOT / "skills/cowork-jobs/SKILL.md").read_bytes())
    original_exists = setup.Path.exists

    def denied(path):
        if path == preferred:
            raise PermissionError("test parent permission error")
        return original_exists(path)

    monkeypatch.setattr(setup.Path, "exists", denied)
    assert setup.link_skill(ROOT, "codex") == ("existing" if legacy_present else "unavailable")
    assert str(preferred) in capsys.readouterr().err
    assert not (tmp_path / ".agents").exists()


def test_ambiguous_client_error_does_not_replace_registration(tmp_path, monkeypatch):
    setup = module("setup_client")
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/test/codex")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "Configuration could not be read")

    monkeypatch.setattr(setup.subprocess, "run", run)
    with pytest.raises(setup.SetupError, match="확인할 수 없습니다"):
        setup.connect_client(tmp_path / "bin", tmp_path, "http://hub:8080", "codex")
    assert len(calls) == 1 and "get" in calls[0]


def test_source_conflict_preserves_existing_files(tmp_path):
    setup = module("setup_client")
    source = tmp_path / "source"
    source.mkdir()
    sentinel = source / "pyproject.toml"
    sentinel.write_text("keep my project")
    with pytest.raises(setup.SetupError, match="보존"):
        setup.copy_source(ROOT, source)
    assert sentinel.read_text() == "keep my project"


@pytest.mark.parametrize(
    "runner",
    [
        None,
        {
            "hub_url": "http://hub:8080",
            "token_file": "/another-install/user.token",
            "environment": {"node_id": "A"},
        },
    ],
)
def test_update_rejects_unfinished_or_foreign_configuration(tmp_path, monkeypatch, runner):
    setup = module("setup_client")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import install_connector

    root = tmp_path / "client"
    prefix = root / "client-0.1.0"
    prefix.mkdir(parents=True, mode=0o700)
    install_connector.write_installation(
        prefix, {"source": str(root / "source"), "source_hash": "0" * 64, "complete": True}
    )
    if runner:
        (root / "state").mkdir(mode=0o700)
        path = root / "state/runner.json"
        path.write_text(json.dumps(runner))
        path.chmod(0o600)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(setup.SetupError, match="기존 설치·환경 설정"):
        setup.update_settings(Namespace(root=root), ROOT)
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_source_update_rolls_back_if_publication_fails(tmp_path, monkeypatch):
    setup = module("setup_client")
    target = tmp_path / "source"
    setup.copy_source(ROOT, target)
    previous = target / "pyproject.toml"
    previous.write_text("previous version")
    rename = Path.rename

    def fail_publish(path, destination):
        if path.name == "source" and path.parent.name.startswith(".cowork-source-"):
            raise OSError("publication failed")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises(OSError, match="publication failed"):
        setup.copy_source(ROOT, target, replace=True)
    assert previous.read_text() == "previous version"


def test_source_is_preserved_if_rollback_also_fails(tmp_path, monkeypatch):
    setup = module("setup_client")
    target = tmp_path / "source"
    setup.copy_source(ROOT, target)
    (target / "pyproject.toml").write_text("previous version")
    rename = Path.rename

    def fail_publish_and_restore(path, destination):
        if path.parent.name.startswith(".cowork-source-"):
            raise OSError("rename failed")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_publish_and_restore)
    with pytest.raises(setup.SetupError, match="보존 위치"):
        setup.copy_source(ROOT, target, replace=True)
    previous = list(tmp_path.glob(".cowork-source-*/previous/pyproject.toml"))
    assert len(previous) == 1 and previous[0].read_text() == "previous version"


@pytest.mark.skipif(
    os.environ.get("COWORK_TEST_FULL_INSTALL") != "1",
    reason="Downloads locked client dependencies into a fresh test virtualenv",
)
def test_packaged_installer_performs_fresh_install(runtime, tmp_path):
    hub, old_config, _ = runtime
    previous = json.loads(old_config.read_text())
    archive = module("build_installer").build(ROOT, tmp_path / "installer.pyz")
    root = tmp_path / "full-install"
    config = root / "state/connector.json"
    argv = [
        sys.executable,
        str(archive),
        "--root",
        str(root),
        "--node",
        "A",
        "--client",
        "cli",
        "--hub-url",
        previous["hub_url"],
        "--token-file",
        previous["token_file"],
        "--no-autostart",
    ]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=240)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "READY" in result.stdout
        assert status(config)["running"]
        assert hub.list_jobs("tester") == []
        repeated = subprocess.run(
            [sys.executable, str(archive), "--root", str(root), "--update", "--no-autostart"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
        assert len(hub.management.list_connectors("tester")) == 1
    finally:
        if config.exists():
            stop(config)
            deadline = time.monotonic() + 5
            while status(config)["running"] and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not status(config)["running"]
