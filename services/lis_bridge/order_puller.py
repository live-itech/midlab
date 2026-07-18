"""
services/lis_bridge/order_puller.py — Pull order pending dari LIS.

Loop: GET /orders/pending → insert ke tbl_order (dedup by order_id).
LIS akan terus return pending sampai kita POST /results
(yang otomatis transition order status di LIS ke 'sample_received').
"""
from __future__ import annotations

import asyncio

from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger


class OrderPuller:
    def __init__(
        self,
        instrument,
        client: LisApiClient,
        order_exists_fn,    # callable(instrument_id, order_id) -> bool
        save_order_fn,      # callable(instrument_id, order_json) -> int
        poll_interval: float | None = None,
    ):
        self._instrument = instrument
        self._client = client
        self._order_exists = order_exists_fn
        self._save_order = save_order_fn
        self._poll_interval = (
            poll_interval if poll_interval is not None
            else (instrument.order_poll_interval or 10)
        )
        self._logger = get_logger(f"lis_bridge.{instrument.id}.order_puller")
        self._running = True

    async def run_once(self):
        try:
            body = await self._client.get_orders_pending()
        except LisApiError as e:
            self._logger.warning(f"order_puller: GET failed: {e}")
            return

        orders = body.get("data") or []
        if not orders:
            return

        inserted = 0
        for order in orders:
            order_id = order.get("order_id", "")
            if not order_id:
                continue
            exists = await asyncio.get_event_loop().run_in_executor(
                None, self._order_exists, self._instrument.id, order_id,
            )
            if exists:
                continue
            new_id = await asyncio.get_event_loop().run_in_executor(
                None, self._save_order, self._instrument.id, order,
            )
            if new_id:
                inserted += 1
        if inserted:
            self._logger.info(f"order_puller: inserted {inserted} new order(s)")

    async def run_forever(self):
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                self._logger.error(f"order_puller cycle error: {e}")
            await asyncio.sleep(self._poll_interval)

    def stop(self):
        self._running = False
