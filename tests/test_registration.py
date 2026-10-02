import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cowork_hub.models import NodeCreate, UserCreate
from cowork_hub.service import Hub
from cowork_hub.store import Store

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "register_cluster", PROJECT / "scripts/register_cluster.py"
)
registration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registration)


@pytest.fixture
def manifest():
    return json.loads((PROJECT / "config/cluster-registration.json").read_text())


@pytest.fixture
def initialized(tmp_path):
    hub = Hub(Store(tmp_path / "hub.sqlite3"))
    token = hub.bootstrap()
    path = tmp_path / "admin.token"
    path.write_text(token + "\n")
    path.chmod(0o600)
    return hub, tmp_path


def test_registers_approved_nodes_with_private_credentials_and_no_execution(manifest, initialized):
    hub, directory = initialized
    result = registration.register_inside_hub(manifest, directory)
    assert result["status"] == "registered"
    assert result["warnings"] == []
    assert not result["containers_verified_by_this_command"]
    assert not result["workers_started_by_this_command"]
    assert {n["id"]: n["gpu_count"] for n in result["nodes"]} == {
        "229": 4,
        "228": 1,
        "226": 0,
        "224": 4,
        "227": 4,
    }
    for node in manifest["nodes"]:
        with hub.store.transaction(write=False) as db:
            saved = db.execute("SELECT * FROM nodes WHERE id=?", (node["id"],)).fetchone()
        assert saved["cpus"] == node["observed"]["logical_cpus_visible"]
        assert saved["memory_mib"] == node["observed"]["memory_total_kib_visible"] * 90 // (
            100 * 1024
        )
        assert saved["last_seen"] is None
    with hub.store.transaction(write=False) as db:
        assert db.execute("SELECT COUNT(*) FROM environments").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert {n["id"] for n in hub.cluster("ezy")} == {"229", "228", "226", "224", "227"}
    for item in result["credentials"]:
        path = Path(item["path"])
        token = path.read_text().strip()
        assert path.stat().st_mode & 0o777 == 0o600
        assert token not in json.dumps(result)
        assert hub.authenticate(token)["id"] == item["principal"]


def test_repeat_preserves_credentials_and_unrelated_grants(manifest, initialized):
    hub, directory = initialized
    first = registration.register_inside_hub(manifest, directory)
    tokens = {item["path"]: Path(item["path"]).read_bytes() for item in first["credentials"]}
    hub.create_node(NodeCreate(id="extra", cpus=1, memory_mib=1024))
    hub.set_grants("ezy", [*manifest["requested_allowed_nodes"], "extra"])
    again = registration.register_inside_hub(manifest, directory)
    assert all(n["status"] == "existing" for n in again["nodes"])
    assert all(Path(path).read_bytes() == value for path, value in tokens.items())
    assert "extra" in {n["id"] for n in hub.cluster("ezy")}


def test_conflicting_node_aborts_batch_without_overwriting(manifest, initialized):
    hub, directory = initialized
    hub.create_node(NodeCreate(id="229", cpus=1, memory_mib=1024))
    with pytest.raises(registration.RegistrationError, match="different budget"):
        registration.register_inside_hub(manifest, directory)
    with hub.store.transaction(write=False) as db:
        assert db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
        assert db.execute("SELECT cpus FROM nodes WHERE id='229'").fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM principals WHERE id='ezy'").fetchone()
    assert not (directory / "registration-credentials").exists()


def test_failed_credential_write_rolls_back_and_retry_reuses_saved_intent(
    manifest, initialized, monkeypatch
):
    hub, directory = initialized
    original = registration._credential
    count = 0

    def fail_second(path):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("Simulated storage failure")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(registration, "_credential", fail_second)
        with pytest.raises(OSError):
            registration.register_inside_hub(manifest, directory)
    with hub.store.transaction(write=False) as db:
        assert db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM principals").fetchone()[0] == 1
    token_path = directory / "registration-credentials/worker-229.token"
    saved = token_path.read_text()
    assert registration.register_inside_hub(manifest, directory)["status"] == "registered"
    assert token_path.read_text() == saved


def test_registration_requires_a_valid_admin(manifest, initialized):
    hub, directory = initialized
    user_token = hub.create_user(UserCreate(id="normal-user"))["token"]
    (directory / "admin.token").write_text(user_token)
    with pytest.raises(registration.RegistrationError, match="not an administrator"):
        registration.register_inside_hub(manifest, directory)
    with hub.store.transaction(write=False) as db:
        assert db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0


def test_unconfirmed_budget_is_rejected_before_registration(manifest, initialized):
    _, directory = initialized
    manifest["budget_policy"]["source"] = "not-confirmed"
    with pytest.raises(registration.RegistrationError, match="confirmed"):
        registration.register_inside_hub(manifest, directory)


def test_stdin_entry_point_used_by_docker_returns_only_safe_report(manifest, initialized):
    _, directory = initialized
    source = (
        "import json\n_REGISTRATION_PAYLOAD = json.loads("
        + repr(json.dumps(manifest))
        + ")\n"
        + (PROJECT / "scripts/register_cluster.py").read_text()
    )
    process = subprocess.run(
        [sys.executable, "-", "--inside-hub"],
        input=source,
        text=True,
        env={**os.environ, "HUB_DATA_DIR": str(directory)},
        capture_output=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(process.stdout)
    assert report["status"] == "registered"
    for item in report["credentials"]:
        token = Path(item["path"]).read_text().strip()
        assert token not in process.stdout + process.stderr
