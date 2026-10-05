"""Local SQLite state. Every resource-changing operation uses one write transaction."""

import contextlib
import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS principals (
  id TEXT PRIMARY KEY, role TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
  node_id TEXT, enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY, cpus INTEGER NOT NULL, memory_mib INTEGER NOT NULL,
  gpus TEXT NOT NULL, last_seen REAL, enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS grants (
  user_id TEXT NOT NULL REFERENCES principals(id), node_id TEXT NOT NULL REFERENCES nodes(id),
  PRIMARY KEY(user_id, node_id)
);
CREATE TABLE IF NOT EXISTS user_identities (
  user_id TEXT PRIMARY KEY REFERENCES principals(id),
  uid INTEGER NOT NULL UNIQUE CHECK(uid >= 0 AND uid <= 4294967295),
  gid INTEGER NOT NULL CHECK(gid >= 0 AND gid <= 4294967295)
);
CREATE TABLE IF NOT EXISTS environments (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES principals(id),
  node_id TEXT NOT NULL REFERENCES nodes(id), name TEXT NOT NULL,
  ssh_target TEXT NOT NULL, workdir TEXT NOT NULL, status TEXT NOT NULL,
  challenge_hash TEXT, challenge_expires REAL, container_id TEXT, uid INTEGER, gid INTEGER,
  cpus INTEGER, memory_mib INTEGER, gpu_ids TEXT NOT NULL DEFAULT '[]',
  observed_at REAL, created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_container
  ON environments(node_id, container_id) WHERE container_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS notification_destinations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES principals(id),
  secret_ref TEXT NOT NULL, channel_id TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL REFERENCES principals(id), request_key TEXT NOT NULL,
  request_hash TEXT NOT NULL, spec TEXT NOT NULL, state TEXT NOT NULL,
  reason TEXT, node_id TEXT, environment_id TEXT, execution_id TEXT UNIQUE,
  gpu_ids TEXT NOT NULL DEFAULT '[]', cpus INTEGER NOT NULL, memory_mib INTEGER NOT NULL,
  notification_id INTEGER NOT NULL REFERENCES notification_destinations(id),
  created_at REAL NOT NULL, started_at REAL, finished_at REAL,
  exit_code INTEGER, log_path TEXT, result_path TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
  UNIQUE(user_id, request_key)
);
CREATE INDEX IF NOT EXISTS jobs_state_seq ON jobs(state, seq);
CREATE INDEX IF NOT EXISTS jobs_node ON jobs(node_id, state);
CREATE TABLE IF NOT EXISTS gpu_reservations (
  node_id TEXT NOT NULL, gpu_id TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id),
  PRIMARY KEY(node_id, gpu_id)
);
CREATE TABLE IF NOT EXISTS worker_events (
  node_id TEXT NOT NULL, event_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
  PRIMARY KEY(node_id, event_id)
);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
  kind TEXT NOT NULL, destination_id INTEGER NOT NULL REFERENCES notification_destinations(id),
  payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt REAL NOT NULL, lease_until REAL, message_id TEXT, error_code TEXT,
  UNIQUE(job_id, kind)
);
CREATE TABLE IF NOT EXISTS local_environments (
  environment_id TEXT PRIMARY KEY REFERENCES environments(id),
  instance_id TEXT NOT NULL UNIQUE, approved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS local_runs (
  job_id TEXT PRIMARY KEY REFERENCES jobs(id), runner_id TEXT NOT NULL,
  last_seen REAL NOT NULL, claimed INTEGER NOT NULL DEFAULT 0, claim_id TEXT
);
CREATE TABLE IF NOT EXISTS user_tokens (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES principals(id), name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, revoked_at REAL
);
CREATE TABLE IF NOT EXISTS connectors (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES principals(id), instance_id TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL, environment_id TEXT NOT NULL REFERENCES environments(id),
  created_at REAL NOT NULL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS registration_requests (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES principals(id),
  connector_id TEXT NOT NULL REFERENCES connectors(id), request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
  target TEXT NOT NULL, state TEXT NOT NULL, claim_id TEXT, lease_until REAL,
  environment_id TEXT REFERENCES environments(id), error_code TEXT, created_at REAL NOT NULL, finished_at REAL,
  UNIQUE(user_id, request_key)
);
CREATE INDEX IF NOT EXISTS registration_queue ON registration_requests(connector_id,state,created_at);
CREATE TABLE IF NOT EXISTS server_access_requests (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES principals(id),
  node_id TEXT NOT NULL REFERENCES nodes(id), uid INTEGER NOT NULL, gid INTEGER NOT NULL,
  port INTEGER NOT NULL, request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'PENDING', created_at REAL NOT NULL,
  reviewed_at REAL, reviewed_by TEXT REFERENCES principals(id),
  UNIQUE(user_id, request_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_server_request
  ON server_access_requests(user_id,node_id) WHERE state='PENDING';
CREATE TABLE IF NOT EXISTS web_accounts (
  firebase_uid TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE REFERENCES principals(id),
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS web_audit (
  id TEXT PRIMARY KEY, actor TEXT NOT NULL REFERENCES principals(id),
  action TEXT NOT NULL, target TEXT NOT NULL, before_value TEXT NOT NULL,
  after_value TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS web_enrollments (
  id TEXT PRIMARY KEY, firebase_uid TEXT NOT NULL UNIQUE, email TEXT NOT NULL,
  account_name TEXT NOT NULL, uid INTEGER NOT NULL, gid INTEGER NOT NULL,
  secret_ref TEXT NOT NULL, channel_id TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'PENDING', created_at REAL NOT NULL,
  reviewed_at REAL, reviewed_by TEXT REFERENCES principals(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS pending_enrollment_name
  ON web_enrollments(account_name) WHERE state='PENDING';
CREATE UNIQUE INDEX IF NOT EXISTS pending_enrollment_uid
  ON web_enrollments(uid) WHERE state='PENDING';
CREATE TABLE IF NOT EXISTS enrollment_notifications (
  id TEXT PRIMARY KEY, enrollment_id TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES principals(id), payload TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt REAL NOT NULL, lease_until REAL, message_id TEXT, error_code TEXT,
  UNIQUE(enrollment_id, user_id)
);
CREATE TABLE IF NOT EXISTS catalog_state (
  id INTEGER PRIMARY KEY CHECK(id=1), revision TEXT NOT NULL,
  payload TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_requests (
  user_id TEXT NOT NULL REFERENCES principals(id), request_key TEXT NOT NULL,
  request_hash TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY(user_id, request_key)
);
CREATE TABLE IF NOT EXISTS catalog_events (
  id TEXT PRIMARY KEY, file_id TEXT NOT NULL, actor TEXT NOT NULL REFERENCES principals(id),
  action TEXT NOT NULL, before_value TEXT NOT NULL, after_value TEXT NOT NULL,
  request_key TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS catalog_events_file ON catalog_events(file_id,created_at);
PRAGMA user_version = 8;
"""


class Store:
    backend = "sqlite"
    integrity_error = sqlite3.IntegrityError
    first_environment_sql = "json_extract(j.spec,'$.environment_ids[0]')"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        new = not self.path.exists()
        with contextlib.closing(self.connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in range(SCHEMA_VERSION + 1):
                raise RuntimeError("Unsupported database schema version")
            db.execute("PRAGMA journal_mode=WAL")
            # Add tables without rewriting existing jobs, identities or credentials.
            db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
        if new:
            os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA cache_size=-4096")
        return db

    @contextlib.contextmanager
    def transaction(self, *, write=True):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def configured_store(data_dir):
    """Explicit PostgreSQL configuration never silently falls back to SQLite."""
    url_file = os.getenv("HUB_DATABASE_URL_FILE")
    active = Path(data_dir) / "postgres.url"
    marker = Path(data_dir) / "database-backend"
    if marker.exists() and marker.read_text().strip() != "postgresql":
        raise RuntimeError("Unknown database backend marker; no database was opened")
    if not url_file and (active.exists() or marker.exists()):
        url_file = active
    if url_file:
        from .postgres import PostgresStore

        return PostgresStore.from_file(Path(url_file))
    return Store(Path(data_dir) / "hub.sqlite3")
