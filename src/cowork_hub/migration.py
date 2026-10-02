"""One-way, verified import of a stopped current SQLite hub into empty PostgreSQL."""

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from .postgres import SCHEMA_VERSION, PostgresStore
from .store import SCHEMA

TABLES = tuple(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA))
SEQUENCES = {"notification_destinations": "id", "jobs": "seq", "notifications": "id"}


def fingerprint(rows):
    # Sort row digests rather than relying on identical database collations.
    digests = []
    for row in rows:
        value = json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digests.append(hashlib.sha256(value.encode()).digest())
    return len(digests), hashlib.sha256(b"".join(sorted(digests))).digest()


def migrate(data_dir, url_file):
    directory = Path(data_dir).resolve()
    source_path = directory / "hub.sqlite3"
    if not source_path.is_file():
        raise RuntimeError("Existing hub.sqlite3 is required; no database was created")
    with (directory / "hub.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Stop the existing hub before migrating") from None
        target = PostgresStore.from_file(url_file, initialize=False)
        with target.exclusive_run():
            target.initialize()
            with contextlib.closing(
                sqlite3.connect(source_path.as_uri() + "?mode=rw", uri=True, timeout=5)
            ) as source:
                source.row_factory = sqlite3.Row
                # Block administrative SQLite writers too, for the whole import.
                source.execute("BEGIN IMMEDIATE")
                try:
                    if source.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                        raise RuntimeError(
                            f"Upgrade the source SQLite hub to schema {SCHEMA_VERSION} before migrating"
                        )
                    if (
                        source.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                        or source.execute("PRAGMA foreign_key_check").fetchone()
                    ):
                        raise RuntimeError("Source database integrity check failed")
                    actual = {
                        r[0]
                        for r in source.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                        )
                    }
                    if actual != set(TABLES):
                        raise RuntimeError(
                            "Unexpected source tables; migration stopped without dropping data"
                        )
                    if not source.execute("SELECT 1 FROM principals WHERE role='admin'").fetchone():
                        raise RuntimeError("Source hub is not initialized")
                    with target.transaction() as db:
                        for table in TABLES:
                            if db.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                                raise RuntimeError(
                                    "Target PostgreSQL hub is not empty; existing data was preserved"
                                )
                        # A second read-only connection backs up the committed,
                        # write-locked source without waiting on our own write lock.
                        backup = directory / (
                            "hub-before-postgres-"
                            + datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%f")
                            + ".sqlite3"
                        )
                        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        os.close(descriptor)
                        with (
                            contextlib.closing(
                                sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
                            ) as reader,
                            contextlib.closing(sqlite3.connect(backup)) as destination,
                        ):
                            reader.backup(destination)
                        counts = {}
                        for table in TABLES:
                            cursor = source.execute(f'SELECT * FROM "{table}"')
                            columns = [item[0] for item in cursor.description]
                            names = ",".join('"' + c + '"' for c in columns)
                            placeholders = ",".join("?" for _ in columns)
                            while rows := cursor.fetchmany(500):
                                db.executemany(
                                    f'INSERT INTO "{table}" ({names}) VALUES({placeholders})',
                                    [tuple(row) for row in rows],
                                )
                            expected = fingerprint(source.execute(f'SELECT * FROM "{table}"'))
                            observed = fingerprint(db.execute(f'SELECT * FROM "{table}"'))
                            if observed != expected:
                                raise RuntimeError(
                                    "Migration verification failed; target changes were rolled back"
                                )
                            counts[table] = expected[0]
                        for table, column in SEQUENCES.items():
                            previous = source.execute(
                                "SELECT seq FROM sqlite_sequence WHERE name=?", (table,)
                            ).fetchone()
                            maximum = (
                                db.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()[0]
                                or 0
                            )
                            high_water = max(previous[0] if previous else 0, maximum)
                            db.execute(
                                "SELECT setval(pg_get_serial_sequence(?,?),?,?)",
                                (table, column, max(1, high_water), bool(high_water)),
                            )
                    return {
                        "backend": "postgresql",
                        "schema_version": SCHEMA_VERSION,
                        "rows": counts,
                        "backup": str(backup),
                        "verified": True,
                    }
                finally:
                    source.rollback()
