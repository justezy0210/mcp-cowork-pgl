"""The hub API. Run one Uvicorn process per database."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .connector_models import (
    ConnectorCreate,
    ConnectorPoll,
    RegistrationCreate,
    RegistrationResult,
    TokenCreate,
)
from .models import (
    EnvironmentCreate,
    EnvironmentVerification,
    Error,
    Grants,
    Heartbeat,
    JobSubmit,
    LocalEnvironment,
    LocalEvent,
    LocalSubmit,
    NodeCreate,
    NotificationProvision,
    NotificationSet,
    PlanRequest,
    RunnerIdentity,
    UserCreate,
    UserIdentity,
    WorkerEvent,
)
from .notifications import Destinations, Notifier
from .service import Hub, new_id

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


class BodyLimit:
    def __init__(self, app, limit=262144):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        data = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            data.extend(message.get("body", b""))
            if len(data) > self.limit:
                return await JSONResponse(
                    {"error": {"code": "REQUEST_TOO_LARGE"}}, status_code=413
                )(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(data), "more_body": False}

        await self.app(scope, bounded_receive, send)


def create_app(
    hub: Hub,
    destinations: Destinations | None = None,
    *,
    background=True,
    web_config=None,
    web_verify=None,
):
    changed = asyncio.Event()
    revision = new_id()
    notifier = Notifier(hub, destinations) if destinations else None

    def wake():
        nonlocal changed, revision
        revision = new_id()
        old, changed = changed, asyncio.Event()
        old.set()

    async def scheduling_loop():
        while True:
            try:
                await asyncio.to_thread(hub.tick)
                wake()
            except Exception:
                logger.error("Scheduler tick failed; will retry")
            await asyncio.sleep(1)

    async def notification_loop():
        while True:
            try:
                sent = await asyncio.to_thread(notifier.step)
            except Exception:
                logger.error("Notification delivery failed; will retry")
                sent = False
            await asyncio.sleep(0.05 if sent else 1)

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        if background:
            tasks.append(asyncio.create_task(scheduling_loop()))
            if notifier:
                tasks.append(asyncio.create_task(notification_loop()))
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if destinations:
            destinations.close()

    app = FastAPI(title="cowork-hub", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodyLimit)
    from .web_api import mount_web

    mount_web(app, hub, web_config, web_verify)

    @app.exception_handler(Error)
    async def hub_error(request: Request, exc: Error):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo argv, headers, challenge values, or input data in errors.
        return JSONResponse(
            {
                "error": {
                    "code": "INVALID_REQUEST",
                    "fields": [
                        {"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()
                    ],
                }
            },
            status_code=422,
        )

    def role(expected):
        def dependency(credential: HTTPAuthorizationCredentials | None = Depends(bearer)):
            if not credential:
                raise Error("UNAUTHENTICATED", "A bearer token is required", 401)
            principal = hub.authenticate(credential.credentials)
            if principal["role"] != expected:
                raise Error("FORBIDDEN", "This credential cannot use this endpoint", 403)
            return principal

        return dependency

    admin, user, worker = role("admin"), role("user"), role("worker")

    async def mutate(call, *args):
        try:
            return await asyncio.to_thread(call, *args)
        finally:
            wake()

    @app.get("/healthz")
    def health():
        with hub.store.transaction(write=False) as db:
            db.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/v1/capabilities")
    def capabilities():
        return {"local_runner": 1, "connector": 1, "personal_tokens": 1}

    @app.post("/v1/tokens", status_code=201)
    def issue_token(body: TokenCreate, principal=Depends(user)):
        return JSONResponse(
            hub.management.create_token(principal["id"], body.name),
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/tokens")
    def tokens(
        limit: int = Query(100, ge=1, le=100),
        after: str = Query("", max_length=80),
        principal=Depends(user),
    ):
        return hub.management.list_tokens(principal["id"], limit, after)

    @app.delete("/v1/tokens/{token_id}")
    def revoke_token(token_id: str, principal=Depends(user)):
        return hub.management.revoke_token(principal["id"], token_id)

    @app.post("/v1/connectors", status_code=201)
    def register_connector(body: ConnectorCreate, principal=Depends(user)):
        return hub.management.register_connector(principal["id"], body)

    @app.get("/v1/connectors")
    def connectors(
        limit: int = Query(100, ge=1, le=100),
        after: str = Query("", max_length=80),
        principal=Depends(user),
    ):
        return hub.management.list_connectors(principal["id"], limit, after)

    @app.post("/v1/registration-requests", status_code=201)
    async def request_registration(body: RegistrationCreate, principal=Depends(user)):
        return await mutate(hub.management.request_registration, principal["id"], body)

    @app.get("/v1/registration-requests")
    def registrations(
        limit: int = Query(100, ge=1, le=100),
        after: str = Query("", max_length=80),
        principal=Depends(user),
    ):
        return hub.management.list_requests(principal["id"], limit, after)

    @app.post("/v1/connectors/{connector_id}/poll")
    async def connector_poll(connector_id: str, body: ConnectorPoll, principal=Depends(user)):
        return await mutate(hub.management.poll, principal["id"], connector_id, body)

    @app.post("/v1/connectors/{connector_id}/requests/{request_id}/result")
    async def connector_result(
        connector_id: str, request_id: str, body: RegistrationResult, principal=Depends(user)
    ):
        return await mutate(
            hub.management.complete, principal["id"], connector_id, request_id, body
        )

    @app.get("/v1/admin/local/environments/pending")
    def pending_environments(
        limit: int = Query(100, ge=1, le=100),
        after: str = Query("", max_length=80),
        principal=Depends(admin),
    ):
        return hub.management.pending_environments(limit, after)

    @app.post("/v1/local/environments", status_code=201)
    async def local_register(body: LocalEnvironment, principal=Depends(user)):
        return await mutate(hub.local.register, principal["id"], body)

    @app.post("/v1/admin/local/environments/{env_id}/approve")
    async def local_approve(env_id: str, principal=Depends(admin)):
        return await mutate(hub.local.approve, env_id)

    @app.post("/v1/local/jobs", status_code=201)
    async def local_submit(body: LocalSubmit, principal=Depends(user)):
        def submit():
            return hub.submit(
                principal["id"],
                JobSubmit.model_validate(body.model_dump(exclude={"runner"})),
                runner=body.runner,
            )

        return await mutate(submit)

    @app.post("/v1/local/jobs/{job_id}/poll")
    async def local_poll(job_id: str, body: RunnerIdentity, principal=Depends(user)):
        # Poll refreshes only this job's liveness. Another runner cannot hide its failure.
        return await mutate(hub.local.poll, principal["id"], job_id, body)

    @app.post("/v1/local/jobs/{job_id}/claim")
    async def local_claim(job_id: str, body: RunnerIdentity, principal=Depends(user)):
        return await mutate(hub.local.claim, principal["id"], job_id, body)

    @app.post("/v1/local/jobs/{job_id}/events")
    async def local_event(job_id: str, body: LocalEvent, principal=Depends(user)):
        return await mutate(hub.local.event, principal["id"], job_id, body)

    @app.post("/v1/admin/nodes", status_code=201)
    def create_node(body: NodeCreate, principal=Depends(admin)):
        return hub.create_node(body)

    @app.post("/v1/admin/users", status_code=201)
    def create_user(body: UserCreate, principal=Depends(admin)):
        return hub.create_user(body)

    @app.put("/v1/admin/users/{user_id}/grants")
    async def grants(user_id: str, body: Grants, principal=Depends(admin)):
        await mutate(hub.set_grants, user_id, body.allowed_nodes)
        return {"updated": True}

    @app.put("/v1/admin/users/{user_id}/identity")
    async def identity(user_id: str, body: UserIdentity, principal=Depends(admin)):
        return await mutate(hub.set_user_identity, user_id, body)

    @app.get("/v1/identity")
    def get_identity(principal=Depends(user)):
        return hub.user_identity(principal["id"])

    def provision(user_id, secret_ref, channel_id):
        if not destinations:
            raise Error("DESTINATION_UNAVAILABLE", "Discord secrets have not been configured", 409)
        destinations.verify(user_id, secret_ref, channel_id)
        return hub.provision_destination(user_id, secret_ref, channel_id)

    @app.post("/v1/admin/notifications")
    def admin_destination(body: NotificationProvision, principal=Depends(admin)):
        return provision(body.user_id, body.secret_ref, body.channel_id)

    @app.put("/v1/notifications")
    def set_destination(body: NotificationSet, principal=Depends(user)):
        return provision(principal["id"], body.secret_ref, body.channel_id)

    @app.get("/v1/notifications")
    def get_destination(principal=Depends(user)):
        return hub.notification_status(principal["id"])

    @app.post("/v1/environments", status_code=201)
    def register(body: EnvironmentCreate, principal=Depends(user)):
        return hub.register_environment(principal["id"], body)

    @app.get("/v1/environments")
    def environments(
        limit: int = Query(100, ge=1, le=100),
        after: str = Query("", max_length=80),
        principal=Depends(user),
    ):
        return hub.list_environments(principal["id"], limit, after)

    @app.get("/v1/cluster")
    def cluster(principal=Depends(user)):
        return hub.cluster(principal["id"])

    @app.post("/v1/jobs/plan")
    def plan(body: PlanRequest, principal=Depends(user)):
        return hub.plan(principal["id"], body)

    @app.post("/v1/jobs", status_code=201)
    async def submit(body: JobSubmit, principal=Depends(user)):
        return await mutate(hub.submit, principal["id"], body)

    @app.get("/v1/jobs")
    def jobs(
        limit: int = Query(100, ge=1, le=100), after: int = Query(0, ge=0), principal=Depends(user)
    ):
        return hub.list_jobs(principal["id"], limit, after)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, principal=Depends(user)):
        return hub.get_job(principal["id"], job_id)

    @app.post("/v1/jobs/{job_id}/cancel")
    async def cancel(job_id: str, principal=Depends(user)):
        return await mutate(hub.cancel, principal["id"], job_id)

    @app.post("/v1/worker/environments/{env_id}/verify")
    async def verify(env_id: str, body: EnvironmentVerification, principal=Depends(worker)):
        return await mutate(hub.verify_environment, principal["node_id"], env_id, body)

    @app.post("/v1/worker/heartbeat")
    async def heartbeat(body: Heartbeat, principal=Depends(worker)):
        return await mutate(hub.heartbeat, principal["node_id"], body)

    @app.get("/v1/worker/assignments")
    async def assignments(
        since: str | None = Query(None, max_length=80),
        wait_seconds: int = Query(0, ge=0, le=25),
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100),
        principal=Depends(worker),
    ):
        event = changed
        if since == revision and wait_seconds:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), wait_seconds)
        current = revision
        items = await asyncio.to_thread(hub.assignments, principal["node_id"], limit, after)
        return {"revision": current, "assignments": items}

    @app.post("/v1/worker/jobs/{job_id}/events")
    async def worker_event(job_id: str, body: WorkerEvent, principal=Depends(worker)):
        return await mutate(hub.event, principal["node_id"], job_id, body)

    return app
