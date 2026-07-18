"""Test OrderPuller: insert new orders, skip duplicates by order_id."""
from unittest.mock import AsyncMock, MagicMock

from services.lis_bridge.order_puller import OrderPuller


def _instrument(id=7, lis_id="INST-X"):
    inst = MagicMock()
    inst.id = id
    inst.lis_instrument_id = lis_id
    inst.order_poll_interval = 10
    return inst


async def test_inserts_new_orders():
    client = AsyncMock()
    client.get_orders_pending = AsyncMock(return_value={
        "success": True, "count": 2,
        "data": [
            {"order_id": "LAB-A", "patient": {}, "specimen": {}, "tests": []},
            {"order_id": "LAB-B", "patient": {}, "specimen": {}, "tests": []},
        ],
    })

    existing = set()
    def order_exists(iid, oid):
        return oid in existing
    save_order = MagicMock(side_effect=lambda iid, oj: (
        existing.add(oj["order_id"]) or 1
    ))

    puller = OrderPuller(
        instrument=_instrument(),
        client=client,
        order_exists_fn=order_exists,
        save_order_fn=save_order,
    )
    await puller.run_once()
    assert save_order.call_count == 2
    assert existing == {"LAB-A", "LAB-B"}


async def test_skips_duplicate_order_id():
    client = AsyncMock()
    client.get_orders_pending = AsyncMock(return_value={"data": [{"order_id": "LAB-A"}]})
    save_order = MagicMock(return_value=1)
    puller = OrderPuller(
        instrument=_instrument(),
        client=client,
        order_exists_fn=lambda iid, oid: True,
        save_order_fn=save_order,
    )
    await puller.run_once()
    save_order.assert_not_called()


async def test_empty_data_no_crash():
    client = AsyncMock()
    client.get_orders_pending = AsyncMock(return_value={"data": []})
    save_order = MagicMock()
    puller = OrderPuller(
        instrument=_instrument(),
        client=client,
        order_exists_fn=lambda *a: False,
        save_order_fn=save_order,
    )
    await puller.run_once()
    save_order.assert_not_called()
