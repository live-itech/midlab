"""
Integration test against real EazyApp sandbox.
Run manually with:
    SANDBOX_API_KEY=inst_xxx pytest tests/integration -v -m sandbox
"""
import os
import pytest

from lib.lis_client import LisApiClient

SANDBOX_URL = os.environ.get("SANDBOX_URL", "https://eazy.vespahobby.xyz")
SANDBOX_KEY = os.environ.get("SANDBOX_API_KEY")

pytestmark = pytest.mark.skipif(
    not SANDBOX_KEY,
    reason="SANDBOX_API_KEY not set; skipping live integration test",
)


@pytest.mark.sandbox
async def test_get_instrument_live():
    async with LisApiClient(base_url=SANDBOX_URL, api_key=SANDBOX_KEY) as client:
        data = await client.get_instrument()
    assert data["success"] is True
    inst = data["data"]["instrument"]
    assert "instrument_id" in inst
    assert inst["instrument_id"].startswith("INST-")


@pytest.mark.sandbox
async def test_get_orders_pending_live():
    async with LisApiClient(base_url=SANDBOX_URL, api_key=SANDBOX_KEY) as client:
        data = await client.get_orders_pending()
    assert "data" in data
    assert isinstance(data["data"], list)


@pytest.mark.sandbox
async def test_post_status_online_offline_live():
    async with LisApiClient(base_url=SANDBOX_URL, api_key=SANDBOX_KEY) as client:
        s1, _ = await client.post_status({"status": "online"})
        s2, _ = await client.post_status({"status": "offline"})
    assert s1 == 200
    assert s2 == 200
