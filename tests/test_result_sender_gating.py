"""ResultSenderService skips rows when instrument.lis_bridge_enabled=True."""
from unittest.mock import AsyncMock, MagicMock, patch


async def test_skip_lis_bridge_enabled_instrument():
    from services.result_sender.service import ResultSenderService

    svc = ResultSenderService()
    svc._running = True
    svc._retry_max = 3
    svc._batch_size = 10

    result_row = MagicMock(id=1, instrument_id=7, retry_count=0)
    enabled_inst = MagicMock(id=7, lis_bridge_enabled=True)

    session = MagicMock()

    with patch("services.result_sender.service.get_pending_results", return_value=[result_row]), \
         patch("services.result_sender.service.get_instrument_by_id", return_value=enabled_inst), \
         patch("services.result_sender.service.get_setting", return_value=""), \
         patch.object(svc, "_refresh_lis_settings"), \
         patch.object(svc, "_send_result", new=AsyncMock()) as send_spy:
        svc._lis_url = "http://test"
        await svc._poll_and_send(session)
        send_spy.assert_not_called()


async def test_proceeds_when_lis_bridge_disabled():
    from services.result_sender.service import ResultSenderService

    svc = ResultSenderService()
    svc._running = True
    svc._retry_max = 3
    svc._batch_size = 10

    result_row = MagicMock(id=1, instrument_id=7, retry_count=0)
    disabled_inst = MagicMock(id=7, lis_bridge_enabled=False)

    session = MagicMock()

    with patch("services.result_sender.service.get_pending_results", return_value=[result_row]), \
         patch("services.result_sender.service.get_instrument_by_id", return_value=disabled_inst), \
         patch("services.result_sender.service.get_setting", return_value=""), \
         patch.object(svc, "_refresh_lis_settings"), \
         patch.object(svc, "_send_result", new=AsyncMock()) as send_spy:
        svc._lis_url = "http://test"
        await svc._poll_and_send(session)
        send_spy.assert_called_once()
