"""
services/lis_bridge/result_pusher.py — Push tbl_result pending ke LIS.

Loop: poll tbl_result → POST /results → update send_status.
2xx → 'sent'. 422 → 'failed' permanen. 5xx/network → bump retry_count;
'pending' kalau belum exhausted, 'failed' kalau retry_count>=retry_max.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger


def build_mid_payload(result_row, instrument) -> dict:
    """Build MID v1.0 payload, rewrite instrument_id ke string LIS."""
    payload = dict(result_row.result_json or {})
    payload["instrument_id"] = instrument.lis_instrument_id
    payload["mid_version"] = "1.0"
    payload.setdefault("message_id", f"MSG-{instrument.id}-{result_row.id}")
    if not payload.get("message_datetime"):
        ts = result_row.received_at or datetime.now(timezone.utc)
        payload["message_datetime"] = ts.isoformat()
    return payload


class ResultPusher:
    def __init__(
        self,
        instrument,
        client: LisApiClient,
        poll_results_fn,        # callable() -> list[TblResult]
        update_status_fn,       # callable(result_id, status, error, *, increment_retry=False)
        retry_max: int,
        batch_size: int = 50,
        poll_interval: float = 5.0,
    ):
        self._instrument = instrument
        self._client = client
        self._poll_results = poll_results_fn
        self._update_status = update_status_fn
        self._retry_max = retry_max
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._logger = get_logger(f"lis_bridge.{instrument.id}.result_pusher")
        self._running = True

    async def run_once(self):
        rows = await asyncio.get_event_loop().run_in_executor(None, self._poll_results)
        for row in rows or []:
            if not self._running:
                break
            await self._send(row)

    async def run_forever(self):
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                self._logger.error(f"result_pusher cycle error: {e}")
            await asyncio.sleep(self._poll_interval)

    def stop(self):
        self._running = False

    async def _send(self, row):
        payload = build_mid_payload(row, self._instrument)
        try:
            status, _ = await self._client.post_results(payload)
            if 200 <= status < 300:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_status, row.id, "sent", None,
                )
                self._logger.info(f"result_id={row.id} sent")
                return
            raise LisApiError(status=status, message="unexpected status", retryable=False)
        except LisApiError as e:
            if not e.retryable:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_status, row.id, "failed", f"{e.status}: {e.message}",
                )
                self._logger.warning(f"result_id={row.id} failed (permanent): {e}")
                return
            next_count = (row.retry_count or 0) + 1
            if next_count >= self._retry_max:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_status, row.id, "failed",
                    f"{e.status}: {e.message} (retry exhausted)",
                )
                self._logger.warning(f"result_id={row.id} retry exhausted")
            else:
                await asyncio.get_event_loop().run_in_executor(
                    None, _call_with_retry_kwarg, self._update_status,
                    row.id, "pending", f"{e.status}: {e.message}",
                )
                self._logger.info(f"result_id={row.id} retry {next_count}/{self._retry_max}")


def _call_with_retry_kwarg(fn, *args):
    return fn(*args, increment_retry=True)
