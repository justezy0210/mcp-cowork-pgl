"""Discord destinations and a durable outbox, independent of resource scheduling."""

import json
import os
import re
import stat
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .models import Error

WEBHOOK = re.compile(r"https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9._-]+\Z")


class Destinations:
    def __init__(
        self,
        path: Path | None,
        guild_id: str | None,
        client=None,
        *,
        managed_path=None,
        additional_guild_ids=(),
    ):
        self.guild_id = guild_id
        self.additional_guild_ids = frozenset(
            value.strip() for value in additional_guild_ids if value.strip()
        )
        if any(not re.fullmatch(r"[0-9]{1,30}", value) for value in self.additional_guild_ids):
            raise ValueError("Additional Discord server IDs must be numeric")
        self.entries = {}
        self.managed_path = Path(managed_path) if managed_path else None
        self.managed_entries = {}
        self.lock = threading.Lock()
        self.client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
        if path:
            self.entries = self._read_entries(path)
        if self.managed_path:
            if path and Path(path).resolve() == self.managed_path.resolve():
                raise ValueError("Managed and administrator Discord files must be separate")
            if self.managed_path.exists() or self.managed_path.is_symlink():
                self.managed_entries = self._read_entries(self.managed_path)
                if self.entries.keys() & self.managed_entries.keys():
                    raise ValueError("Duplicate Discord destination reference")
                self.entries.update(self.managed_entries)

    @staticmethod
    def _read_entries(path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Discord secret file must be a private regular file (mode 0600)")
        if info.st_size > 1024 * 1024:
            raise ValueError("Discord secret file is too large")
        entries = json.loads(path.read_text())
        if not isinstance(entries, dict):
            raise ValueError("Invalid Discord webhook configuration")
        for value in entries.values():
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("url"), str)
                or not WEBHOOK.fullmatch(value["url"])
            ):
                raise ValueError("Invalid Discord webhook configuration")
            if not all(
                isinstance(value.get(k), str) and value[k] for k in ("user_id", "channel_id")
            ):
                raise ValueError("Each webhook needs an owner and a channel")
        return entries

    @property
    def registration_available(self):
        return bool(self.guild_id and self.managed_path)

    def _check_guild(self, metadata):
        if str(metadata.get("guild_id")) not in {self.guild_id, *self.additional_guild_ids}:
            raise Error(
                "DESTINATION_GUILD_NOT_ALLOWED", "Webhook's Discord server is not allowed", 409
            )

    def register(self, user_id, url):
        """Verify and durably store a user's webhook; never overwrite an existing route."""
        if not self.registration_available:
            raise Error("DESTINATION_UNAVAILABLE", "Discord registration is not configured", 409)
        if not isinstance(url, str) or len(url) > 512 or not WEBHOOK.fullmatch(url):
            raise Error("INVALID_WEBHOOK", "A Discord webhook URL is required", 422)
        metadata = self._metadata(url)
        self._check_guild(metadata)
        channel_id = str(metadata.get("channel_id", ""))
        if not re.fullmatch(r"[0-9]{1,30}", channel_id):
            raise Error("DESTINATION_MISMATCH", "Webhook has no valid channel", 409)
        with self.lock:
            if any(
                entry["channel_id"] == channel_id and entry["user_id"] != user_id
                for entry in self.entries.values()
            ):
                raise Error(
                    "DESTINATION_IN_USE", "This channel is already assigned to another user", 409
                )
            for ref, entry in self.entries.items():
                if entry["user_id"] == user_id and entry["url"] == url:
                    if entry["channel_id"] != channel_id:
                        raise Error(
                            "DESTINATION_MISMATCH", "An existing webhook changed channels", 409
                        )
                    return ref, channel_id
            ref = "web-" + uuid.uuid4().hex
            entry = {"user_id": user_id, "channel_id": channel_id, "url": url}
            entries = {**self.managed_entries, ref: entry}
            self._save_managed(entries)
            self.entries[ref] = entry
            return ref, channel_id

    def discard_pending(self, secret_ref):
        """Remove a rejected enrollment secret after its caller proves it has no live owner."""
        with self.lock:
            if secret_ref not in self.managed_entries:
                return
            entries = {
                key: value for key, value in self.managed_entries.items() if key != secret_ref
            }
            self._save_managed(entries)
            self.entries.pop(secret_ref, None)

    def _save_managed(self, entries):
        # Caller holds the destination lock for the file and in-memory update.
        data = json.dumps(entries).encode()
        if len(data) > 1024 * 1024:
            raise Error("DESTINATION_STORE_FAILED", "Discord storage is full", 503)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.managed_path.parent, prefix=".discord-", delete=False
            ) as output:
                temporary = Path(output.name)
                os.fchmod(output.fileno(), 0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.managed_path)
            directory = os.open(self.managed_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.managed_entries = entries
        except OSError:
            raise Error(
                "DESTINATION_STORE_FAILED", "Discord destination could not be saved", 503
            ) from None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def add_environment_webhook(self, *, user_id, secret_ref, channel_id, url):
        """Use an existing deployment secret without writing another plaintext copy."""
        if (
            not user_id
            or not secret_ref
            or not channel_id
            or not self.guild_id
            or not isinstance(url, str)
            or not WEBHOOK.fullmatch(url)
            or not re.fullmatch(r"[0-9]{1,30}", channel_id)
        ):
            raise ValueError("Incomplete Discord environment configuration")
        if secret_ref in self.entries:
            raise ValueError("Discord destination reference is already configured")
        self.entries[secret_ref] = {"user_id": user_id, "channel_id": channel_id, "url": url}

    def lookup(self, user_id, secret_ref, channel_id):
        value = self.entries.get(secret_ref)
        if (
            not self.guild_id
            or not value
            or value["user_id"] != user_id
            or value["channel_id"] != channel_id
        ):
            raise Error(
                "DESTINATION_UNAVAILABLE",
                "An administrator must configure this user's destination",
                409,
            )
        return value

    def verify(self, user_id, secret_ref, channel_id):
        value = self.lookup(user_id, secret_ref, channel_id)
        metadata = self._metadata(value["url"])
        self._check_guild(metadata)
        if str(metadata.get("channel_id")) != channel_id:
            raise Error(
                "DESTINATION_MISMATCH", "Webhook channel does not match its registration", 409
            )

    def _metadata(self, url):
        try:
            response = self.client.get(url, timeout=5)
            response.raise_for_status()
            metadata = response.json()
            if not isinstance(metadata, dict):
                raise ValueError("Invalid metadata")
        except (httpx.HTTPError, ValueError):
            # Do not expose HTTP exceptions: their URL contains the webhook secret.
            raise Error(
                "DESTINATION_CHECK_FAILED", "Discord destination could not be verified", 503
            ) from None
        return metadata

    def close(self):
        self.client.close()


def discord_payload(event):
    labels = {
        "RUNNING": "작업 시작",
        "SUCCEEDED": "작업 성공",
        "FAILED": "작업 실패",
        "CANCELLED": "작업 취소",
    }
    fields = [
        {"name": "작업 ID", "value": event["job_id"]},
        {"name": "실행 ID", "value": event["execution_id"] or "실행 전"},
        {"name": "서버", "value": event["node_id"] or "배정 전", "inline": True},
        {
            "name": "예약 자원",
            "value": f"CPU {event['cpus']} · RAM {event['memory_mib']} MiB · GPU {len(event['gpu_ids'])}",
            "inline": True,
        },
    ]
    if event["exit_code"] is not None:
        fields.append({"name": "종료 코드", "value": str(event["exit_code"]), "inline": True})
    for key, label in (("started_at", "시작 (UTC)"), ("finished_at", "종료 (UTC)")):
        if event[key] is not None:
            fields.append(
                {"name": label, "value": datetime.fromtimestamp(event[key], UTC).isoformat()}
            )
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"{labels[event['state']]} · {event['name']}"[:256],
                "fields": fields,
            }
        ],
    }


def deliver_discord(destinations, row, payload):
    """Send outside a transaction and return the durable delivery outcome."""
    state, error, message_id, delay = (
        "PENDING",
        None,
        None,
        min(3600, 2 ** min(row["attempts"] + 1, 11)),
    )
    try:
        if not row["enabled"]:
            raise Error("DESTINATION_DISABLED", "Destination is disabled")
        destination = destinations.lookup(row["user_id"], row["secret_ref"], row["channel_id"])
        response = destinations.client.post(
            destination["url"],
            params={"wait": "true"},
            json=payload,
        )
        if 200 <= response.status_code < 300:
            metadata = response.json()
            if not isinstance(metadata, dict) or not metadata.get("id"):
                error = "INVALID_DISCORD_RESPONSE"
            else:
                state, message_id = "SENT", str(metadata["id"])
        elif response.status_code == 429:
            error = "RATE_LIMITED"
            try:
                retry_after = response.headers.get("Retry-After")
                if retry_after is None:
                    retry_after = response.json().get("retry_after", delay)
                delay = max(1, min(86400, float(retry_after)))
            except (TypeError, ValueError, AttributeError):
                pass
        elif 400 <= response.status_code < 500 or 300 <= response.status_code < 400:
            state, error = "FAILED", f"DISCORD_HTTP_{response.status_code}"
        else:
            error = "DISCORD_UNAVAILABLE"
    except Error as exc:
        state, error = "FAILED", exc.code
    except (httpx.HTTPError, ValueError):
        error = "TRANSPORT_ERROR"
    return state, error, message_id, delay


class Notifier:
    def __init__(self, hub, destinations: Destinations):
        self.hub = hub
        self.destinations = destinations

    def step(self):
        """Claim at most one message; no DB lock is held during a network call."""
        now = self.hub.clock()
        with self.hub.store.transaction() as db:
            db.execute(
                "UPDATE notifications SET state='PENDING' WHERE state='SENDING' AND lease_until<?",
                (now,),
            )
            row = db.execute(
                """SELECT n.*,d.user_id,d.secret_ref,d.channel_id,d.enabled FROM notifications n
                JOIN notification_destinations d ON d.id=n.destination_id
                WHERE n.state='PENDING' AND n.next_attempt<=?
                ORDER BY CASE n.kind WHEN 'started' THEN 0 ELSE 1 END,n.id LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                return False
            db.execute(
                "UPDATE notifications SET state='SENDING',lease_until=?,attempts=attempts+1 WHERE id=?",
                (now + 60, row["id"]),
            )
            # Once an end notification begins delivery, do not later deliver a stale start.
            if row["kind"] == "finished":
                db.execute(
                    "UPDATE notifications SET state='SUPPRESSED' WHERE job_id=? AND kind='started' AND state='PENDING'",
                    (row["job_id"],),
                )

        state, error, message_id, delay = deliver_discord(
            self.destinations, row, discord_payload(json.loads(row["payload"]))
        )

        with self.hub.store.transaction() as db:
            db.execute(
                """UPDATE notifications SET state=?,message_id=?,error_code=?,next_attempt=?,lease_until=NULL WHERE id=?""",
                (state, message_id, error, self.hub.clock() + delay, row["id"]),
            )
        return True


def enqueue_enrollment(db, enrollment, config):
    """Save admin alerts in the same transaction as the new account request."""
    embed = {
        "title": "새 계정 승인 요청",
        "description": "새 사용자가 계정 등록을 신청했습니다. 관리자 화면에서 확인해 주세요.",
        "fields": [
            {"name": "서버에서 사용하는 유저 ID", "value": enrollment["account_name"]},
            {"name": "UID / GID", "value": f"{enrollment['uid']} / {enrollment['gid']}"},
            {"name": "신청 ID", "value": enrollment["id"]},
        ],
        "timestamp": datetime.fromtimestamp(enrollment["created_at"], UTC).isoformat(),
    }
    if config.allowed_origins:
        embed["url"] = config.allowed_origins[0] + "/#admin"
        embed["description"] += f"\n[새 계정 승인 열기]({embed['url']})"
    payload = json.dumps({"allowed_mentions": {"parse": []}, "embeds": [embed]})
    for user_id in set(config.admin_users):
        if not db.execute(
            "SELECT 1 FROM principals WHERE id=? AND role='user' AND enabled=1", (user_id,)
        ).fetchone():
            continue
        db.execute(
            """INSERT INTO enrollment_notifications(id,enrollment_id,user_id,payload,next_attempt)
            VALUES(?,?,?,?,?) ON CONFLICT(enrollment_id,user_id) DO NOTHING""",
            (str(uuid.uuid4()), enrollment["id"], user_id, payload, enrollment["created_at"]),
        )


class EnrollmentNotifier:
    def __init__(self, hub, destinations, config):
        self.hub, self.destinations, self.config = hub, destinations, config

    def step(self):
        now = self.hub.clock()
        with self.hub.store.transaction() as db:
            db.execute(
                "UPDATE enrollment_notifications SET state='PENDING' WHERE state='SENDING' AND lease_until<?",
                (now,),
            )
            # Enrollment IDs change on resubmission, so old alerts deliberately have no FK.
            # Resolve the administrator's current route; never use the applicant's webhook.
            row = db.execute(
                """SELECT n.*,p.enabled AS user_enabled,e.state AS enrollment_state,
                d.secret_ref,d.channel_id,d.enabled FROM enrollment_notifications n
                JOIN principals p ON p.id=n.user_id
                LEFT JOIN web_enrollments e ON e.id=n.enrollment_id
                LEFT JOIN notification_destinations d ON d.id=(
                    SELECT MAX(id) FROM notification_destinations WHERE user_id=n.user_id)
                WHERE n.state='PENDING' AND n.next_attempt<=?
                ORDER BY n.next_attempt,n.id LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                return False
            if (
                row["user_id"] not in self.config.admin_users
                or not row["user_enabled"]
                or row["enrollment_state"] != "PENDING"
            ):
                db.execute(
                    "UPDATE enrollment_notifications SET state='SUPPRESSED',lease_until=NULL WHERE id=?",
                    (row["id"],),
                )
                return True
            db.execute(
                "UPDATE enrollment_notifications SET state='SENDING',lease_until=?,attempts=attempts+1 WHERE id=?",
                (now + 60, row["id"]),
            )

        if not row["enabled"]:
            # Keep the request until the administrator configures their own channel.
            state, error, message_id, delay = "PENDING", "DESTINATION_UNAVAILABLE", None, 60
        else:
            state, error, message_id, delay = deliver_discord(
                self.destinations, row, json.loads(row["payload"])
            )
            if error == "DESTINATION_UNAVAILABLE":
                state, delay = "PENDING", max(60, delay)
        with self.hub.store.transaction() as db:
            db.execute(
                """UPDATE enrollment_notifications SET state=?,message_id=?,error_code=?,
                next_attempt=?,lease_until=NULL WHERE id=?""",
                (state, message_id, error, self.hub.clock() + delay, row["id"]),
            )
        return True
