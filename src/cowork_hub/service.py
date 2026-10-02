"""Authenticated state transitions and cooperative, transactional reservations."""

import hashlib
import json
import secrets
import time
import uuid

from .models import (
    EnvironmentCreate,
    EnvironmentVerification,
    Error,
    Heartbeat,
    JobSpec,
    JobSubmit,
    NodeCreate,
    PlanRequest,
    UserCreate,
    UserIdentity,
    WorkerEvent,
)
from .store import Store

ACTIVE = ("DISPATCHING", "RUNNING", "UNKNOWN")
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED")


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def new_id():
    return uuid.uuid4().hex


class Hub:
    def __init__(self, store: Store, *, clock=time.time, heartbeat_timeout=45):
        self.store = store
        self.clock = clock
        self.heartbeat_timeout = heartbeat_timeout
        # The API installs a thread-safe listener. Publish only after a successful commit.
        self.on_jobs_changed = None
        from .local import LocalJobs

        self.local = LocalJobs(self)
        from .management import Management

        self.management = Management(self)

    def jobs_changed(self, job_ids):
        listener = self.on_jobs_changed
        if job_ids and listener:
            listener(frozenset(job_ids))

    def bootstrap(self):
        """Return a one-time credential; never overwrite an existing administrator."""
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM principals WHERE role='admin'").fetchone():
                raise Error("ALREADY_INITIALIZED", "An administrator already exists", 409)
            return self._principal(db, "admin", "admin")

    def _principal(self, db, principal_id, role, node_id=None):
        token = secrets.token_urlsafe(32)
        try:
            db.execute(
                "INSERT INTO principals(id,role,token_hash,node_id) VALUES(?,?,?,?)",
                (principal_id, role, digest(token), node_id),
            )
        except self.store.integrity_error as exc:
            raise Error("ALREADY_EXISTS", "Principal already exists", 409) from exc
        return token

    def authenticate(self, token):
        with self.store.transaction(write=False) as db:
            row = db.execute(
                "SELECT id,role,node_id FROM principals WHERE token_hash=? AND enabled=1",
                (digest(token),),
            ).fetchone()
            if not row:
                row = db.execute(
                    "SELECT p.id,p.role,p.node_id FROM user_tokens t JOIN principals p ON p.id=t.user_id "
                    "WHERE t.token_hash=? AND t.revoked_at IS NULL AND p.enabled=1 AND p.role='user'",
                    (digest(token),),
                ).fetchone()
        if not row:
            raise Error("UNAUTHENTICATED", "A valid bearer token is required", 401)
        return dict(row)

    def create_node(self, request: NodeCreate):
        with self.store.transaction() as db:
            try:
                db.execute(
                    "INSERT INTO nodes(id,cpus,memory_mib,gpus) VALUES(?,?,?,?)",
                    (
                        request.id,
                        request.cpus,
                        request.memory_mib,
                        encode([g.model_dump() for g in request.gpus]),
                    ),
                )
            except self.store.integrity_error as exc:
                raise Error("ALREADY_EXISTS", "Node already exists", 409) from exc
            token = self._principal(db, f"worker:{request.id}", "worker", request.id)
        return {"id": request.id, "worker_token": token}

    def create_user(self, request: UserCreate):
        with self.store.transaction() as db:
            token = self._principal(db, request.id, "user")
            self._grants(db, request.id, request.allowed_nodes)
            if request.identity is not None:
                self._set_identity(db, request.id, request.identity)
        return {"id": request.id, "token": token}

    @staticmethod
    def _set_identity(db, user_id, identity):
        if not db.execute(
            "SELECT 1 FROM principals WHERE id=? AND role='user'", (user_id,)
        ).fetchone():
            raise Error("NOT_FOUND", "User does not exist", 404)
        if db.execute(
            "SELECT 1 FROM user_identities WHERE uid=? AND user_id<>?", (identity.uid, user_id)
        ).fetchone():
            raise Error("UID_ALREADY_ASSIGNED", "This UID belongs to another hub user", 409)
        if db.execute(
            """SELECT 1 FROM environments WHERE user_id=? AND container_id IS NOT NULL
               AND (uid<>? OR gid<>?)""",
            (user_id, identity.uid, identity.gid),
        ).fetchone():
            raise Error(
                "IDENTITY_IN_USE", "Existing verified environments have a different identity", 409
            )
        db.execute(
            """INSERT INTO user_identities(user_id,uid,gid) VALUES(?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET uid=excluded.uid,gid=excluded.gid""",
            (user_id, identity.uid, identity.gid),
        )

    def set_user_identity(self, user_id, identity: UserIdentity):
        with self.store.transaction() as db:
            self._set_identity(db, user_id, identity)
        return {"user_id": user_id, "configured": True, **identity.model_dump()}

    def user_identity(self, user_id):
        with self.store.transaction(write=False) as db:
            row = db.execute(
                "SELECT uid,gid FROM user_identities WHERE user_id=?", (user_id,)
            ).fetchone()
        return {"configured": bool(row), **(dict(row) if row else {})}

    @staticmethod
    def _identity_matches(db, user_id, uid, gid):
        return bool(
            db.execute(
                "SELECT 1 FROM user_identities WHERE user_id=? AND uid=? AND gid=?",
                (user_id, uid, gid),
            ).fetchone()
        )

    @staticmethod
    def _require_identity(db, user_id, uid, gid):
        row = db.execute(
            "SELECT uid,gid FROM user_identities WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            raise Error(
                "USER_IDENTITY_REQUIRED", "An administrator must configure your UID/GID", 409
            )
        if (row["uid"], row["gid"]) != (uid, gid):
            raise Error(
                "IDENTITY_MISMATCH",
                "Environment UID/GID differs from your configured identity",
                409,
            )

    def _grants(self, db, user_id, nodes):
        if not db.execute(
            "SELECT 1 FROM principals WHERE id=? AND role='user'", (user_id,)
        ).fetchone():
            raise Error("NOT_FOUND", "User does not exist", 404)
        for node in set(nodes):
            if not db.execute("SELECT 1 FROM nodes WHERE id=?", (node,)).fetchone():
                raise Error("NOT_FOUND", "Allowed node does not exist", 404)
        db.execute("DELETE FROM grants WHERE user_id=?", (user_id,))
        db.executemany("INSERT INTO grants VALUES(?,?)", [(user_id, n) for n in set(nodes)])

    def set_grants(self, user_id, nodes):
        with self.store.transaction() as db:
            self._grants(db, user_id, nodes)
            changed = self._schedule(db)
        self.jobs_changed(changed)

    @staticmethod
    def _allowed(db, user_id, node_id):
        return bool(
            db.execute(
                "SELECT 1 FROM grants WHERE user_id=? AND node_id=?", (user_id, node_id)
            ).fetchone()
        )

    def register_environment(self, user_id, request: EnvironmentCreate):
        env_id, challenge = new_id(), secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            if not self._allowed(db, user_id, request.node_id):
                raise Error("FORBIDDEN_NODE", "This server is not allowed", 403)
            db.execute(
                """INSERT INTO environments(id,user_id,node_id,name,ssh_target,workdir,status,
                challenge_hash,challenge_expires,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    env_id,
                    user_id,
                    request.node_id,
                    request.name,
                    request.ssh_target,
                    request.workdir,
                    "PENDING_VERIFICATION",
                    digest(challenge),
                    self.clock() + 600,
                    self.clock(),
                ),
            )
        return {
            "id": env_id,
            "status": "PENDING_VERIFICATION",
            "challenge": challenge,
            "expires_in_seconds": 600,
        }

    def verify_environment(self, node_id, env_id, request: EnvironmentVerification):
        with self.store.transaction() as db:
            if db.execute(
                "SELECT 1 FROM local_environments WHERE environment_id=?", (env_id,)
            ).fetchone():
                raise Error(
                    "FORBIDDEN", "Local environments are registered by their owning runner", 403
                )
            env = db.execute("SELECT * FROM environments WHERE id=?", (env_id,)).fetchone()
            if not env or env["node_id"] != node_id:
                raise Error("NOT_FOUND", "Environment does not exist on this node", 404)
            if (
                env["status"] != "PENDING_VERIFICATION"
                or env["challenge_expires"] < self.clock()
                or not secrets.compare_digest(env["challenge_hash"], digest(request.challenge))
            ):
                raise Error("INVALID_PROOF", "Registration proof is invalid or expired", 409)
            if not self._allowed(db, env["user_id"], node_id):
                raise Error("FORBIDDEN_NODE", "The server grant was revoked", 403)
            self._require_identity(db, env["user_id"], request.uid, request.gid)
            node = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
            gpu_ids = {g["id"] for g in json.loads(node["gpus"])}
            if (
                request.cpus > node["cpus"]
                or request.memory_mib > node["memory_mib"]
                or not set(request.gpu_ids) <= gpu_ids
            ):
                raise Error("INVALID_CAPACITY", "Environment exceeds the registered node budget")
            try:
                db.execute(
                    """UPDATE environments SET status='READY',challenge_hash=NULL,
                    challenge_expires=NULL,container_id=?,uid=?,gid=?,cpus=?,memory_mib=?,
                    gpu_ids=?,observed_at=? WHERE id=?""",
                    (
                        request.container_id,
                        request.uid,
                        request.gid,
                        request.cpus,
                        request.memory_mib,
                        encode(sorted(set(request.gpu_ids))),
                        self.clock(),
                        env_id,
                    ),
                )
            except self.store.integrity_error as exc:
                raise Error(
                    "CONTAINER_REGISTERED", "This container is already registered", 409
                ) from exc
            changed = self._schedule(db)
        self.jobs_changed(changed)
        return {"id": env_id, "status": "READY"}

    def list_environments(self, user_id, limit=100, after=""):
        with self.store.transaction(write=False) as db:
            rows = db.execute(
                """SELECT id,name,node_id,ssh_target,workdir,status,container_id,uid,gid,cpus,memory_mib,
                gpu_ids FROM environments WHERE user_id=? AND id>? ORDER BY id LIMIT ?""",
                (user_id, after, limit),
            ).fetchall()
        return [{**dict(r), "gpu_ids": json.loads(r["gpu_ids"])} for r in rows]

    def heartbeat(self, node_id, request: Heartbeat):
        now = self.clock()
        with self.store.transaction() as db:
            node = db.execute("SELECT last_seen FROM nodes WHERE id=?", (node_id,)).fetchone()
            recovered = (
                node["last_seen"] is None or node["last_seen"] <= now - self.heartbeat_timeout
            )
            before = {
                row["id"]: row["status"]
                for row in db.execute(
                    "SELECT id,status FROM environments WHERE node_id=? AND container_id IS NOT NULL "
                    "AND id NOT IN (SELECT environment_id FROM local_environments)",
                    (node_id,),
                )
            }
            # Expire the old view before accepting a returning node's heartbeat.
            changed = self._expire(db) if recovered else set()
            db.execute("UPDATE nodes SET last_seen=? WHERE id=?", (now, node_id))
            db.execute(
                """UPDATE environments SET status='UNAVAILABLE' WHERE node_id=? AND container_id IS NOT NULL
                AND id NOT IN (SELECT environment_id FROM local_environments)""",
                (node_id,),
            )
            for observation in request.environments:
                if db.execute(
                    "SELECT 1 FROM local_environments WHERE environment_id=?",
                    (observation.environment_id,),
                ).fetchone():
                    raise Error("FORBIDDEN", "Node Worker cannot update a local environment", 403)
                env = db.execute(
                    "SELECT * FROM environments WHERE id=? AND node_id=?",
                    (observation.environment_id, node_id),
                ).fetchone()
                if not env or not env["container_id"]:
                    raise Error(
                        "INVALID_ENVIRONMENT", "Heartbeat includes an unverified environment"
                    )
                ready = (
                    observation.ready
                    and observation.container_id == env["container_id"]
                    and set(observation.gpu_ids) == set(json.loads(env["gpu_ids"]))
                    and self._identity_matches(db, env["user_id"], env["uid"], env["gid"])
                )
                db.execute(
                    "UPDATE environments SET status=?,observed_at=? WHERE id=?",
                    ("READY" if ready else "UNAVAILABLE", now, env["id"]),
                )
            after = {
                row["id"]: row["status"]
                for row in db.execute(
                    "SELECT id,status FROM environments WHERE node_id=? AND container_id IS NOT NULL "
                    "AND id NOT IN (SELECT environment_id FROM local_environments)",
                    (node_id,),
                )
            }
            if recovered or before != after:
                changed.update(self._schedule(db))
        self.jobs_changed(changed)
        return {"accepted": True}

    def provision_destination(self, user_id, secret_ref, channel_id, *, actor=None):
        """Called only after the API has verified the owner, guild and channel."""
        with self.store.transaction() as db:
            if not db.execute(
                "SELECT 1 FROM principals WHERE id=? AND role='user'", (user_id,)
            ).fetchone():
                raise Error("NOT_FOUND", "User does not exist", 404)
            before = db.execute(
                "SELECT channel_id FROM notification_destinations "
                "WHERE user_id=? AND enabled=1 ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            # Destinations are immutable: existing jobs keep their original route.
            cursor = db.execute(
                """INSERT INTO notification_destinations(user_id,secret_ref,channel_id,created_at)
                VALUES(?,?,?,?) RETURNING id""",
                (user_id, secret_ref, channel_id, self.clock()),
            )
            result = {"id": cursor.fetchone()["id"], "channel_id": channel_id, "configured": True}
            if actor:
                from .server_management import audit

                audit(
                    db,
                    self,
                    actor,
                    "notifications.update",
                    user_id,
                    dict(before) if before else {},
                    {"channel_id": channel_id},
                )
            return result

    def notification_status(self, user_id):
        with self.store.transaction(write=False) as db:
            row = db.execute(
                """SELECT id,channel_id FROM notification_destinations
                WHERE user_id=? AND enabled=1 ORDER BY id DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        return {"configured": bool(row), **(dict(row) if row else {})}

    def _validate_spec(self, db, user_id, spec, *, local=False):
        for env_id in spec.environment_ids:
            env = db.execute("SELECT * FROM environments WHERE id=?", (env_id,)).fetchone()
            if not env or env["user_id"] != user_id:
                raise Error("NOT_FOUND", "A requested environment is not yours", 404)
            if not self._allowed(db, user_id, env["node_id"]):
                raise Error("FORBIDDEN_NODE", "A requested server is not allowed", 403)
            if not env["container_id"]:
                raise Error(
                    "ENVIRONMENT_NOT_VERIFIED", "Complete environment verification first", 409
                )
            registered = db.execute(
                "SELECT approved FROM local_environments WHERE environment_id=?", (env_id,)
            ).fetchone()
            if registered and not registered["approved"]:
                raise Error("ENVIRONMENT_NOT_APPROVED", "Confirm the environment first", 409)
            if local is False and registered:
                raise Error("LOCAL_RUNNER_REQUIRED", "Submit from the local execution script", 409)
            self._require_identity(db, user_id, env["uid"], env["gid"])

    def _usage(self, db):
        usage = {"node": {}, "env": {}, "gpu": set()}
        for row in db.execute(
            "SELECT * FROM jobs WHERE state IN ('DISPATCHING','RUNNING','UNKNOWN')"
        ):
            self._account(
                usage,
                row["node_id"],
                row["environment_id"],
                row["cpus"],
                row["memory_mib"],
                json.loads(row["gpu_ids"]),
            )
        return usage

    @staticmethod
    def _account(usage, node, env, cpus, memory, gpu_ids):
        for category, key in (("node", node), ("env", env)):
            used = usage[category].setdefault(key, [0, 0])
            used[0] += cpus
            used[1] += memory
        usage["gpu"].update((node, gpu) for gpu in gpu_ids)

    def _candidate(self, db, user_id, spec, usage, *, potential=False):
        for env_id in spec.environment_ids:
            env = db.execute("SELECT * FROM environments WHERE id=?", (env_id,)).fetchone()
            if (
                not env
                or env["user_id"] != user_id
                or not env["container_id"]
                or not self._allowed(db, user_id, env["node_id"])
                or not self._identity_matches(db, user_id, env["uid"], env["gid"])
            ):
                continue
            node = db.execute("SELECT * FROM nodes WHERE id=?", (env["node_id"],)).fetchone()
            if not node["enabled"]:
                continue
            local = db.execute(
                "SELECT approved FROM local_environments WHERE environment_id=?", (env_id,)
            ).fetchone()
            if local and not local["approved"]:
                continue
            if not potential and (
                env["status"] != "READY"
                or (
                    not local
                    and (
                        node["last_seen"] is None
                        or node["last_seen"] <= self.clock() - self.heartbeat_timeout
                    )
                )
            ):
                continue
            nu = [0, 0] if potential else usage["node"].get(node["id"], [0, 0])
            eu = [0, 0] if potential else usage["env"].get(env_id, [0, 0])
            if spec.cpus > min(node["cpus"] - nu[0], env["cpus"] - eu[0]) or spec.memory_mib > min(
                node["memory_mib"] - nu[1], env["memory_mib"] - eu[1]
            ):
                continue
            visible = set(json.loads(env["gpu_ids"]))
            gpus = [
                g["id"]
                for g in json.loads(node["gpus"])
                if g["id"] in visible
                and (potential or (node["id"], g["id"]) not in usage["gpu"])
                and (not spec.gpu_model or g["model"] == spec.gpu_model)
                and g["memory_mib"] >= spec.gpu_min_memory_mib
            ]
            if len(gpus) >= spec.gpu_count:
                return {
                    "node_id": node["id"],
                    "environment_id": env_id,
                    "gpu_ids": sorted(gpus)[: spec.gpu_count],
                }
        return None

    def plan(self, user_id, request: PlanRequest):
        with self.store.transaction(write=False) as db:
            usage = self._usage(db)
            # Simulate older queued jobs first, without writing any reservation.
            for job in db.execute("SELECT * FROM jobs WHERE state='QUEUED' ORDER BY seq"):
                run = db.execute(
                    "SELECT last_seen FROM local_runs WHERE job_id=?", (job["id"],)
                ).fetchone()
                if run and run["last_seen"] <= self.clock() - self.heartbeat_timeout:
                    continue
                spec = JobSpec.model_validate_json(job["spec"])
                target = self._candidate(db, job["user_id"], spec, usage)
                if target:
                    self._account(
                        usage,
                        target["node_id"],
                        target["environment_id"],
                        spec.cpus,
                        spec.memory_mib,
                        target["gpu_ids"],
                    )
            profiles = []
            for spec in [request.primary, *request.alternatives]:
                self._validate_spec(db, user_id, spec, local=True)
                possible = self._candidate(db, user_id, spec, usage, potential=True)
                target = self._candidate(db, user_id, spec, usage)
                profiles.append(
                    {
                        "spec": spec.model_dump(),
                        "can_start_now": bool(target),
                        "possible": bool(possible),
                        "assignment": target,
                        "reason": None
                        if target
                        else (
                            "WAITING_FOR_RESOURCES_OR_ENVIRONMENT" if possible else "UNSATISFIABLE"
                        ),
                    }
                )
        return {"profiles": profiles, "reservation_created": False}

    def submit(self, user_id, request: JobSubmit, *, runner=None):
        request_hash = digest(encode(request.model_dump()))
        unavailable = False
        with self.store.transaction() as db:
            previous = db.execute(
                "SELECT * FROM jobs WHERE user_id=? AND request_key=?",
                (user_id, request.request_key),
            ).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    legacy = request.model_dump()
                    if legacy["spec"].get("workdir") is None:
                        legacy["spec"].pop("workdir", None)
                    if previous["request_hash"] != digest(encode(legacy)):
                        raise Error(
                            "IDEMPOTENCY_CONFLICT", "This request key has different content", 409
                        )
                run = db.execute(
                    "SELECT runner_id FROM local_runs WHERE job_id=?", (previous["id"],)
                ).fetchone()
                if bool(run) != bool(runner) or (run and run["runner_id"] != runner.runner_id):
                    raise Error("IDEMPOTENCY_CONFLICT", "Request belongs to another runner", 409)
                if runner:
                    self.local.validate(db, user_id, request.spec.environment_ids[0], runner)
                return self._job_view(db, previous)
            self._validate_spec(db, user_id, request.spec, local=bool(runner))
            if runner:
                if len(request.spec.environment_ids) != 1:
                    raise Error("INVALID_REQUEST", "Local submission needs exactly one environment")
                self.local.validate(db, user_id, request.spec.environment_ids[0], runner)
            destination = db.execute(
                """SELECT id FROM notification_destinations WHERE user_id=? AND enabled=1
                ORDER BY id DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
            if not destination:
                raise Error(
                    "NOTIFICATION_NOT_CONFIGURED", "Configure your Discord destination first", 409
                )
            if not self._candidate(db, user_id, request.spec, self._usage(db), potential=True):
                raise Error(
                    "UNSATISFIABLE", "No selected environment can fit this resource request", 409
                )
            # Existing feasible submissions always go first.
            changed = self._schedule(db)
            target = self._candidate(db, user_id, request.spec, self._usage(db))
            if not target and request.mode == "start_if_available":
                unavailable = True
            else:
                job_id = new_id()
                db.execute(
                    """INSERT INTO jobs(id,user_id,request_key,request_hash,spec,state,cpus,memory_mib,
                    notification_id,created_at) VALUES(?,?,?,?,?,'QUEUED',?,?,?,?)""",
                    (
                        job_id,
                        user_id,
                        request.request_key,
                        request_hash,
                        encode(request.spec.model_dump()),
                        request.spec.cpus,
                        request.spec.memory_mib,
                        destination["id"],
                        self.clock(),
                    ),
                )
                if runner:
                    db.execute(
                        "INSERT INTO local_runs(job_id,runner_id,last_seen) VALUES(?,?,?)",
                        (job_id, runner.runner_id, self.clock()),
                    )
                changed.update(self._schedule(db))
                result = self._job_view(
                    db, db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                )
        self.jobs_changed(changed)
        if unavailable:
            raise Error(
                "CAPACITY_UNAVAILABLE", "Capacity changed; nothing was queued for this request", 409
            )
        return result

    def _expire(self, db):
        changed = {
            r[0]
            for r in db.execute(
                """UPDATE jobs SET state='UNKNOWN',reason='WORKER_OFFLINE'
            WHERE state IN ('DISPATCHING','RUNNING') AND node_id IN
            (SELECT id FROM nodes WHERE last_seen IS NULL OR last_seen<=?)
            AND id NOT IN (SELECT job_id FROM local_runs) RETURNING id""",
                (self.clock() - self.heartbeat_timeout,),
            )
        }
        changed.update(
            r[0]
            for r in db.execute(
                """UPDATE jobs SET state='UNKNOWN',reason='RUNNER_OFFLINE'
            WHERE state IN ('DISPATCHING','RUNNING') AND id IN
            (SELECT job_id FROM local_runs WHERE last_seen<=?) RETURNING id""",
                (self.clock() - self.heartbeat_timeout,),
            )
        )
        changed.update(
            r[0]
            for r in db.execute(
                """UPDATE jobs SET reason='RUNNER_OFFLINE'
                WHERE state='QUEUED' AND COALESCE(reason,'')<>'RUNNER_OFFLINE'
                AND id IN (SELECT job_id FROM local_runs WHERE last_seen<=?) RETURNING id""",
                (self.clock() - self.heartbeat_timeout,),
            )
        )
        return changed

    def next_liveness_deadline(self):
        """Read only timestamps that can still cause a state/reason transition."""
        with self.store.transaction(write=False) as db:
            seen = db.execute(
                """SELECT MIN(last_seen) FROM (
                    SELECT r.last_seen FROM local_runs r JOIN jobs j ON j.id=r.job_id
                    WHERE j.state IN ('DISPATCHING','RUNNING')
                       OR (j.state='QUEUED' AND COALESCE(j.reason,'')<>'RUNNER_OFFLINE')
                    UNION ALL
                    SELECT COALESCE(n.last_seen,0) AS last_seen FROM jobs j
                    JOIN nodes n ON n.id=j.node_id
                    WHERE j.state IN ('DISPATCHING','RUNNING')
                    AND NOT EXISTS (SELECT 1 FROM local_runs r WHERE r.job_id=j.id)
                ) AS live_jobs"""
            ).fetchone()[0]
        return None if seen is None else seen + self.heartbeat_timeout

    def expire(self):
        """Silence changes liveness, never releases reservations or scans for assignments."""
        with self.store.transaction() as db:
            changed = self._expire(db)
        self.jobs_changed(changed)

    def _schedule(self, db):
        changed = self._expire(db)
        usage = self._usage(db)
        for job in db.execute("SELECT * FROM jobs WHERE state='QUEUED' ORDER BY seq"):
            run = db.execute(
                "SELECT last_seen FROM local_runs WHERE job_id=?", (job["id"],)
            ).fetchone()
            if run and run["last_seen"] <= self.clock() - self.heartbeat_timeout:
                if job["reason"] != "RUNNER_OFFLINE":
                    db.execute("UPDATE jobs SET reason='RUNNER_OFFLINE' WHERE id=?", (job["id"],))
                    changed.add(job["id"])
                continue
            spec = JobSpec.model_validate_json(job["spec"])
            target = self._candidate(db, job["user_id"], spec, usage)
            if not target:
                if job["reason"] != "WAITING_FOR_RESOURCES_OR_ENVIRONMENT":
                    db.execute(
                        "UPDATE jobs SET reason='WAITING_FOR_RESOURCES_OR_ENVIRONMENT' WHERE id=?",
                        (job["id"],),
                    )
                    changed.add(job["id"])
                continue
            db.execute(
                """UPDATE jobs SET state='DISPATCHING',reason=NULL,node_id=?,environment_id=?,
                execution_id=?,gpu_ids=? WHERE id=?""",
                (
                    target["node_id"],
                    target["environment_id"],
                    new_id(),
                    encode(target["gpu_ids"]),
                    job["id"],
                ),
            )
            db.executemany(
                "INSERT INTO gpu_reservations VALUES(?,?,?)",
                [(target["node_id"], gpu, job["id"]) for gpu in target["gpu_ids"]],
            )
            self._account(
                usage,
                target["node_id"],
                target["environment_id"],
                spec.cpus,
                spec.memory_mib,
                target["gpu_ids"],
            )
            changed.add(job["id"])
        return changed

    def tick(self):
        with self.store.transaction() as db:
            changed = self._schedule(db)
        self.jobs_changed(changed)

    def _job_view(self, db, row):
        result = dict(row)
        for key in ("request_hash", "notification_id"):
            result.pop(key)
        result["spec"] = json.loads(result["spec"])
        result["gpu_ids"] = json.loads(result["gpu_ids"])
        result["cancel_requested"] = bool(result["cancel_requested"])
        result["notifications"] = [
            dict(r)
            for r in db.execute(
                "SELECT kind,state,attempts,error_code FROM notifications WHERE job_id=? ORDER BY id",
                (row["id"],),
            )
        ]
        return result

    def get_job(self, user_id, job_id):
        with self.store.transaction(write=False) as db:
            row = self._owned_job(db, user_id, job_id)
            return self._job_view(db, row)

    @staticmethod
    def _owned_job(db, user_id, job_id):
        row = db.execute(
            "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
        ).fetchone()
        if not row:
            raise Error("NOT_FOUND", "Job does not exist", 404)
        return row

    def list_jobs(self, user_id, limit=100, after=0):
        with self.store.transaction(write=False) as db:
            return [
                self._job_view(db, r)
                for r in db.execute(
                    "SELECT * FROM jobs WHERE user_id=? AND seq>? ORDER BY seq LIMIT ?",
                    (user_id, after, limit),
                )
            ]

    def assignments(self, node_id, limit=100, after=0):
        with self.store.transaction(write=False) as db:
            result = []
            for row in db.execute(
                """SELECT * FROM jobs WHERE node_id=? AND seq>? AND state IN
                ('DISPATCHING','RUNNING','UNKNOWN') AND id NOT IN (SELECT job_id FROM local_runs)
                ORDER BY seq LIMIT ?""",
                (node_id, after, limit),
            ):
                env = db.execute(
                    "SELECT * FROM environments WHERE id=?", (row["environment_id"],)
                ).fetchone()
                result.append(
                    {
                        "job_id": row["id"],
                        "seq": row["seq"],
                        "execution_id": row["execution_id"],
                        "state": row["state"],
                        "cancel_requested": bool(row["cancel_requested"]),
                        "spec": json.loads(row["spec"]),
                        "gpu_ids": json.loads(row["gpu_ids"]),
                        "environment": {
                            k: env[k] for k in ("id", "container_id", "uid", "gid", "workdir")
                        },
                    }
                )
        return result

    def cancel(self, user_id, job_id):
        changed = set()
        with self.store.transaction() as db:
            row = self._owned_job(db, user_id, job_id)
            if row["state"] not in TERMINAL:
                if not row["cancel_requested"]:
                    db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
                    changed.add(job_id)
                if row["state"] == "QUEUED":
                    db.execute(
                        "UPDATE jobs SET state='CANCELLED',finished_at=?,reason=NULL WHERE id=?",
                        (self.clock(), job_id),
                    )
                    self._notify(db, job_id, "finished")
                changed.update(self._schedule(db))
            result = self._job_view(
                db, db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            )
        self.jobs_changed(changed)
        return result

    def event(self, node_id, job_id, event: WorkerEvent, *, user_id=None, runner=None):
        payload_hash = digest(encode({"job_id": job_id, **event.model_dump()}))
        changed = set()
        with self.store.transaction() as db:
            if runner:
                owned, run, env = self.local.owned(db, user_id, job_id, runner)
                if run["claimed"] and run["claim_id"] != runner.claim_id:
                    raise Error("INVALID_CLAIM", "Report belongs to a different claim attempt", 409)
                if not run["claimed"] and event.kind != "not_started":
                    raise Error(
                        "EXECUTION_NOT_GRANTED", "Runner has not claimed this execution", 409
                    )
                node_id = env["node_id"]
            elif db.execute("SELECT 1 FROM local_runs WHERE job_id=?", (job_id,)).fetchone():
                raise Error("FORBIDDEN", "Local jobs require their owning runner", 403)
            row = db.execute(
                "SELECT * FROM jobs WHERE id=? AND node_id=?", (job_id, node_id)
            ).fetchone()
            if not row or row["execution_id"] != event.execution_id:
                raise Error("INVALID_EXECUTION", "This execution is not assigned to your node", 404)
            previous = db.execute(
                "SELECT * FROM worker_events WHERE node_id=? AND event_id=?",
                (node_id, event.event_id),
            ).fetchone()
            if previous:
                if previous["payload_hash"] != payload_hash:
                    raise Error("EVENT_CONFLICT", "Event ID was reused with different content", 409)
                return {"accepted": True, "duplicate": True}
            if event.occurred_at > self.clock() + 300:
                raise Error("INVALID_EVENT_TIME", "Event timestamp is too far in the future")
            if event.kind == "started":
                if row["reason"] == "NOT_STARTED":
                    raise Error(
                        "EVENT_CONFLICT",
                        "Worker already confirmed this execution never started",
                        409,
                    )
                if row["started_at"] is None:
                    if row["finished_at"] is not None and event.occurred_at > row["finished_at"]:
                        raise Error("EVENT_CONFLICT", "Start time is later than completion", 409)
                    db.execute(
                        "UPDATE jobs SET started_at=? WHERE id=?", (event.occurred_at, job_id)
                    )
                    self._notify(db, job_id, "started")
                    changed.add(job_id)
                if row["state"] not in TERMINAL and row["state"] != "RUNNING":
                    db.execute("UPDATE jobs SET state='RUNNING',reason=NULL WHERE id=?", (job_id,))
                    changed.add(job_id)
            else:
                state = {
                    "succeeded": "SUCCEEDED",
                    "failed": "FAILED",
                    "cancelled": "CANCELLED",
                    "not_started": "CANCELLED" if row["cancel_requested"] else "FAILED",
                }[event.kind]
                if row["state"] in TERMINAL:
                    if row["state"] != state or row["exit_code"] != event.exit_code:
                        raise Error(
                            "EVENT_CONFLICT", "The execution already has a different outcome", 409
                        )
                else:
                    if row["started_at"] is not None and event.occurred_at < row["started_at"]:
                        raise Error("EVENT_CONFLICT", "Completion time precedes start", 409)
                    if event.kind == "not_started" and row["started_at"] is not None:
                        raise Error("EVENT_CONFLICT", "Execution was already observed running", 409)
                    db.execute(
                        """UPDATE jobs SET state=?,reason=?,finished_at=?,exit_code=?,log_path=?,result_path=? WHERE id=?""",
                        (
                            state,
                            "NOT_STARTED" if event.kind == "not_started" else None,
                            event.occurred_at,
                            event.exit_code,
                            event.log_path,
                            event.result_path,
                            job_id,
                        ),
                    )
                    db.execute("DELETE FROM gpu_reservations WHERE job_id=?", (job_id,))
                    self._notify(db, job_id, "finished")
                    changed.add(job_id)
            db.execute(
                "INSERT INTO worker_events VALUES(?,?,?)", (node_id, event.event_id, payload_hash)
            )
            if event.kind != "started" and row["state"] not in TERMINAL:
                changed.update(self._schedule(db))
            else:
                changed.update(self._expire(db))
        self.jobs_changed(changed)
        return {"accepted": True, "duplicate": False}

    def _notify(self, db, job_id, kind):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        spec = json.loads(row["spec"])
        payload = {
            "job_id": job_id,
            "name": spec["name"],
            "kind": kind,
            "execution_id": row["execution_id"],
            "environment_id": row["environment_id"],
            "state": "RUNNING" if kind == "started" else row["state"],
            "node_id": row["node_id"],
            "cpus": row["cpus"],
            "memory_mib": row["memory_mib"],
            "gpu_ids": json.loads(row["gpu_ids"]),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "exit_code": row["exit_code"],
        }
        # Commands, secrets and raw logs are deliberately absent from Discord payloads.
        state = "PENDING"
        if (
            kind == "started"
            and db.execute(
                "SELECT 1 FROM notifications WHERE job_id=? AND kind='finished' AND state IN ('SENDING','SENT')",
                (job_id,),
            ).fetchone()
        ):
            state = "SUPPRESSED"
        db.execute(
            """INSERT INTO notifications(job_id,kind,destination_id,payload,state,next_attempt)
            VALUES(?,?,?,?,?,?) ON CONFLICT(job_id,kind) DO NOTHING""",
            (job_id, kind, row["notification_id"], encode(payload), state, self.clock()),
        )

    def cluster(self, user_id):
        with self.store.transaction(write=False) as db:
            usage = self._usage(db)
            result = []
            for node in db.execute(
                "SELECT n.* FROM nodes n JOIN grants g ON n.id=g.node_id WHERE g.user_id=? ORDER BY n.id",
                (user_id,),
            ):
                used = usage["node"].get(node["id"], [0, 0])
                result.append(
                    {
                        "id": node["id"],
                        "online": bool(
                            node["enabled"]
                            and (
                                (
                                    node["last_seen"] is not None
                                    and node["last_seen"] >= self.clock() - self.heartbeat_timeout
                                )
                                or self.local.is_online(db, node["id"], user_id)
                            )
                        ),
                        "cpus": node["cpus"],
                        "memory_mib": node["memory_mib"],
                        "reserved_cpus": used[0],
                        "reserved_memory_mib": used[1],
                        "gpus": [
                            {**gpu, "reserved": (node["id"], gpu["id"]) in usage["gpu"]}
                            for gpu in json.loads(node["gpus"])
                        ],
                    }
                )
            return result
