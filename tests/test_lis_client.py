"""Test LisApiClient: auth header, retry on 5xx, no retry on 4xx."""
import pytest
from aioresponses import aioresponses

from lib.lis_client import LisApiClient, LisApiError


BASE = "https://eazy.example.test"
KEY = "inst_TEST_KEY"


@pytest.fixture
def client():
    return LisApiClient(base_url=BASE, api_key=KEY, timeout=2, retry_max=3)


async def test_get_instrument_sends_bearer(client):
    with aioresponses() as m:
        m.get(
            f"{BASE}/api/v1/instrument",
            payload={"success": True, "data": {"instrument": {"instrument_id": "INST-X"}}},
            status=200,
        )
        async with client:
            data = await client.get_instrument()
        assert data["data"]["instrument"]["instrument_id"] == "INST-X"

        first_call = list(m.requests.values())[0][0]
        assert first_call.kwargs["headers"]["Authorization"] == f"Bearer {KEY}"


async def test_post_results_returns_body(client):
    with aioresponses() as m:
        m.post(
            f"{BASE}/api/v1/instrument/results",
            payload={"success": True, "message_id": "MSG-1"},
            status=201,
        )
        async with client:
            status, body = await client.post_results({"mid_version": "1.0"})
        assert status == 201
        assert body["message_id"] == "MSG-1"


async def test_4xx_raises_no_retry(client):
    with aioresponses() as m:
        m.post(
            f"{BASE}/api/v1/instrument/results",
            payload={"message": "validation failed"},
            status=422,
        )
        async with client:
            with pytest.raises(LisApiError) as exc:
                await client.post_results({})
        assert exc.value.status == 422
        assert exc.value.retryable is False


async def test_5xx_retries_then_raises(client):
    with aioresponses() as m:
        for _ in range(3):
            m.post(f"{BASE}/api/v1/instrument/results", status=502, payload={})
        async with client:
            with pytest.raises(LisApiError) as exc:
                await client.post_results({})
        assert exc.value.status == 502
        assert exc.value.retryable is True


async def test_get_orders_pending(client):
    with aioresponses() as m:
        m.get(
            f"{BASE}/api/v1/instrument/orders/pending",
            payload={"success": True, "count": 0, "data": []},
            status=200,
        )
        async with client:
            data = await client.get_orders_pending()
        assert data["count"] == 0


async def test_post_status_online(client):
    with aioresponses() as m:
        m.post(f"{BASE}/api/v1/instrument/status",
               payload={"success": True}, status=200)
        async with client:
            status, _ = await client.post_status({"status": "online"})
        assert status == 200


async def test_post_logs(client):
    with aioresponses() as m:
        m.post(f"{BASE}/api/v1/instrument/logs",
               payload={"success": True}, status=200)
        async with client:
            status, _ = await client.post_log({
                "level": "error", "message": "x", "logged_at": "2026-01-01T00:00:00Z"
            })
        assert status == 200
