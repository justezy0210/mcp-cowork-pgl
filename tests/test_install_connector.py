import json
import subprocess

import pytest
from test_setup_client import module


@pytest.fixture
def installation(tmp_path, monkeypatch):
    installer = module("install_connector")
    source = tmp_path / "source"
    package = source / "src/cowork_hub"
    package.mkdir(parents=True)
    (source / "pyproject.toml").write_text('[project]\nname="cowork-hub"\nversion="0.1.0"\n')
    (source / "requirements-client.txt").write_text("")
    (package / "connector.py").write_text("# initial version\n")
    prefix = tmp_path / "client"
    calls = []
    creations = []

    def create(builder, environment):
        creations.append(environment)
        binary = environment / "bin"
        binary.mkdir(parents=True)
        (binary / "python").write_text("managed interpreter")

    def run(argv, **kwargs):
        calls.append(argv)
        if "--force-reinstall" in argv:
            for name in ("cowork-connector", "cowork-mcp", "cowork-run"):
                (prefix / "venv/bin" / name).write_text("managed entry point")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(installer.venv.EnvBuilder, "create", create)
    monkeypatch.setattr(installer.subprocess, "run", run)
    return installer, prefix, source, calls, creations, run


def test_same_version_update_reuses_paths_and_unchanged_installation(installation):
    installer, prefix, source, calls, creations, _ = installation
    first = installer.install(prefix, source)
    original = json.loads((prefix / "installation.json").read_text())
    calls.clear()
    assert installer.install(prefix, source)["reused"]
    assert calls == []
    (source / "src/cowork_hub/connector.py").write_text("# fixed version\n")
    updated = installer.install(prefix, source)
    assert updated["bin"] == first["bin"] and not updated["reused"]
    assert len(creations) == 1
    assert any("--force-reinstall" in call for call in calls)
    saved = json.loads((prefix / "installation.json").read_text())
    assert saved["complete"] and saved["source_hash"] != original["source_hash"]
    assert (prefix / "installation.json").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("failure_stage", ["ensurepip", "--require-hashes", "--force-reinstall"])
def test_interrupted_installation_resumes_in_same_directory(
    installation, monkeypatch, failure_stage
):
    installer, prefix, source, _, creations, run = installation

    def fail_once(argv, **kwargs):
        if failure_stage in argv:
            raise subprocess.CalledProcessError(1, argv)
        return run(argv, **kwargs)

    monkeypatch.setattr(installer.subprocess, "run", fail_once)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(prefix, source)
    assert not json.loads((prefix / "installation.json").read_text())["complete"]
    marker = prefix / "preserve.txt"
    marker.write_text("keep")
    monkeypatch.setattr(installer.subprocess, "run", run)
    assert not installer.install(prefix, source)["reused"]
    assert json.loads((prefix / "installation.json").read_text())["complete"]
    assert marker.read_text() == "keep" and len(creations) == 1


def test_missing_entry_point_is_repaired(installation):
    installer, prefix, source, _, _, _ = installation
    installer.install(prefix, source)
    (prefix / "venv/bin/cowork-mcp").unlink()
    assert not installer.install(prefix, source)["reused"]
    assert (prefix / "venv/bin/cowork-mcp").is_file()


@pytest.mark.parametrize("kind", ["unknown", "symlink", "other-source"])
def test_unrecognized_installation_is_preserved(installation, tmp_path, kind):
    installer, prefix, source, calls, _, _ = installation
    prefix.mkdir(mode=0o700)
    marker = prefix / "installation.json"
    if kind == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_text("keep")
        marker.symlink_to(outside)
    elif kind == "other-source":
        marker.write_text(
            json.dumps({"source": "/another/source", "source_hash": "0" * 64, "complete": True})
        )
        marker.chmod(0o600)
    sentinel = prefix / "preserve.txt"
    sentinel.write_text("keep")
    with pytest.raises(RuntimeError, match="preserved"):
        installer.install(prefix, source)
    assert sentinel.read_text() == "keep" and calls == []
    if kind == "symlink":
        assert outside.read_text() == "keep"
