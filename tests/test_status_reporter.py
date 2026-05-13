"""Test StatusReporter: drain queue (event_type='status'), 2xx→sent, 5xx→retry."""
from unittest.mock import AsyncMock, MagicMock

from lib.lis_client import LisApiError
from services.lis_bridge.status_reporter import StatusReporter


def _event(id, payload, retry=0):
    ev = MagicMock()
    ev.id = id
    ev.payload_json = payload
    ev.retry_count = retry
    return ev


def _instrument(id=7):
    inst = MagicMock()
    inst.id = id
    return inst


async def test_2xx_marks_sent():
    client = AsyncMock()
    client.post_status = AsyncMock(return_value=(200, {"success": True}))
    update_fn = MagicMock()
    poll_fn = MagicMock(return_value=[_event(1, {"status": "online"})])

    reporter = StatusReporter(
        instrument=_instrument(), client=client,
        poll_events_fn=poll_fn, update_event_fn=update_fn, retry_max=3,
    )
    await reporter.run_once()
    update_fn.assert_called_with(1, "sent", None)


async def test_4xx_marks_failed_permanent():
    client = AsyncMock()
    client.post_status = AsyncMock(side_effect=LisApiError(
        status=422, message="bad payload", retryable=False,
    ))
    update_fn = MagicMock()
    poll_fn = MagicMock(return_value=[_event(1, {"status": "wrong"})])

    reporter = StatusReporter(
        instrument=_instrument(), client=client,
        poll_events_fn=poll_fn, update_event_fn=update_fn, retry_max=3,
    )
    await reporter.run_once()
    args, _ = update_fn.call_args
    assert args[1] == "failed"


async def test_5xx_keeps_pending_under_max():
    client = AsyncMock()
    client.post_status = AsyncMock(side_effect=LisApiError(
        status=502, message="x", retryable=True,
    ))
    update_fn = MagicMock()
    poll_fn = MagicMock(return_value=[_event(1, {"status": "online"}, retry=0)])

    reporter = StatusReporter(
        instrument=_instrument(), client=client,
        poll_events_fn=poll_fn, update_event_fn=update_fn, retry_max=3,
    )
    await reporter.run_once()
    args, kwargs = update_fn.call_args
    assert args[1] == "pending"
    assert kwargs.get("increment_retry") is True
