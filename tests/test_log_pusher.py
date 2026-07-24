"""Test LogPusher: filter WARN/ERROR, cursor advance, only this instrument."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from services.lis_bridge.log_pusher import LogPusher, _log_matches_instrument


def _log(id, level, service, message, ts):
    row = MagicMock()
    row.id = id
    row.level = level
    row.service = service
    row.message = message
    row.logged_at = ts
    return row


def _instrument(id=7):
    inst = MagicMock()
    inst.id = id
    inst.lis_instrument_id = "INST-X"
    return inst


def test_log_matches_instrument_by_service_name():
    log = _log(1, "WARNING", "tcp_7", "x", datetime.now())
    assert _log_matches_instrument(log, 7) is True
    assert _log_matches_instrument(log, 8) is False


def test_log_matches_lis_bridge_service_name():
    log = _log(1, "ERROR", "lis_bridge_7", "x", datetime.now())
    assert _log_matches_instrument(log, 7) is True


def test_log_matches_via_message_tag():
    log = _log(1, "WARNING", "protocols", "[INSTRUMENT 7] parse error", datetime.now())
    assert _log_matches_instrument(log, 7) is True


async def test_pushes_warn_and_error_only():
    # Naive = jam lokal lab, bentuk yang tersimpan di tbl_service_log.logged_at.
    ts = datetime(2026, 5, 13, 10, 0)
    logs = [
        _log(1, "INFO",    "tcp_7", "noise", ts),
        _log(2, "WARNING", "tcp_7", "warn", ts),
        _log(3, "ERROR",   "tcp_7", "err",  ts),
    ]
    client = AsyncMock()
    client.post_log = AsyncMock(return_value=(200, {"success": True}))
    get_cursor = MagicMock(return_value=None)
    set_cursor = MagicMock()
    poll_logs = MagicMock(return_value=logs)

    pusher = LogPusher(
        instrument=_instrument(7),
        client=client,
        get_cursor_fn=get_cursor,
        set_cursor_fn=set_cursor,
        poll_logs_fn=poll_logs,
    )
    await pusher.run_once()

    assert client.post_log.await_count == 2  # INFO skipped
    set_cursor.assert_called_with(7, ts)

    # logged_at harus sampai ke EazyApp ber-offset, bukan naive telanjang.
    sent = client.post_log.await_args_list[0].args[0]
    assert sent["logged_at"] == "2026-05-13T10:00:00+07:00"
