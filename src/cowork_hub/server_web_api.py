"""Firebase-authenticated server management. No command submission endpoints."""

from typing import Annotated, Literal

from fastapi import Depends, Query
from pydantic import Field, SecretStr

from .connector_models import RegistrationCreate
from .models import Error, Identifier, Input
from .onboarding import EnrollmentReview
from .server_management import (
    AccessRequest,
    AccountCreate,
    GrantUpdate,
    NodeUpdate,
    ReviewRequest,
    ServerManagement,
)
from .ssh_input import ContainerConnect, SSHCommand, parse_ssh_command

Page = Annotated[int, Query(ge=1, le=100000)]
Size = Annotated[int, Query(ge=1, le=100)]


class WebhookSet(Input):
    webhook_url: SecretStr = Field(min_length=1, max_length=512)


def mount_servers(app, hub, config, user, *, destinations=None, onboarding=None):
    portal = ServerManagement(hub)

    def is_admin(principal):
        return bool(config and principal["user_id"] in config.admin_users)

    def admin(principal=Depends(user)):
        if not is_admin(principal):
            raise Error("FORBIDDEN", "Web administrator access is required", 403)
        return principal

    @app.get("/v1/web/profile")
    def profile(principal=Depends(user)):
        return portal.profile(principal["user_id"], is_admin(principal))

    @app.get("/v1/web/admin/enrollments")
    def enrollments(page: Page = 1, page_size: Size = 20, principal=Depends(admin)):
        return onboarding.pending(page, page_size)

    @app.post("/v1/web/admin/enrollments/{request_id}/review")
    def review_enrollment(request_id: str, body: EnrollmentReview, principal=Depends(admin)):
        return onboarding.review(principal["user_id"], request_id, body)

    @app.get("/v1/web/servers")
    def servers(principal=Depends(user)):
        return portal.servers(principal["user_id"])

    @app.get("/v1/web/servers/{node_id}/jobs")
    def server_jobs(
        node_id: str,
        group: Literal["active", "queued"] = "active",
        page: Page = 1,
        page_size: Size = 20,
        principal=Depends(user),
    ):
        return portal.server_jobs(principal["user_id"], node_id, group, page, page_size)

    @app.get("/v1/web/jobs")
    def jobs(
        page: Page = 1,
        page_size: Size = 20,
        include_completed: bool = False,
        node_id: Identifier | None = None,
        principal=Depends(user),
    ):
        return portal.jobs(
            principal["user_id"],
            page,
            page_size,
            include_completed=include_completed,
            node_id=node_id,
        )

    @app.get("/v1/web/environments")
    def environments(page: Page = 1, page_size: Size = 20, principal=Depends(user)):
        return portal.environments(principal["user_id"], page, page_size)

    @app.get("/v1/web/connectors")
    def connectors(principal=Depends(user)):
        return hub.management.list_connectors(principal["user_id"])

    @app.get("/v1/web/registrations")
    def registrations(page: Page = 1, page_size: Size = 20, principal=Depends(user)):
        return portal.registrations(principal["user_id"], page, page_size)

    @app.post("/v1/web/registrations", status_code=201)
    def register(body: RegistrationCreate, principal=Depends(user)):
        return hub.management.request_registration(principal["user_id"], body)

    @app.post("/v1/web/ssh/parse")
    def parse_ssh(body: SSHCommand, principal=Depends(user)):
        return parse_ssh_command(body.ssh_command).model_dump()

    @app.post("/v1/web/containers", status_code=201)
    def connect_container(body: ContainerConnect, principal=Depends(user)):
        target = parse_ssh_command(body.ssh_command)
        with hub.store.transaction(write=False) as db:
            connector = hub.management._connector(db, principal["user_id"], body.connector_id)
            main = db.execute(
                "SELECT workdir FROM environments WHERE id=? AND user_id=?",
                (connector["environment_id"], principal["user_id"]),
            ).fetchone()
            if not main:
                raise Error("NOT_FOUND", "Main environment is unavailable", 404)
        registration = RegistrationCreate(
            connector_id=body.connector_id,
            request_key=body.request_key,
            target={**target.model_dump(), "node_id": body.node_id, "workdir": main["workdir"]},
        )
        return hub.management.request_registration(principal["user_id"], registration)

    @app.get("/v1/web/access-requests")
    def access_requests(page: Page = 1, page_size: Size = 20, principal=Depends(user)):
        return portal.access_requests(principal["user_id"], page, page_size)

    @app.post("/v1/web/access-requests", status_code=201)
    def request_access(body: AccessRequest, principal=Depends(user)):
        return portal.request_access(principal["user_id"], body)

    @app.get("/v1/web/admin/access-requests")
    def admin_requests(page: Page = 1, page_size: Size = 20, principal=Depends(admin)):
        return portal.access_requests(None, page, page_size)

    @app.post("/v1/web/admin/access-requests/{request_id}/review")
    def review(request_id: str, body: ReviewRequest, principal=Depends(admin)):
        return portal.review_access(principal["user_id"], request_id, body.decision)

    @app.get("/v1/web/admin/users")
    def users(page: Page = 1, page_size: Size = 20, principal=Depends(admin)):
        return {
            **portal.users(page, page_size),
            "notification_registration_available": bool(
                destinations and destinations.registration_available
            ),
        }

    @app.post("/v1/web/admin/users/{user_id}/notifications")
    def set_notification(user_id: Identifier, body: WebhookSet, principal=Depends(admin)):
        if not destinations:
            raise Error("DESTINATION_UNAVAILABLE", "Discord registration is not configured", 409)
        # Check the target before sending a webhook to Discord or persisting a secret.
        with hub.store.transaction(write=False) as db:
            if not db.execute(
                "SELECT 1 FROM principals WHERE id=? AND role='user' AND enabled=1", (user_id,)
            ).fetchone():
                raise Error("NOT_FOUND", "User does not exist", 404)
        secret_ref, channel_id = destinations.register(user_id, body.webhook_url.get_secret_value())
        return hub.provision_destination(
            user_id, secret_ref, channel_id, actor=principal["user_id"]
        )

    @app.post("/v1/web/admin/users", status_code=201)
    def create_account(body: AccountCreate, principal=Depends(admin)):
        return portal.create_account(principal["user_id"], body, config)

    @app.post("/v1/web/admin/users/{user_id}/grants")
    def grants(user_id: str, body: GrantUpdate, principal=Depends(admin)):
        return portal.update_grants(principal["user_id"], user_id, body)

    @app.get("/v1/web/admin/servers")
    def all_servers(principal=Depends(admin)):
        return portal.servers()

    @app.post("/v1/web/admin/servers/{node_id}")
    def update_server(node_id: str, body: NodeUpdate, principal=Depends(admin)):
        return portal.update_node(principal["user_id"], node_id, body)

    @app.get("/v1/web/admin/environments/pending")
    def pending_environments(page: Page = 1, page_size: Size = 20, principal=Depends(admin)):
        return portal.environments(None, page, page_size, pending=True)

    @app.post("/v1/web/admin/environments/{environment_id}/approve")
    def approve_environment(environment_id: str, principal=Depends(admin)):
        return hub.local.approve(environment_id, actor=principal["user_id"])

    @app.get("/v1/web/admin/audit")
    def audit_log(page: Page = 1, page_size: Size = 20, principal=Depends(admin)):
        return portal.audit_log(page, page_size)
