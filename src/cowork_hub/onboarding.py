"""First-use account details, held for administrator review before granting access."""

import threading

from pydantic import Field, SecretStr

from .models import Error, Identifier, UserIdentity
from .notifications import enqueue_enrollment
from .server_management import ReviewRequest, audit, page_rows
from .service import new_id


class Enrollment(UserIdentity):
    account_name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    webhook_url: SecretStr = Field(min_length=1, max_length=512)


class EnrollmentReview(ReviewRequest):
    allowed_nodes: list[Identifier] = Field(default_factory=list, max_length=256)


class Onboarding:
    def __init__(self, hub, config, destinations):
        self.hub, self.config, self.destinations = hub, config, destinations
        self.lock = threading.Lock()

    @staticmethod
    def view(row):
        # Webhook URLs, secret references and Firebase subjects never leave this service.
        return {
            key: row[key]
            for key in (
                "id",
                "email",
                "account_name",
                "uid",
                "gid",
                "channel_id",
                "state",
                "created_at",
                "reviewed_at",
            )
        }

    def _linked(self, db, subject):
        return (
            subject in self.config.users
            or db.execute("SELECT 1 FROM web_accounts WHERE firebase_uid=?", (subject,)).fetchone()
        )

    def status(self, identity):
        with self.hub.store.transaction(write=False) as db:
            if self._linked(db, identity["firebase_uid"]):
                return {"state": "LINKED"}
            row = db.execute(
                "SELECT * FROM web_enrollments WHERE firebase_uid=?", (identity["firebase_uid"],)
            ).fetchone()
            return self.view(row) if row else {"state": "NEW"}

    def _check(self, db, identity, body):
        if self._linked(db, identity["firebase_uid"]):
            raise Error("ALREADY_EXISTS", "This Google account is already linked", 409)
        previous = db.execute(
            "SELECT * FROM web_enrollments WHERE firebase_uid=?", (identity["firebase_uid"],)
        ).fetchone()
        if previous and previous["state"] == "PENDING":
            if (previous["account_name"], previous["uid"], previous["gid"]) != (
                body.account_name,
                body.uid,
                body.gid,
            ):
                raise Error("REQUEST_PENDING", "Account review is already pending", 409)
            return previous
        if (
            body.account_name in self.config.admin_users
            or body.account_name in self.config.users.values()
            or db.execute("SELECT 1 FROM principals WHERE id=?", (body.account_name,)).fetchone()
            or db.execute(
                "SELECT 1 FROM web_enrollments WHERE state='PENDING' AND account_name=?",
                (body.account_name,),
            ).fetchone()
        ):
            raise Error("ALREADY_EXISTS", "Account name is already assigned", 409)
        if (
            db.execute("SELECT 1 FROM user_identities WHERE uid=?", (body.uid,)).fetchone()
            or db.execute(
                "SELECT 1 FROM web_enrollments WHERE state='PENDING' AND uid=?", (body.uid,)
            ).fetchone()
        ):
            raise Error("UID_ALREADY_ASSIGNED", "UID is already assigned", 409)
        return None

    def _release_rejected(self, subject):
        # A corrected account name must be able to reuse its own unapproved webhook.
        # Hold the DB write lock so account creation cannot race this private-file cleanup.
        with self.hub.store.transaction() as db:
            previous = db.execute(
                "SELECT * FROM web_enrollments WHERE firebase_uid=? AND state='REJECTED'",
                (subject,),
            ).fetchone()
            if (
                not previous
                or db.execute(
                    "SELECT 1 FROM principals WHERE id=?", (previous["account_name"],)
                ).fetchone()
            ):
                return
            ref = previous["secret_ref"]
            if (
                not db.execute(
                    "SELECT 1 FROM notification_destinations WHERE secret_ref=?", (ref,)
                ).fetchone()
                and not db.execute(
                    "SELECT 1 FROM web_enrollments WHERE secret_ref=? AND state='PENDING'", (ref,)
                ).fetchone()
            ):
                self.destinations.discard_pending(ref)

    def submit(self, identity, body):
        if not self.destinations or not self.destinations.registration_available:
            raise Error("DESTINATION_UNAVAILABLE", "Discord registration is unavailable", 409)
        # Serialize enrollment submissions, without holding the scheduling DB lock over HTTP.
        with self.lock:
            with self.hub.store.transaction(write=False) as db:
                previous = self._check(db, identity, body)
                if previous:
                    return self.view(previous)
            self._release_rejected(identity["firebase_uid"])
            ref, channel = self.destinations.register(
                body.account_name, body.webhook_url.get_secret_value()
            )
            with self.hub.store.transaction() as db:
                self._check(db, identity, body)
                db.execute(
                    "INSERT INTO web_enrollments"
                    "(id,firebase_uid,email,account_name,uid,gid,secret_ref,channel_id,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(firebase_uid) DO UPDATE SET "
                    "id=excluded.id,email=excluded.email,account_name=excluded.account_name,"
                    "uid=excluded.uid,gid=excluded.gid,secret_ref=excluded.secret_ref,"
                    "channel_id=excluded.channel_id,created_at=excluded.created_at,"
                    "state='PENDING',reviewed_at=NULL,reviewed_by=NULL",
                    (
                        new_id(),
                        identity["firebase_uid"],
                        identity["email"],
                        body.account_name,
                        body.uid,
                        body.gid,
                        ref,
                        channel,
                        self.hub.clock(),
                    ),
                )
                result = self.view(
                    db.execute(
                        "SELECT * FROM web_enrollments WHERE firebase_uid=?",
                        (identity["firebase_uid"],),
                    ).fetchone()
                )
                enqueue_enrollment(db, result, self.config)
                return result

    def pending(self, page, size):
        with self.hub.store.transaction(write=False) as db:
            result = page_rows(
                db,
                "SELECT * FROM web_enrollments WHERE state='PENDING'",
                (),
                page,
                size,
                order="created_at,id",
            )
            result["items"] = [self.view(row) for row in result["items"]]
            return result

    def review(self, actor, request_id, body):
        with self.hub.store.transaction(write=False) as db:
            row = db.execute("SELECT * FROM web_enrollments WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise Error("NOT_FOUND", "Account request does not exist", 404)
            if row["state"] != "PENDING":
                if row["state"] == body.decision:
                    return self.view(row)
                raise Error("REQUEST_REVIEWED", "Account request was already reviewed", 409)
        if body.decision == "APPROVED":
            if not body.allowed_nodes:
                raise Error("INVALID_REQUEST", "Select at least one available server", 422)
            if not self.destinations:
                raise Error("DESTINATION_UNAVAILABLE", "Discord registration is unavailable", 409)
            self.destinations.verify(row["account_name"], row["secret_ref"], row["channel_id"])
        with self.hub.store.transaction() as db:
            current = db.execute(
                "SELECT * FROM web_enrollments WHERE id=?", (request_id,)
            ).fetchone()
            if not current or current["state"] != "PENDING":
                raise Error("REQUEST_REVIEWED", "Account request changed; reload", 409)
            if body.decision == "APPROVED":
                # Recheck ownership in the same transaction as every account change.
                if (
                    self._linked(db, row["firebase_uid"])
                    or db.execute(
                        "SELECT 1 FROM principals WHERE id=?", (row["account_name"],)
                    ).fetchone()
                    or row["account_name"]
                    in (*self.config.admin_users, *self.config.users.values())
                ):
                    raise Error("ALREADY_EXISTS", "Account name or Google account is assigned", 409)
                self.hub._principal(db, row["account_name"], "user")
                self.hub._set_identity(
                    db, row["account_name"], UserIdentity(uid=row["uid"], gid=row["gid"])
                )
                self.hub._grants(db, row["account_name"], body.allowed_nodes)
                db.execute(
                    "INSERT INTO web_accounts VALUES(?,?,?)",
                    (row["firebase_uid"], row["account_name"], self.hub.clock()),
                )
                db.execute(
                    "INSERT INTO notification_destinations(user_id,secret_ref,channel_id,created_at) "
                    "VALUES(?,?,?,?)",
                    (row["account_name"], row["secret_ref"], row["channel_id"], self.hub.clock()),
                )
            db.execute(
                "UPDATE web_enrollments SET state=?,reviewed_at=?,reviewed_by=? WHERE id=?",
                (body.decision, self.hub.clock(), actor, request_id),
            )
            audit(
                db,
                self.hub,
                actor,
                "account.review",
                row["account_name"],
                {"state": "PENDING"},
                {
                    "state": body.decision,
                    "uid": row["uid"],
                    "gid": row["gid"],
                    "allowed_nodes": body.allowed_nodes if body.decision == "APPROVED" else [],
                    "channel_id": row["channel_id"],
                },
            )
            return self.view(
                db.execute("SELECT * FROM web_enrollments WHERE id=?", (request_id,)).fetchone()
            )
