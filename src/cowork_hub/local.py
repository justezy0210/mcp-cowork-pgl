"""User-owned pull runners. No Docker socket, remote shell, or process control in the hub."""

import json
import sqlite3

from .models import Error, LocalEnvironment
from .service import encode, new_id


class LocalJobs:
    def __init__(self, hub):
        self.hub = hub

    def register(self, user_id, request: LocalEnvironment):
        with self.hub.store.transaction() as db:
            if not self.hub._allowed(db, user_id, request.node_id):
                raise Error("FORBIDDEN_NODE", "This server is not allowed", 403)
            self.hub._require_identity(db, user_id, request.uid, request.gid)
            node = db.execute("SELECT * FROM nodes WHERE id=?", (request.node_id,)).fetchone()
            if (
                request.cpus > node["cpus"]
                or request.memory_mib > node["memory_mib"]
                or not set(request.gpu_ids) <= {g["id"] for g in json.loads(node["gpus"])}
            ):
                raise Error("INVALID_CAPACITY", "Environment exceeds the node budget")
            previous = db.execute(
                """SELECT e.*,l.instance_id,l.approved FROM environments e
                JOIN local_environments l ON l.environment_id=e.id WHERE l.instance_id=?""",
                (request.instance_id,),
            ).fetchone()
            if previous:
                if (
                    previous["user_id"] != user_id
                    or any(
                        previous[k] != getattr(request, k)
                        for k in (
                            "node_id",
                            "name",
                            "ssh_target",
                            "workdir",
                            "uid",
                            "gid",
                            "cpus",
                            "memory_mib",
                        )
                    )
                    or set(json.loads(previous["gpu_ids"])) != set(request.gpu_ids)
                ):
                    raise Error(
                        "ENVIRONMENT_CONFLICT", "Instance already has different settings", 409
                    )
                return {"id": previous["id"], "approved": bool(previous["approved"])}
            env_id = new_id()
            try:
                db.execute(
                    """INSERT INTO environments(id,user_id,node_id,name,ssh_target,workdir,status,
                    container_id,uid,gid,cpus,memory_mib,gpu_ids,created_at)
                    VALUES(?,?,?,?,?,?,'PENDING_APPROVAL',?,?,?,?,?,?,?)""",
                    (
                        env_id,
                        user_id,
                        request.node_id,
                        request.name,
                        request.ssh_target,
                        request.workdir,
                        "local-" + request.instance_id,
                        request.uid,
                        request.gid,
                        request.cpus,
                        request.memory_mib,
                        encode(sorted(set(request.gpu_ids))),
                        self.hub.clock(),
                    ),
                )
                db.execute(
                    "INSERT INTO local_environments VALUES(?,?,0)", (env_id, request.instance_id)
                )
            except sqlite3.IntegrityError as exc:
                raise Error("ENVIRONMENT_CONFLICT", "Instance is already registered", 409) from exc
        return {"id": env_id, "approved": False}

    def approve(self, env_id):
        """Administrator confirms owner and physical node mapping; this is not Docker attestation."""
        with self.hub.store.transaction() as db:
            env = db.execute(
                "SELECT e.* FROM environments e JOIN local_environments l ON l.environment_id=e.id WHERE e.id=?",
                (env_id,),
            ).fetchone()
            if not env:
                raise Error("NOT_FOUND", "Local environment does not exist", 404)
            self.hub._require_identity(db, env["user_id"], env["uid"], env["gid"])
            if not self.hub._allowed(db, env["user_id"], env["node_id"]):
                raise Error("FORBIDDEN_NODE", "Server grant is missing", 403)
            db.execute("UPDATE local_environments SET approved=1 WHERE environment_id=?", (env_id,))
            db.execute("UPDATE environments SET status='READY' WHERE id=?", (env_id,))
        return {"id": env_id, "approved": True}

    def validate(self, db, user_id, env_id, identity):
        env = db.execute(
            """SELECT e.*,l.instance_id,l.approved FROM environments e
            JOIN local_environments l ON l.environment_id=e.id WHERE e.id=? AND e.user_id=?""",
            (env_id, user_id),
        ).fetchone()
        if not env or env["instance_id"] != identity.instance_id:
            raise Error("NOT_FOUND", "Runner environment does not belong to this user", 404)
        if not env["approved"]:
            raise Error(
                "ENVIRONMENT_NOT_APPROVED", "An administrator must confirm the node mapping", 409
            )
        if (env["uid"], env["gid"]) != (identity.uid, identity.gid):
            raise Error("IDENTITY_MISMATCH", "Runner UID/GID does not match the environment", 409)
        self.hub._require_identity(db, user_id, identity.uid, identity.gid)
        return env

    def owned(self, db, user_id, job_id, identity):
        job = self.hub._owned_job(db, user_id, job_id)
        run = db.execute("SELECT * FROM local_runs WHERE job_id=?", (job_id,)).fetchone()
        if not run or run["runner_id"] != identity.runner_id:
            raise Error("NOT_FOUND", "Job is not owned by this runner", 404)
        spec = json.loads(job["spec"])
        env = self.validate(db, user_id, spec["environment_ids"][0], identity)
        return job, run, env

    def poll(self, user_id, job_id, identity):
        with self.hub.store.transaction() as db:
            self.hub._expire(db)
            self.owned(db, user_id, job_id, identity)
            db.execute(
                "UPDATE local_runs SET last_seen=? WHERE job_id=?", (self.hub.clock(), job_id)
            )
            self.hub._schedule(db)
            return self.hub._job_view(db, self.hub._owned_job(db, user_id, job_id))

    def claim(self, user_id, job_id, identity):
        with self.hub.store.transaction() as db:
            self.hub._expire(db)
            job, run, env = self.owned(db, user_id, job_id, identity)
            if not identity.claim_id:
                raise Error("INVALID_REQUEST", "A unique claim attempt ID is required")
            if not self.hub._allowed(db, user_id, env["node_id"]):
                raise Error("FORBIDDEN_NODE", "Server grant was revoked", 403)
            if job["state"] != "DISPATCHING" or job["cancel_requested"] or run["claimed"]:
                raise Error(
                    "EXECUTION_NOT_GRANTED", "No new execution permission is available", 409
                )
            db.execute(
                "UPDATE local_runs SET claimed=1,last_seen=?,claim_id=? WHERE job_id=?",
                (self.hub.clock(), identity.claim_id, job_id),
            )
            return {
                "granted": True,
                "job_id": job_id,
                "execution_id": job["execution_id"],
                "gpu_ids": json.loads(job["gpu_ids"]),
            }

    def event(self, user_id, job_id, request):
        # Ownership is checked inside the same transaction as the transition.
        return self.hub.event(None, job_id, request.event, user_id=user_id, runner=request.runner)

    def is_online(self, db, node_id, user_id):
        return bool(
            db.execute(
                """SELECT 1 FROM local_runs r JOIN jobs j ON j.id=r.job_id
            JOIN environments e ON e.id=json_extract(j.spec,'$.environment_ids[0]')
            WHERE e.node_id=? AND j.user_id=? AND r.last_seen>=?
            AND j.state IN ('QUEUED','DISPATCHING','RUNNING','UNKNOWN') LIMIT 1""",
                (node_id, user_id, self.hub.clock() - self.hub.heartbeat_timeout),
            ).fetchone()
        )
