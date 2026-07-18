"""Test LisBridgeService: load config, verify with LIS, exception in load_config."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.lis_bridge.service import LisBridgeService


def _instrument(id=7, lis_id="INST-X", api_key="inst_key", enabled=True):
    inst = MagicMock()
    inst.id = id
    inst.name = "Test"
    inst.lis_instrument_id = lis_id
    inst.lis_api_key = api_key
    inst.lis_bridge_enabled = enabled
    inst.order_poll_interval = 10
    return inst


async def test_load_config_reads_base_url_from_settings():
    inst = _instrument()
    with patch("services.lis_bridge.service.get_instrument_by_id", return_value=inst), \
         patch("services.lis_bridge.service.get_setting") as gs:
        gs.side_effect = lambda key, default=None: {
            "lis.base_url": "https://eazy.example",
            "lis.http_timeout": "30",
            "lis.retry_max": "3",
        }.get(key, default)

        svc = LisBridgeService(instrument_id=7)
        svc._load_config()
        assert svc.lis_base_url == "https://eazy.example"
        assert svc.lis_api_key == "inst_key"
        assert svc.lis_instrument_id == "INST-X"


async def test_load_config_raises_if_no_api_key():
    inst = _instrument(api_key="")
    with patch("services.lis_bridge.service.get_instrument_by_id", return_value=inst), \
         patch("services.lis_bridge.service.get_setting", return_value="https://x"):
        svc = LisBridgeService(instrument_id=7)
        with pytest.raises(ValueError, match="lis_api_key"):
            svc._load_config()


async def test_verify_with_lis_caches_lis_instrument_id():
    inst = _instrument(lis_id=None)
    with patch("services.lis_bridge.service.get_instrument_by_id", return_value=inst), \
         patch("services.lis_bridge.service.get_setting", return_value="https://x"), \
         patch("services.lis_bridge.service.update_instrument_lis_sync") as upd:
        svc = LisBridgeService(instrument_id=7)
        svc._load_config()

        mock_client = AsyncMock()
        mock_client.get_instrument = AsyncMock(return_value={
            "data": {"instrument": {"instrument_id": "INST-Y"}}
        })
        svc._client = mock_client

        await svc._verify_with_lis()
        assert svc.lis_instrument_id == "INST-Y"
        upd.assert_called_once()
