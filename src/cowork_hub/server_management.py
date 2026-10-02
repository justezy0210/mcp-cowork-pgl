"""Web server administration against the scheduler's existing reservation ledger."""

import json
from typing import Literal

from pydantic import Field

from .models import Error, Identifier, Input, UserIdentity
from .service import digest, encode, new_id


class AccessRequest(UserIdentity):
    node_id: Identifier
    port: int = Field(ge=1, le=65535)
    request_key: str = Field(min_length=8, max_length=128)


class ReviewRequest(Input):
    decision: Literal["APPROVED", "REJECTED"]


class GrantUpdate(Input):
    allowed_nodes: list[Identifier] = Field(max_length=256)
    expected_nodes: list[Identifier] = Field(max_length=256)


class NodeBudget(Input):
    cpus: int = Field(gt=0, le=65536)
    memory_mib: int = Field(gt=0, le=2**40)
    enabled: bool


class NodeUpdate(NodeBudget):
    expected: NodeBudget


class AccountCreate(UserIdentity):
    id: Identifier
    firebase_uid: str = Field(min_length=1, max_length=128, pattern=r"^[^\s\x00-\x1f\x7f]+$")
    allowed_nodes: list[Identifier] = Field(default_factory=list, max_length=256)


def audit(db, hub, actor, action, target, before, after):
    db.execute(
        "INSERT INTO web_audit VALUES(?,?,?,?,?,?,?)",
        (new_id(), actor, action, target, encode(before), encode(after), hub.clock()),
    )


def page_rows(db, query, params, page, size, *, order):
    total = db.execute("SELECT COUNT(*) FROM (" + query + ") AS records", params).fetchone()[0]
    rows = db.execute(
        query + " ORDER BY " + order + " LIMIT ? OFFSET ?", (*params, size, (page - 1) * size)
    )
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": size}


class ServerManagement:
    def __init__(self, hub):
        self.hub = hub

    def candidate_environments(self):
        # Correlated to jobs AS j. Queued jobs have no assigned node yet.
        ids = (
            "SELECT jsonb_array_elements_text(j.spec::jsonb->'environment_ids')"
            if self.hub.store.backend == "postgresql"
            else "SELECT value FROM json_each(j.spec, '$.environment_ids')"
        )
        return f"SELECT e.node_id FROM environments e WHERE e.user_id=j.user_id AND e.id IN ({ids})"

    def server_job_filter(self, group):
        if group == "active":
            return "j.node_id=? AND j.state IN ('DISPATCHING','RUNNING','UNKNOWN')"
        return f"j.state='QUEUED' AND EXISTS ({self.candidate_environments()} AND e.node_id=?)"

    @staticmethod
    def grants(db, user_id):
        return [
            r[0]
            for r in db.execute(
                "SELECT node_id FROM grants WHERE user_id=? ORDER BY node_id", (user_id,)
            )
        ]

    def profile(self, user_id, is_admin):
        with self.hub.store.transaction(write=False) as db:
            grants = self.grants(db, user_id)
        return {
            "user_id": user_id,
            "is_admin": is_admin,
            "allowed_nodes": grants,
            "identity": self.hub.user_identity(user_id),
            "notification": self.hub.notification_status(user_id),
        }

    def servers(self, user_id=None):
        with self.hub.store.transaction(write=False) as db:
            query = "SELECT n.* FROM nodes n"
            if user_id is not None:
                query += " JOIN grants g ON g.node_id=n.id WHERE g.user_id=?"
            nodes = db.execute(query + " ORDER BY n.id", (user_id,) if user_id else ()).fetchall()
            usage = self.hub._usage(db)
            result = []
            for node in nodes:
                cpus, memory = usage["node"].get(node["id"], [0, 0])
                row = db.execute(
                    "SELECT MAX(r.last_seen) FROM local_runs r JOIN jobs j ON j.id=r.job_id "
                    "WHERE j.node_id=? AND j.state IN ('DISPATCHING','RUNNING','UNKNOWN')",
                    (node["id"],),
                ).fetchone()
                seen = max(
                    [value for value in (row[0], node["last_seen"]) if value is not None],
                    default=None,
                )
                counts = dict.fromkeys(("RUNNING", "DISPATCHING", "UNKNOWN", "QUEUED"), 0)
                for row in db.execute(
                    "SELECT j.state,COUNT(*) AS total FROM jobs j WHERE ("
                    + self.server_job_filter("active")
                    + ") OR ("
                    + self.server_job_filter("queued")
                    + ") GROUP BY j.state",
                    (node["id"], node["id"]),
                ):
                    counts[row["state"]] = row["total"]
                result.append(
                    {
                        "id": node["id"],
                        "cpus": node["cpus"],
                        "memory_mib": node["memory_mib"],
                        "enabled": bool(node["enabled"]),
                        "last_seen": seen,
                        "recent_report": seen is not None
                        and seen >= self.hub.clock() - self.hub.heartbeat_timeout,
                        "reserved_cpus": cpus,
                        "reserved_memory_mib": memory,
                        "job_counts": counts,
                        "gpus": [
                            {**gpu, "reserved": (node["id"], gpu["id"]) in usage["gpu"]}
                            for gpu in json.loads(node["gpus"])
                        ],
                    }
                )
            return result

    def server_jobs(self, user_id, node_id, group, page, size):
        with self.hub.store.transaction(write=False) as db:
            if not self.hub._allowed(db, user_id, node_id):
                raise Error("FORBIDDEN_NODE", "Server access is required", 403)
            result = page_rows(
                db,
                "SELECT j.id,j.user_id,j.spec,j.state,j.reason,j.cpus,j.memory_mib,"
                "j.created_at,j.started_at FROM jobs j WHERE " + self.server_job_filter(group),
                (node_id,),
                page,
                size,
                order="CASE WHEN j.state='RUNNING' THEN 0 ELSE 1 END,j.seq"
                if group == "active"
                else "j.seq",
            )
            for row in result["items"]:
                spec = json.loads(row.pop("spec"))
                row["name"] = spec["name"]
                row["gpu_count"] = spec.get("gpu_count", 0)
                row["multiple_candidates"] = False
                if group == "queued":
                    count = db.execute(
                        "SELECT COUNT(DISTINCT e.node_id) FROM environments e "
                        "WHERE e.user_id=? AND e.id IN ("
                        + ",".join("?" for _ in spec["environment_ids"])
                        + ")",
                        (row["user_id"], *spec["environment_ids"]),
                    ).fetchone()[0]
                    row["multiple_candidates"] = count > 1
            return result

    def jobs(self, user_id, page, size, *, include_completed=False, node_id=None):
        with self.hub.store.transaction(write=False) as db:
            if node_id is not None and not self.hub._allowed(db, user_id, node_id):
                raise Error("FORBIDDEN_NODE", "Server access is required", 403)
            terminal = "state IN ('SUCCEEDED','FAILED','CANCELLED')"
            order = f"CASE WHEN state='RUNNING' THEN 0 WHEN {terminal} THEN 2 ELSE 1 END,seq DESC"
            query = (
                "SELECT seq,id,spec,state,reason,node_id,cpus,memory_mib,gpu_ids,"
                "created_at,started_at,finished_at,exit_code,"
                f"CASE WHEN {terminal} THEN NULL ELSE "
                f"SUM(CASE WHEN {terminal} THEN 0 ELSE 1 END) OVER (ORDER BY {order}) "
                "END AS number FROM jobs j WHERE user_id=?"
            )
            params = [user_id]
            if node_id is not None:
                query += (
                    " AND (j.node_id=? OR (j.node_id IS NULL AND EXISTS ("
                    + self.candidate_environments()
                    + " AND e.node_id=?)))"
                )
                params.extend([node_id, node_id])
            if not include_completed:
                query += f" AND NOT ({terminal})"
            result = page_rows(
                db,
                query,
                params,
                page,
                size,
                order=order,
            )
            for row in result["items"]:
                spec = json.loads(row.pop("spec"))
                row["name"] = spec["name"]
                row["gpu_count"] = spec.get("gpu_count", 0)
                row["gpu_ids"] = json.loads(row["gpu_ids"])
                # Only assigned servers or permitted candidates are shown in the web summary.
                if row["node_id"] and not self.hub._allowed(db, user_id, row["node_id"]):
                    row["node_id"] = None
            return result

    def environments(self, user_id, page, size, *, pending=False):
        with self.hub.store.transaction(write=False) as db:
            query = (
                "SELECT e.id,e.user_id,e.name,e.node_id,e.ssh_target,e.workdir,e.status,e.uid,e.gid,"
                "e.cpus,e.memory_mib,e.gpu_ids,l.approved FROM environments e "
                "LEFT JOIN local_environments l ON l.environment_id=e.id"
            )
            query += " WHERE l.approved=0" if pending else " WHERE e.user_id=?"
            result = page_rows(
                db, query, () if pending else (user_id,), page, size, order="e.created_at DESC,e.id"
            )
            for row in result["items"]:
                row["gpu_ids"] = json.loads(row["gpu_ids"])
                row["allowed"] = self.hub._allowed(db, row["user_id"], row["node_id"])
            return result

    def registrations(self, user_id, page, size):
        with self.hub.store.transaction(write=False) as db:
            result = page_rows(
                db,
                "SELECT * FROM registration_requests WHERE user_id=?",
                (user_id,),
                page,
                size,
                order="created_at DESC,id",
            )
            result["items"] = [self.hub.management._request(row) for row in result["items"]]
            return result

    def request_access(self, user_id, body):
        with self.hub.store.transaction() as db:
            payload_hash = digest(encode(body.model_dump()))
            previous = db.execute(
                "SELECT * FROM server_access_requests WHERE user_id=? AND request_key=?",
                (user_id, body.request_key),
            ).fetchone()
            if previous:
                if previous["request_hash"] != payload_hash:
                    raise Error("IDEMPOTENCY_CONFLICT", "Request key has different data", 409)
                return self.access_view(previous)
            if not db.execute("SELECT 1 FROM nodes WHERE id=?", (body.node_id,)).fetchone():
                raise Error("NOT_FOUND", "Server does not exist", 404)
            if self.hub._allowed(db, user_id, body.node_id):
                raise Error("ALREADY_ALLOWED", "Server is already allowed", 409)
            identity = db.execute(
                "SELECT uid,gid FROM user_identities WHERE user_id=?", (user_id,)
            ).fetchone()
            if identity and (identity["uid"], identity["gid"]) != (body.uid, body.gid):
                raise Error("IDENTITY_MISMATCH", "Use your registered UID/GID", 409)
            if db.execute(
                "SELECT 1 FROM server_access_requests WHERE user_id=? AND node_id=? AND state='PENDING'",
                (user_id, body.node_id),
            ).fetchone():
                raise Error("REQUEST_PENDING", "A request for this server is pending", 409)
            request_id = new_id()
            db.execute(
                "INSERT INTO server_access_requests(id,user_id,node_id,uid,gid,port,request_key,request_hash,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    user_id,
                    body.node_id,
                    body.uid,
                    body.gid,
                    body.port,
                    body.request_key,
                    payload_hash,
                    self.hub.clock(),
                ),
            )
            audit(
                db,
                self.hub,
                user_id,
                "access.request",
                request_id,
                {},
                {"node_id": body.node_id, "uid": body.uid, "gid": body.gid, "port": body.port},
            )
            return self.access_view(
                db.execute(
                    "SELECT * FROM server_access_requests WHERE id=?", (request_id,)
                ).fetchone()
            )

    @staticmethod
    def access_view(row):
        return {
            key: row[key]
            for key in (
                "id",
                "user_id",
                "node_id",
                "uid",
                "gid",
                "port",
                "state",
                "created_at",
                "reviewed_at",
                "reviewed_by",
            )
        }

    def access_requests(self, user_id, page, size):
        with self.hub.store.transaction(write=False) as db:
            query = "SELECT * FROM server_access_requests"
            if user_id is not None:
                query += " WHERE user_id=?"
            result = page_rows(
                db, query, (user_id,) if user_id else (), page, size, order="created_at DESC,id"
            )
            result["items"] = [self.access_view(row) for row in result["items"]]
            return result

    def review_access(self, actor, request_id, decision):
        with self.hub.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM server_access_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not row:
                raise Error("NOT_FOUND", "Request does not exist", 404)
            if row["state"] != "PENDING":
                if row["state"] == decision:
                    return self.access_view(row)
                raise Error("REQUEST_REVIEWED", "Request was already reviewed", 409)
            if decision == "APPROVED":
                current_identity = db.execute(
                    "SELECT uid,gid FROM user_identities WHERE user_id=?", (row["user_id"],)
                ).fetchone()
                if current_identity and (current_identity["uid"], current_identity["gid"]) != (
                    row["uid"],
                    row["gid"],
                ):
                    raise Error("IDENTITY_MISMATCH", "Identity changed after this request", 409)
                self.hub._set_identity(
                    db, row["user_id"], UserIdentity(uid=row["uid"], gid=row["gid"])
                )
                before = self.grants(db, row["user_id"])
                self.hub._grants(db, row["user_id"], [*before, row["node_id"]])
                audit(
                    db,
                    self.hub,
                    actor,
                    "grants.update",
                    row["user_id"],
                    before,
                    self.grants(db, row["user_id"]),
                )
            db.execute(
                "UPDATE server_access_requests SET state=?,reviewed_at=?,reviewed_by=? WHERE id=?",
                (decision, self.hub.clock(), actor, request_id),
            )
            audit(
                db,
                self.hub,
                actor,
                "access.review",
                request_id,
                {"state": "PENDING"},
                {"state": decision},
            )
            changed = self.hub._schedule(db)
            result = self.access_view(
                db.execute(
                    "SELECT * FROM server_access_requests WHERE id=?", (request_id,)
                ).fetchone()
            )
        self.hub.jobs_changed(changed)
        return result

    def users(self, page, size):
        with self.hub.store.transaction(write=False) as db:
            result = page_rows(
                db,
                "SELECT p.id,p.enabled,i.uid,i.gid FROM principals p "
                "LEFT JOIN user_identities i ON i.user_id=p.id WHERE p.role='user'",
                (),
                page,
                size,
                order="p.id",
            )
            for row in result["items"]:
                row["allowed_nodes"] = self.grants(db, row["id"])
                destination = db.execute(
                    "SELECT channel_id FROM notification_destinations "
                    "WHERE user_id=? AND enabled=1 ORDER BY id DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                row["notification"] = {
                    "configured": bool(destination),
                    **(dict(destination) if destination else {}),
                }
            return result

    def update_grants(self, actor, user_id, body):
        with self.hub.store.transaction() as db:
            before = self.grants(db, user_id)
            if before != sorted(set(body.expected_nodes)):
                raise Error("STALE_SETTINGS", "Permissions changed; reload before saving", 409)
            self.hub._grants(db, user_id, body.allowed_nodes)
            after = self.grants(db, user_id)
            audit(db, self.hub, actor, "grants.update", user_id, before, after)
            changed = self.hub._schedule(db)
        self.hub.jobs_changed(changed)
        return {"user_id": user_id, "allowed_nodes": after}

    def update_node(self, actor, node_id, body):
        with self.hub.store.transaction() as db:
            node = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
            if not node:
                raise Error("NOT_FOUND", "Server does not exist", 404)
            before = {
                "cpus": node["cpus"],
                "memory_mib": node["memory_mib"],
                "enabled": bool(node["enabled"]),
            }
            if before != body.expected.model_dump():
                raise Error("STALE_SETTINGS", "Server settings changed; reload before saving", 409)
            used = self.hub._usage(db)["node"].get(node_id, [0, 0])
            if body.cpus < used[0] or body.memory_mib < used[1]:
                raise Error(
                    "RESOURCES_RESERVED", "Budget cannot be below current reservations", 409
                )
            after = body.model_dump(exclude={"expected"})
            db.execute(
                "UPDATE nodes SET cpus=?,memory_mib=?,enabled=? WHERE id=?",
                (body.cpus, body.memory_mib, int(body.enabled), node_id),
            )
            audit(db, self.hub, actor, "node.update", node_id, before, after)
            changed = self.hub._schedule(db)
        self.hub.jobs_changed(changed)
        return {"id": node_id, **after}

    def create_account(self, actor, body, config):
        with self.hub.store.transaction() as db:
            if body.firebase_uid in config.users or body.id in config.users.values():
                raise Error("ALREADY_EXISTS", "Account mapping already exists", 409)
            if db.execute(
                "SELECT 1 FROM web_accounts WHERE firebase_uid=? OR user_id=?",
                (body.firebase_uid, body.id),
            ).fetchone():
                raise Error("ALREADY_EXISTS", "Account mapping already exists", 409)
            existing = db.execute(
                "SELECT role,enabled FROM principals WHERE id=?", (body.id,)
            ).fetchone()
            if existing:
                if existing["role"] != "user" or not existing["enabled"]:
                    raise Error("FORBIDDEN", "Only an enabled user can be linked", 403)
                self.hub._require_identity(db, body.id, body.uid, body.gid)
                if body.allowed_nodes:
                    raise Error("INVALID_REQUEST", "Edit existing permissions separately", 409)
            else:
                self.hub._principal(db, body.id, "user")
                self.hub._set_identity(db, body.id, UserIdentity(uid=body.uid, gid=body.gid))
                self.hub._grants(db, body.id, body.allowed_nodes)
            db.execute(
                "INSERT INTO web_accounts VALUES(?,?,?)",
                (body.firebase_uid, body.id, self.hub.clock()),
            )
            audit(
                db,
                self.hub,
                actor,
                "account.link" if existing else "user.create",
                body.id,
                {},
                {"uid": body.uid, "gid": body.gid, "allowed_nodes": body.allowed_nodes},
            )
            return {"user_id": body.id, "created": not bool(existing), "linked": True}

    def audit_log(self, page, size):
        with self.hub.store.transaction(write=False) as db:
            result = page_rows(
                db, "SELECT * FROM web_audit", (), page, size, order="created_at DESC,id"
            )
            for row in result["items"]:
                row["before_value"] = json.loads(row["before_value"])
                row["after_value"] = json.loads(row["after_value"])
            return result
