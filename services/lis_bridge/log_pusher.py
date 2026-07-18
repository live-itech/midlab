"""
services/lis_bridge/log_pusher.py — Cursor-based push tbl_service_log → LIS.

Filter level: WARNING + ERROR only.
Filter alat: service name tcp_<id> / lis_bridge_<id> / tag [INSTRUMENT <id>] di message.
Cursor disimpan di tbl_setting key 'lis.log_cursor.<instrument_id>'.
"""
from __future__ import annotations

import asyncio

from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger


PUSHABLE_LEVELS = {"WARNING", "ERROR"}


def _log_matches_instrument(log_row, instrument_id: int) -> bool:
    service = log_row.service or ""
    if service == f"tcp_{instrument_id}":
        return True
    if service == f"lis_bridge_{instrument_id}":
        return True
    if service.startswith(f"lis_bridge.{instrument_id}."):
        return True
    msg = log_row.message or ""
    if f"[INSTRUMENT {instrument_id}]" in msg:
        return True
    return False


class LogPusher:
    def __init__(
        self,
        instrument,
        client: LisApiClient,
        get_cursor_fn,   # callable(instrument_id) -> datetime|None
        set_cursor_fn,   # callable(instrument_id, datetime)
        poll_logs_fn,    # callable(cursor) -> list[TblServiceLog]
        poll_interval: float = 5.0,
    ):
        self._instrument = instrument
        self._client = client
        self._get_cursor = get_cursor_fn
        self._set_cursor = set_cursor_fn
        self._poll_logs = poll_logs_fn
        self._poll_interval = poll_interval
        self._logger = get_logger(f"lis_bridge.{instrument.id}.log_pusher")
        self._running = True

    async def run_once(self):
        cursor = await asyncio.get_event_loop().run_in_executor(
            None, self._get_cursor, self._instrument.id,
        )
        logs = await asyncio.get_event_loop().run_in_executor(
            None, self._poll_logs, cursor,
        )
        last_ts = cursor
        for log in logs or []:
            if not self._running:
                break
            if log.level not in PUSHABLE_LEVELS:
                continue
            if not _log_matches_instrument(log, self._instrument.id):
                continue
            try:
                await self._client.post_log({
                    "level": log.level.lower(),
                    "message": log.message,
                    "logged_at": log.logged_at.isoformat() if log.logged_at else None,
                    "context": {
                        "service": log.service,
                        "instrument_id": self._instrument.lis_instrument_id,
                    },
                })
                last_ts = log.logged_at
            except LisApiError as e:
                self._logger.warning(f"log_pusher: push gagal id={log.id}: {e}")
                break

        if last_ts and last_ts != cursor:
            await asyncio.get_event_loop().run_in_executor(
                None, self._set_cursor, self._instrument.id, last_ts,
            )

    async def run_forever(self):
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                self._logger.error(f"log_pusher cycle error: {e}")
            await asyncio.sleep(self._poll_interval)

    def stop(self):
        self._running = False
