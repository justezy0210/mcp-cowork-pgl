"""User tokens and durable SSH-registration requests for a user's main connector."""

import json
import secrets

from .models import Error
from .service import digest, encode, new_id


class Management:
    def __init__(self, hub):
        self.hub = hub

    def create_token(self, user_id, name):
        token, token_id = secrets.token_urlsafe(32), new_id()
        now = self.hub.clock()
        with self.hub.store.transaction() as db:
            db.execute(
                "INSERT INTO user_tokens(id,user_id,name,token_hash,created_at) VALUES(?,?,?,?,?)",
                (token_id, user_id, name, digest(token), now),
            )
        return {"id": token_id, "name": name, "created_at": now, "token": token}

    def list_tokens(self, user_id, limit=100, after=""):
        with self.hub.store.transaction(write=False) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,name,created_at,revoked_at FROM user_tokens "
                    "WHERE user_id=? AND id>? ORDER BY id LIMIT ?",
                    (user_id, after, limit),
                )
            ]

    def revoke_token(self, user_id, token_id):
        with self.hub.store.transaction() as db:
            row = db.execute(
                "SELECT id FROM user_tokens WHERE user_id=? AND id=?", (user_id, token_id)
            ).fetchone()
            if not row:
                raise Error("NOT_FOUND", "Token does not exist", 404)
            db.execute(
                "UPDATE user_tokens SET revoked_at=COALESCE(revoked_at,?) WHERE id=?",
                (self.hub.clock(), token_id),
            )
        return {"id": token_id, "revoked": True}

    def register_connector(self, user_id, body):
        with self.hub.store.transaction() as db:
            env = db.execute(
                "SELECT id FROM environments WHERE id=? AND user_id=?",
                (body.environment_id, user_id),
            ).fetchone()
            if not env:
                raise Error("NOT_FOUND", "Main environment does not exist", 404)
            row = db.execute(
                "SELECT * FROM connectors WHERE instance_id=?", (body.instance_id,)
            ).fetchone()
            if row:
                if (row["user_id"], row["environment_id"]) != (user_id, body.environment_id):
                    raise Error("CONNECTOR_CONFLICT", "Connector identity differs", 409)
                return {"id": row["id"], "instance_id": row["instance_id"]}
            connector_id = new_id()
            db.execute(
                "INSERT INTO connectors(id,user_id,instance_id,name,environment_id,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    connector_id,
                    user_id,
                    body.instance_id,
                    body.name,
                    body.environment_id,
                    self.hub.clock(),
                ),
            )
        return {"id": connector_id, "instance_id": body.instance_id}

    def list_connectors(self, user_id, limit=100, after=""):
        with self.hub.store.transaction(write=False) as db:
            rows = db.execute(
                "SELECT id,name,environment_id,last_seen FROM connectors "
                "WHERE user_id=? AND id>? ORDER BY id LIMIT ?",
                (user_id, after, limit),
            ).fetchall()
        return [
            {
                **dict(r),
                "online": r["last_seen"] is not None and self.hub.clock() - r["last_seen"] < 45,
            }
            for r in rows
        ]

    @staticmethod
    def _connector(db, user_id, connector_id, instance_id=None):
        row = db.execute(
            "SELECT * FROM connectors WHERE id=? AND user_id=?", (connector_id, user_id)
        ).fetchone()
        if not row or (instance_id is not None and row["instance_id"] != instance_id):
            raise Error("NOT_FOUND", "Connector does not exist", 404)
        return row

    def request_registration(self, user_id, body):
        payload_hash = digest(encode(body.model_dump()))
        with self.hub.store.transaction() as db:
            self._connector(db, user_id, body.connector_id)
            if not self.hub._allowed(db, user_id, body.target.node_id):
                raise Error("FORBIDDEN_NODE", "Server is not allowed", 403)
            previous = db.execute(
                "SELECT * FROM registration_requests WHERE user_id=? AND request_key=?",
                (user_id, body.request_key),
            ).fetchone()
            if previous:
                if previous["request_hash"] != payload_hash:
                    raise Error("IDEMPOTENCY_CONFLICT", "Request key has different data", 409)
                return self._request(previous)
            request_id = new_id()
            db.execute(
                "INSERT INTO registration_requests(id,user_id,connector_id,request_key,request_hash,"
                "target,state,created_at) VALUES(?,?,?,?,?,?,'PENDING',?)",
                (
                    request_id,
                    user_id,
                    body.connector_id,
                    body.request_key,
                    payload_hash,
                    encode(body.target.model_dump()),
                    self.hub.clock(),
                ),
            )
            return self._request(
                db.execute(
                    "SELECT * FROM registration_requests WHERE id=?", (request_id,)
                ).fetchone()
            )

    @staticmethod
    def _request(row):
        return {
            k: (json.loads(row[k]) if k == "target" else row[k])
            for k in (
                "id",
                "connector_id",
                "request_key",
                "target",
                "state",
                "environment_id",
                "error_code",
                "created_at",
                "finished_at",
            )
        }

    def list_requests(self, user_id, limit=100, after=""):
        with self.hub.store.transaction(write=False) as db:
            return [
                self._request(r)
                for r in db.execute(
                    "SELECT * FROM registration_requests WHERE user_id=? AND id>? ORDER BY id LIMIT ?",
                    (user_id, after, limit),
                )
            ]

    def poll(self, user_id, connector_id, body):
        now = self.hub.clock()
        with self.hub.store.transaction() as db:
            self._connector(db, user_id, connector_id, body.instance_id)
            db.execute("UPDATE connectors SET last_seen=? WHERE id=?", (now, connector_id))
            # One request at a time per connector. A lease expires after a process/network failure.
            active = db.execute(
                "SELECT 1 FROM registration_requests WHERE connector_id=? "
                "AND state='RUNNING' AND lease_until>?",
                (connector_id, now),
            ).fetchone()
            if active:
                return {"request": None}
            row = db.execute(
                "SELECT * FROM registration_requests WHERE connector_id=? "
                "AND (state='PENDING' OR (state='RUNNING' AND lease_until<=?)) "
                "ORDER BY created_at,id LIMIT 1",
                (connector_id, now),
            ).fetchone()
            if not row:
                return {"request": None}
            target = json.loads(row["target"])
            if not self.hub._allowed(db, user_id, target["node_id"]):
                db.execute(
                    "UPDATE registration_requests SET state='FAILED',error_code='FORBIDDEN_NODE',"
                    "finished_at=? WHERE id=?",
                    (now, row["id"]),
                )
                return {"request": None}
            claim = new_id()
            db.execute(
                "UPDATE registration_requests SET state='RUNNING',claim_id=?,lease_until=? WHERE id=?",
                (claim, now + 120, row["id"]),
            )
            return {"request": {"id": row["id"], "target": target, "claim_id": claim}}

    def complete(self, user_id, connector_id, request_id, body):
        with self.hub.store.transaction() as db:
            self._connector(db, user_id, connector_id, body.instance_id)
            row = db.execute(
                "SELECT * FROM registration_requests WHERE id=? AND connector_id=? AND user_id=?",
                (request_id, connector_id, user_id),
            ).fetchone()
            if not row:
                raise Error("NOT_FOUND", "Registration request does not exist", 404)
            if row["claim_id"] != body.claim_id:
                raise Error("STALE_CLAIM", "Request was assigned again", 409)
            outcome = "REGISTERED" if body.environment_id else "FAILED"
            if row["state"] in ("REGISTERED", "FAILED"):
                if (row["environment_id"], row["error_code"]) != (
                    body.environment_id,
                    body.error_code,
                ):
                    raise Error("IDEMPOTENCY_CONFLICT", "Result differs", 409)
                return self._request(row)
            if row["state"] != "RUNNING":
                raise Error("STALE_CLAIM", "Request is not running", 409)
            if body.environment_id:
                target = json.loads(row["target"])
                env = db.execute(
                    "SELECT * FROM environments WHERE id=? AND user_id=?",
                    (body.environment_id, user_id),
                ).fetchone()
                expected_ssh = f"ssh -p {target['port']} {target['user']}@{target['host']}"
                if not env or (env["node_id"], env["ssh_target"], env["workdir"]) != (
                    target["node_id"],
                    expected_ssh,
                    target["workdir"],
                ):
                    raise Error(
                        "ENVIRONMENT_CONFLICT", "Result does not match the requested target", 409
                    )
            db.execute(
                "UPDATE registration_requests SET state=?,environment_id=?,error_code=?,finished_at=?,"
                "lease_until=NULL WHERE id=?",
                (outcome, body.environment_id, body.error_code, self.hub.clock(), request_id),
            )
            return self._request(
                db.execute(
                    "SELECT * FROM registration_requests WHERE id=?", (request_id,)
                ).fetchone()
            )

    def pending_environments(self, limit=100, after=""):
        with self.hub.store.transaction(write=False) as db:
            return [
                {**dict(r), "gpu_ids": json.loads(r["gpu_ids"])}
                for r in db.execute(
                    "SELECT e.id,e.user_id,e.node_id,e.name,e.ssh_target,e.workdir,e.uid,e.gid,e.cpus,"
                    "e.memory_mib,e.gpu_ids,e.created_at FROM environments e JOIN local_environments l "
                    "ON e.id=l.environment_id WHERE l.approved=0 AND e.id>? ORDER BY e.id LIMIT ?",
                    (after, limit),
                )
            ]
