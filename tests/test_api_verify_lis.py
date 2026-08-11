import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from unittest.mock import patch

from fastapi.testclient import TestClient
from aioresponses import aioresponses

from services.web_console.api import app

LIS_BASE_URL = "https://eazy.vespahobby.xyz"


def test_verify_lis_endpoint_returns_lis_info():
    client = TestClient(app)
    # Endpoint mengambil base URL lewat get_setting(), yang membaca tbl_settings
    # di DB nyata — nilainya beda-beda per mesin, jadi tanpa patch ini URL yang
    # dipanggil tidak cocok dengan yang di-mock dan tesnya gagal di deployment
    # mana pun yang settings-nya bukan default. Patch-nya menyasar lib.db karena
    # endpoint meng-import get_setting secara lokal di dalam fungsi.
    with patch("lib.db.get_setting", return_value=LIS_BASE_URL), aioresponses() as m:
        m.get(
            f"{LIS_BASE_URL}/api/v1/instrument",
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
