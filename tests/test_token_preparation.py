import importlib.util
import stat
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "prepare_mcp_token", Path(__file__).resolve().parents[1] / "scripts/prepare_mcp_token.py"
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def runner(raw=b"test-user-credential-1234567890\n", code=0):
    def run(command, **kwargs):
        assert command[:7] == ["docker", "compose", "exec", "-T", "hub", "python", "-c"]
        assert command[-1] == "ezy"
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(command, code, raw, b"DO_NOT_ECHO_STDERR")

    return run


def test_private_complete_write_and_idempotent_rerun(tmp_path, capsys):
    result = helper.prepare(tmp_path, "ezy", run=runner())
    assert result.read_bytes() == b"test-user-credential-1234567890\n"
    assert stat.S_IMODE(result.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.parent.stat().st_mode) == 0o700
    inode = result.stat().st_ino
    assert helper.prepare(tmp_path, "ezy", run=runner()) == result
    assert result.stat().st_ino == inode
    assert list(result.parent.glob(".prepare-*")) == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_existing_different_credential_is_preserved(tmp_path):
    result = helper.prepare(tmp_path, "ezy", run=runner())
    with pytest.raises(helper.PreparationError, match="preserved"):
        helper.prepare(tmp_path, "ezy", run=runner(b"different-test-credential-1234567890\n"))
    assert result.read_bytes() == b"test-user-credential-1234567890\n"
    assert list(result.parent.glob(".prepare-*")) == []


@pytest.mark.parametrize("raw,code", [(b"SENSITIVE_FAILURE_OUTPUT", 1), (b"", 0)])
def test_failed_export_leaves_no_token_and_redacts_output(tmp_path, raw, code):
    with pytest.raises(helper.PreparationError) as caught:
        helper.prepare(tmp_path, "ezy", run=runner(raw, code))
    assert "SENSITIVE" not in str(caught.value)
    assert "DO_NOT_ECHO" not in str(caught.value)
    assert list((tmp_path / ".local/credentials").iterdir()) == []


def test_shared_or_symlinked_directory_is_rejected(tmp_path):
    directory = tmp_path / ".local"
    directory.mkdir(mode=0o755)
    with pytest.raises(helper.PreparationError, match="0700"):
        helper.prepare(tmp_path, "ezy", run=runner())
    directory.rmdir()
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    directory.symlink_to(other)
    with pytest.raises(helper.PreparationError, match="0700"):
        helper.prepare(tmp_path, "ezy", run=runner())


def test_hub_side_reader_checks_user_role_and_enabled_state(rig, tmp_path):
    # Exercise the actual embedded reader with a fixture DB and a private test credential.
    root = tmp_path / "hub-data"
    root.mkdir()
    (root / "hub.sqlite3").symlink_to(rig.hub.store.path)
    credentials = root / "registration-credentials"
    credentials.mkdir()
    (credentials / "user-alice.token").write_text(rig.alice + "\n")
    import os
    import sys

    env = {**os.environ, "HUB_DATA_DIR": str(root)}
    result = subprocess.run(
        [sys.executable, "-c", helper.READ_USER_TOKEN, "alice"],
        env=env,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout.strip().decode() == rig.alice
    with rig.hub.store.transaction() as db:
        db.execute("UPDATE principals SET enabled=0 WHERE id='alice'")
    denied = subprocess.run(
        [sys.executable, "-c", helper.READ_USER_TOKEN, "alice"],
        env=env,
        capture_output=True,
        timeout=5,
    )
    assert denied.returncode != 0
    assert denied.stdout == b""
