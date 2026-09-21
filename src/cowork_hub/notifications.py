"""Discord destinations and a durable outbox, independent of resource scheduling."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .models import Error

WEBHOOK = re.compile(r"https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9._-]+\Z")


class Destinations:
    def __init__(self, path: Path | None, guild_id: str | None, client=None):
        self.guild_id = guild_id
        self.entries = {}
        self.client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
        if path:
            if path.stat().st_mode & 0o077:
                raise ValueError("Discord secret file must be private (mode 0600)")
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("Discord secret file is too large")
            self.entries = json.loads(path.read_text())
            for value in self.entries.values():
                if not isinstance(value, dict) or not WEBHOOK.fullmatch(value.get("url", "")):
                    raise ValueError("Invalid Discord webhook configuration")
                if not all(
                    isinstance(value.get(k), str) and value[k] for k in ("user_id", "channel_id")
                ):
                    raise ValueError("Each webhook needs an owner and a channel")

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
        try:
            response = self.client.get(value["url"])
            response.raise_for_status()
            metadata = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Do not expose HTTP exceptions: their URL contains the webhook secret.
            raise Error(
                "DESTINATION_CHECK_FAILED", "Discord destination could not be verified", 503
            ) from exc
        if (
            not isinstance(metadata, dict)
            or str(metadata.get("guild_id")) != self.guild_id
            or str(metadata.get("channel_id")) != channel_id
        ):
            raise Error(
                "DESTINATION_MISMATCH", "Webhook is not in the configured guild and channel", 409
            )

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

        state, error, message_id, delay = (
            "PENDING",
            None,
            None,
            min(3600, 2 ** min(row["attempts"] + 1, 11)),
        )
        try:
            if not row["enabled"]:
                raise Error("DESTINATION_DISABLED", "Destination is disabled")
            destination = self.destinations.lookup(
                row["user_id"], row["secret_ref"], row["channel_id"]
            )
            response = self.destinations.client.post(
                destination["url"],
                params={"wait": "true"},
                json=discord_payload(json.loads(row["payload"])),
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

        with self.hub.store.transaction() as db:
            db.execute(
                """UPDATE notifications SET state=?,message_id=?,error_code=?,next_attempt=?,lease_until=NULL WHERE id=?""",
                (state, message_id, error, self.hub.clock() + delay, row["id"]),
            )
        return True
