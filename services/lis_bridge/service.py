"""
services/lis_bridge/service.py — LisBridgeService orchestrator per-alat.

Lifecycle:
  start() → _load_config() → _verify_with_lis() → _push_boot_status()
         → asyncio.gather(supervise(result_pusher), supervise(order_puller),
                          supervise(status_reporter), supervise(log_pusher))

Setiap child task di-wrap _supervise(): catch exception, log, restart
dengan exponential backoff 1s → 2s → 4s → ... max 60s.
"""
from __future__ import annotations

import asyncio
import signal

from lib.db import (
    get_instrument_by_id,
    get_pending_results,
    get_pending_lis_events,
    update_result_status,
    update_lis_event_status,
    get_setting,
    update_instrument_lis_sync,
    order_exists_by_lis_id,
    save_order,
    get_log_cursor,
    set_log_cursor,
    get_service_logs_after,
)
from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger

from services.lis_bridge.result_pusher import ResultPusher
from services.lis_bridge.order_puller import OrderPuller
from services.lis_bridge.status_reporter import StatusReporter
from services.lis_bridge.log_pusher import LogPusher


def _int_setting(key: str, default: int) -> int:
    v = get_setting(key, str(default))
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class LisBridgeService:
    def __init__(self, instrument_id: int):
        self.instrument_id = instrument_id
        self.instrument = None
        self.lis_base_url = None
        self.lis_api_key = None
        self.lis_instrument_id = None
        self._client: LisApiClient | None = None
        self._logger = get_logger(f"lis_bridge_{instrument_id}")
        self._running = False
        self._stoppables: list = []

    async def start(self):
        self._running = True
        self._load_config()
        async with LisApiClient(
            base_url=self.lis_base_url,
            api_key=self.lis_api_key,
            timeout=_int_setting("lis.http_timeout", 30),
            retry_max=_int_setting("lis.retry_max", 3),
        ) as client:
            self._client = client
            await self._verify_with_lis()
            await self._push_boot_status()
            self._setup_signal_handlers()
            try:
                await self._run_supervised_tasks()
            finally:
                await self._push_shutdown_status()

    def _setup_signal_handlers(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._signal_handler, sig)
            except NotImplementedError:
                pass

    def _signal_handler(self, sig):
        self._logger.info(f"signal {sig} received, shutting down...")
        self._running = False
        for s in self._stoppables:
            try:
                s.stop()
            except Exception:
                pass

    def _load_config(self):
        inst = get_instrument_by_id(self.instrument_id)
        if not inst:
            raise ValueError(f"Instrument id={self.instrument_id} tidak ditemukan")
        if not inst.lis_api_key:
            raise ValueError(
                f"Instrument id={self.instrument_id} belum punya lis_api_key — "
                f"set via Web Console dulu"
            )
        self.instrument = inst
        self.lis_api_key = inst.lis_api_key
        self.lis_instrument_id = inst.lis_instrument_id
        self.lis_base_url = get_setting("lis.base_url", "https://eazy.vespahobby.xyz")
        self._logger.info(
            f"config loaded: base={self.lis_base_url}, "
            f"lis_instrument_id={self.lis_instrument_id or '(belum sync)'}"
        )

    async def _verify_with_lis(self):
        try:
            body = await self._client.get_instrument()
        except LisApiError as e:
            self._logger.error(f"GET /instrument gagal: {e}")
            raise
        info = (body.get("data") or {}).get("instrument") or {}
        new_id = info.get("instrument_id")
        if new_id and new_id != self.lis_instrument_id:
            self.lis_instrument_id = new_id
            self.instrument.lis_instrument_id = new_id
            await asyncio.get_event_loop().run_in_executor(
                None, update_instrument_lis_sync, self.instrument_id, new_id,
            )
            self._logger.info(f"lis_instrument_id synced: {new_id}")

    async def _push_boot_status(self):
        try:
            await self._client.post_status({"status": "online"})
            self._logger.info("boot status=online pushed")
        except LisApiError as e:
            self._logger.warning(f"boot status push gagal: {e}")

    async def _push_shutdown_status(self):
        if not self._client:
            return
        try:
            await self._client.post_status({"status": "offline"})
            self._logger.info("shutdown status=offline pushed")
        except Exception as e:
            self._logger.warning(f"shutdown status push gagal: {e}")

    async def _run_supervised_tasks(self):
        retry_max = _int_setting("lis.retry_max", 3)
        result_poll = _int_setting("lis.result_poll_interval", 5)
        status_poll = _int_setting("lis.status_poll_interval", 2)
        log_poll    = _int_setting("lis.log_poll_interval", 5)

        result_pusher = ResultPusher(
            instrument=self.instrument,
            client=self._client,
            poll_results_fn=lambda: _poll_results_for(self.instrument_id),
            update_status_fn=update_result_status,
            retry_max=retry_max,
            poll_interval=result_poll,
        )
        order_puller = OrderPuller(
            instrument=self.instrument,
            client=self._client,
            order_exists_fn=order_exists_by_lis_id,
            save_order_fn=save_order,
        )
        status_reporter = StatusReporter(
            instrument=self.instrument,
            client=self._client,
            poll_events_fn=lambda: get_pending_lis_events(
                self.instrument_id, event_type="status", limit=50,
            ),
            update_event_fn=update_lis_event_status,
            retry_max=retry_max,
            poll_interval=status_poll,
        )
        log_pusher = LogPusher(
            instrument=self.instrument,
            client=self._client,
            get_cursor_fn=get_log_cursor,
            set_cursor_fn=set_log_cursor,
            poll_logs_fn=lambda cursor: get_service_logs_after(
                cursor, ("WARNING", "ERROR"), limit=100,
            ),
            poll_interval=log_poll,
        )

        self._stoppables = [result_pusher, order_puller, status_reporter, log_pusher]

        await asyncio.gather(
            self._supervise(result_pusher.run_forever, "result_pusher"),
            self._supervise(order_puller.run_forever, "order_puller"),
            self._supervise(status_reporter.run_forever, "status_reporter"),
            self._supervise(log_pusher.run_forever, "log_pusher"),
        )

    async def _supervise(self, coro_fn, name: str):
        backoff = 1
        while self._running:
            try:
                await coro_fn()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.error(f"[{name}] crashed: {e}; restart in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


def _poll_results_for(instrument_id: int):
    """Filter get_pending_results ke alat ini."""
    rows = get_pending_results(limit=200)
    return [r for r in rows if r.instrument_id == instrument_id]
