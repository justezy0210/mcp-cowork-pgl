"""Deadline-driven liveness checks and an infrequent reconciliation fallback."""

import asyncio
import contextlib
import logging

RECONCILE_SECONDS = 60
logger = logging.getLogger(__name__)


async def run_scheduler(hub, changed, *, reconcile_seconds=RECONCILE_SECONDS, retry_seconds=1):
    loop = asyncio.get_running_loop()
    reconcile_at = loop.time()  # Rebuild the scheduling view once after every restart.
    while True:
        # Subscribe before reading. Commits during the read cannot strand a new deadline.
        changed.clear()
        try:
            if loop.time() >= reconcile_at:
                await asyncio.to_thread(hub.tick)
                reconcile_at = loop.time() + reconcile_seconds
            deadline = await asyncio.to_thread(hub.next_liveness_deadline)
            if deadline is not None and deadline <= hub.clock():
                await asyncio.to_thread(hub.expire)
                continue
            delay = reconcile_at - loop.time()
            if deadline is not None:
                delay = min(delay, deadline - hub.clock())
        except Exception:
            logger.error("Scheduler check failed; will retry")
            await asyncio.sleep(retry_seconds)
            continue
        # Heartbeats only extend deadlines. Re-read on wake before expiring anything;
        # an ordinary heartbeat need not wake this timer or rescan the queue.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(changed.wait(), max(0, delay))
