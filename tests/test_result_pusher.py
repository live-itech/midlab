"""Test ResultPusher: payload build, 2xx→sent, 422→failed, 5xx→retry semantics."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.db import TblResult
from lib.lis_client import LisApiError
from services.lis_bridge.result_pusher import ResultPusher, build_mid_payload


def _row(**kw):
    defaults = {
        "id": 1,
        "instrument_id": 7,
        "result_json": {"mid_version": "1.0", "results": []},
        # Naive = jam dinding lokal lab, persis bentuk yang dibaca dari
        # kolom DATETIME (lihat lib/timeutil.py).
        "received_at": datetime(2026, 5, 13, 8, 0),
        "retry_count": 0,
    }
    defaults.update(kw)
    r = TblResult()
    for k, v in defaults.items():
        setattr(r, k, v)
    return r


def _instrument(**kw):
    inst = MagicMock()
    inst.id = kw.get("id", 7)
    inst.lis_instrument_id = kw.get("lis_instrument_id", "INST-X")
    return inst


def test_build_mid_payload_rewrites_instrument_id():
    row = _row(result_json={"mid_version": "1.0", "instrument_id": 7})
    payload = build_mid_payload(row, _instrument())
    assert payload["instrument_id"] == "INST-X"
    assert payload["mid_version"] == "1.0"
    assert payload["message_id"] == "MSG-7-1"
    assert payload["message_datetime"] == "2026-05-13T08:00:00+07:00"


async def test_2xx_marks_sent():
    client = AsyncMock()
    client.post_results = AsyncMock(return_value=(201, {"success": True}))
    update_fn = MagicMock()
    poll_fn = MagicMock(return_value=[_row()])

    pusher = ResultPusher(
        instrument=_instrument(),
        client=client,
        poll_results_fn=poll_fn,
        update_status_fn=update_fn,
        retry_max=3,
    )
    await pusher.run_once()
    update_fn.assert_called_with(1, "sent", None)


async def test_422_marks_failed_permanent():
    client = AsyncMock()
    client.post_results = AsyncMock(side_effect=LisApiError(
        status=422, message="validation", retryable=False,
    ))
    update_fn = MagicMock()
    poll_fn = MagicMock(return_value=[_row()])

    pusher = ResultPusher(
        instrument=_instrument(), client=client,
        poll_results_fn=poll_fn, update_status_fn=update_fn, retry_max=3,
    )
    await pusher.run_once()
    args, _ = update_fn.call_args
    assert args[0] == 1
    assert args[1] == "failed"
    assert "validation" in args[2]


async def test_5xx_under_retry_max_stays_pending():
    client = AsyncMock()
    client.post_results = AsyncMock(side_effect=LisApiError(
        status=502, message="bad gateway", retryable=True,
    ))
    update_fn = MagicMock()
    row = _row(retry_count=0)
    poll_fn = MagicMock(return_value=[row])

    pusher = ResultPusher(
        instrument=_instrument(), client=client,
        poll_results_fn=poll_fn, update_status_fn=update_fn, retry_max=3,
    )
    await pusher.run_once()
    args, kwargs = update_fn.call_args
    assert args[1] == "pending"
    assert kwargs.get("increment_retry") is True


async def test_5xx_at_retry_max_marks_failed():
    client = AsyncMock()
    client.post_results = AsyncMock(side_effect=LisApiError(
        status=502, message="bad gateway", retryable=True,
    ))
    update_fn = MagicMock()
    row = _row(retry_count=2)
    poll_fn = MagicMock(return_value=[row])

    pusher = ResultPusher(
        instrument=_instrument(), client=client,
        poll_results_fn=poll_fn, update_status_fn=update_fn, retry_max=3,
    )
    await pusher.run_once()
    args, _ = update_fn.call_args
    assert args[1] == "failed"
