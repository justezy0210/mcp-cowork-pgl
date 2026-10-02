import fcntl
import json
import sqlite3
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import Rig

from cowork_hub.migration import TABLES, fingerprint, migrate
from cowork_hub.models import Error, UserCreate
from cowork_hub.postgres import PostgresStore
from cowork_hub.service import Hub
from cowork_hub.store import configured_store


def url_file(tmp_path, url):
    path = tmp_path / "postgres.url"
    path.write_text(url)
    path.chmod(0o600)
    return path


def test_import_preserves_every_row_credentials_sequence_and_queue(tmp_path, postgres_url):
    rig = Rig(tmp_path / "hub.sqlite3")
    first = rig.submit(gpu_count=1)
    queued = rig.submit(gpu_count=1)
    token = rig.hub.management.create_token("alice", "main")["token"]
    revoked = rig.hub.management.create_token("alice", "old")
    rig.hub.management.revoke_token("alice", revoked["id"])
    rig.event(first, "started")
    with rig.hub.store.transaction() as db:
        db.execute("UPDATE sqlite_sequence SET seq=900 WHERE name='notification_destinations'")
    report = migrate(tmp_path, url_file(tmp_path, postgres_url))
    assert report["verified"] and report["rows"]["jobs"] == 2
    assert Path(report["backup"]).stat().st_mode & 0o777 == 0o600
    target = PostgresStore(postgres_url)
    with (
        rig.hub.store.transaction(write=False) as source,
        target.transaction(write=False) as destination,
    ):
        for table in TABLES:
            assert fingerprint(source.execute(f'SELECT * FROM "{table}"')) == fingerprint(
                destination.execute(f'SELECT * FROM "{table}"')
            )
    hub = Hub(target, clock=lambda: rig.now)
    for credential in (rig.alice, rig.admin, rig.worker, token):
        assert hub.authenticate(credential) == rig.hub.authenticate(credential)
    with pytest.raises(Error):
        hub.authenticate(revoked["token"])
    assert hub.provision_destination("alice", "new", "123")["id"] == 901
    rig.hub = hub
    rig.event(first)
    assert rig.state(queued) == "DISPATCHING"
    assert token not in json.dumps(report)


def test_existing_target_is_preserved_and_import_is_not_repeated(tmp_path, postgres_url):
    Rig(tmp_path / "hub.sqlite3")
    config = url_file(tmp_path, postgres_url)
    migrate(tmp_path, config)
    store = PostgresStore(postgres_url)
    with store.transaction(write=False) as db:
        before = fingerprint(db.execute("SELECT * FROM principals"))
    with pytest.raises(RuntimeError, match="not empty"):
        migrate(tmp_path, config)
    with store.transaction(write=False) as db:
        assert fingerprint(db.execute("SELECT * FROM principals")) == before


def test_corrupt_reference_rolls_back_and_preserves_source(tmp_path, postgres_url):
    rig = Rig(tmp_path / "hub.sqlite3")
    with sqlite3.connect(rig.hub.store.path) as db:
        db.execute("INSERT INTO grants VALUES('missing','A')")
    with pytest.raises(RuntimeError, match="integrity"):
        migrate(tmp_path, url_file(tmp_path, postgres_url))
    with PostgresStore(postgres_url).transaction(write=False) as db:
        assert db.execute("SELECT COUNT(*) FROM principals").fetchone()[0] == 0


def test_import_error_rolls_back_all_target_tables(tmp_path, postgres_url):
    rig = Rig(tmp_path / "hub.sqlite3")
    # Valid SQLite text which PostgreSQL cannot store. Earlier table copies must roll back.
    with rig.hub.store.transaction() as db:
        db.execute("UPDATE environments SET name=?", ("name\x00invalid",))
    with pytest.raises(RuntimeError):
        migrate(tmp_path, url_file(tmp_path, postgres_url))
    with PostgresStore(postgres_url).transaction(write=False) as db:
        assert db.execute("SELECT COUNT(*) FROM principals").fetchone()[0] == 0
    with rig.hub.store.transaction(write=False) as db:
        assert db.execute("SELECT COUNT(*) FROM principals").fetchone()[0] == 4


def test_migration_refuses_running_sqlite_hub(tmp_path, postgres_url):
    Rig(tmp_path / "hub.sqlite3")
    with (tmp_path / "hub.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="Stop"):
            migrate(tmp_path, url_file(tmp_path, postgres_url))


def test_single_hub_lock_and_migration_exclusion(postgres_url):
    first, second = PostgresStore(postgres_url), PostgresStore(postgres_url)
    with first.exclusive_run():
        with pytest.raises(RuntimeError, match="Another hub"):
            with second.exclusive_run():
                pytest.fail("Two hub owners")
    with second.exclusive_run():
        pass


def test_concurrent_independent_connections_cannot_overbook(postgres_url):
    rig = Rig(store=PostgresStore(postgres_url))
    with ThreadPoolExecutor(8) as pool:
        jobs = list(
            pool.map(lambda i: rig.submit(key=f"parallel-{i}", cpus=2, memory_mib=2048), range(12))
        )
    assert sum(j["state"] == "DISPATCHING" for j in jobs) == 4
    assert rig.hub.cluster("alice")[0]["reserved_cpus"] == 8
    with pytest.raises(Error):
        rig.hub.create_user(UserCreate(id="alice", allowed_nodes=["A"]))
    assert rig.hub.authenticate(rig.alice)["id"] == "alice"


def test_cli_initialization_and_private_connection_file(tmp_path, postgres_url, monkeypatch):
    monkeypatch.delenv("HUB_DATABASE_URL_FILE", raising=False)
    url_file(tmp_path, postgres_url)
    command = [sys.executable, "-m", "cowork_hub", "init", "--data-dir", str(tmp_path)]
    # The project entry point is a function rather than a module __main__.
    command[1:3] = ["-c", "from cowork_hub.cli import main; main()"]
    first = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert first.returncode == 0, first.stderr
    token = (tmp_path / "admin.token").read_text().strip()
    assert configured_store(tmp_path).backend == "postgresql"
    assert Hub(configured_store(tmp_path)).authenticate(token)["role"] == "admin"
    assert postgres_url not in first.stdout + first.stderr
    assert token not in first.stdout + first.stderr
    second = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert second.returncode != 0 and (tmp_path / "admin.token").read_text().strip() == token
    assert not (tmp_path / "hub.sqlite3").exists()


def test_invalid_connection_never_falls_back_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("HUB_DATABASE_URL_FILE", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="connection file"):
        configured_store(tmp_path)
    assert not (tmp_path / "hub.sqlite3").exists()


def test_missing_activated_config_does_not_open_old_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_DATABASE_URL_FILE", raising=False)
    original = Rig(tmp_path / "hub.sqlite3")
    (tmp_path / "database-backend").write_text("postgresql\n")
    with pytest.raises(RuntimeError, match="connection file"):
        configured_store(tmp_path)
    assert original.hub.authenticate(original.alice)["id"] == "alice"


def test_lost_dispatcher_lock_stops_further_operations(postgres_url):
    store = PostgresStore(postgres_url)
    with store.exclusive_run():
        store._run_connection.close()
        with pytest.raises(RuntimeError, match="lease was lost"):
            with store.transaction():
                pytest.fail("A disconnected dispatcher must not reconnect and continue writing")


def test_database_error_does_not_expose_parameter_in_traceback(postgres_url):
    store = PostgresStore(postgres_url)
    value = "sensitive-value-must-not-appear-in-diagnostics"
    with pytest.raises(RuntimeError) as caught:
        with store.transaction(write=False) as db:
            db.execute("SELECT CAST(? AS BIGINT)", (value,))
    assert value not in "".join(traceback.format_exception(caught.value))
