"""
services/lis_bridge/status_reporter.py — Drain tbl_lis_event_queue (status events).

Loop: poll queue → POST /status → update send_status.
Retry semantics sama dengan ResultPusher.
"""
from __future__ import annotations

import asyncio

from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger


class StatusReporter:
    def __init__(
        self,
        instrument,
        client: LisApiClient,
        poll_events_fn,
        update_event_fn,
        retry_max: int,
        poll_interval: float = 2.0,
    ):
        self._instrument = instrument
        self._client = client
        self._poll_events = poll_events_fn
        self._update_event = update_event_fn
        self._retry_max = retry_max
        self._poll_interval = poll_interval
        self._logger = get_logger(f"lis_bridge.{instrument.id}.status_reporter")
        self._running = True

    async def run_once(self):
        events = await asyncio.get_event_loop().run_in_executor(None, self._poll_events)
        for ev in events or []:
            if not self._running:
                break
            await self._send(ev)

    async def run_forever(self):
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                self._logger.error(f"status_reporter cycle error: {e}")
            await asyncio.sleep(self._poll_interval)

    def stop(self):
        self._running = False

    async def _send(self, ev):
        try:
            status, _ = await self._client.post_status(ev.payload_json)
            if 200 <= status < 300:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_event, ev.id, "sent", None,
                )
                return
            raise LisApiError(status=status, message="unexpected", retryable=False)
        except LisApiError as e:
            if not e.retryable:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_event, ev.id, "failed", f"{e.status}: {e.message}",
                )
                return
            next_count = (ev.retry_count or 0) + 1
            if next_count >= self._retry_max:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_event, ev.id, "failed",
                    f"{e.status}: {e.message} (retry exhausted)",
                )
            else:
                await asyncio.get_event_loop().run_in_executor(
                    None, _call_increment, self._update_event,
                    ev.id, "pending", f"{e.status}: {e.message}",
                )


def _call_increment(fn, *args):
    return fn(*args, increment_retry=True)
