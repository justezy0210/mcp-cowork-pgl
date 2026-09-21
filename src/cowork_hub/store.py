"""Local SQLite state. Every resource-changing operation uses one write transaction."""

import contextlib
import os
import sqlite3
from pathlib import Path

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
PRAGMA user_version = 4;
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        new = not self.path.exists()
        with contextlib.closing(self.connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4):
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
