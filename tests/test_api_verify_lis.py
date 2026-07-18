import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from aioresponses import aioresponses

from services.web_console.api import app


def test_verify_lis_endpoint_returns_lis_info():
    client = TestClient(app)
    with aioresponses() as m:
        m.get(
            "https://eazy.vespahobby.xyz/api/v1/instrument",
            payload={
                "success": True,
                "data": {"instrument": {
                    "instrument_id": "INST-Z", "name": "Mock",
                    "vendor": "X", "model": "Y",
                }},
            },
            status=200,
        )
        r = client.post("/api/instruments/1/verify-lis", json={"lis_api_key": "k"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["lis_instrument_id"] == "INST-Z"
