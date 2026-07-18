"""
lib/lis_client.py — HTTP client wrapper untuk EazyApp LIS Instrument API.

Endpoints:
  GET  /api/v1/instrument                 — info alat
  POST /api/v1/instrument/status          — push status
  GET  /api/v1/instrument/orders/pending  — pull orders
  POST /api/v1/instrument/results         — submit results
  POST /api/v1/instrument/logs            — push log

Semua endpoint pakai Bearer auth per-alat:
  Authorization: Bearer <api_key>

Caller bertanggung jawab kelola session pakai async-with:
    async with LisApiClient(...) as client:
        await client.get_instrument()
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp


class LisApiError(Exception):
    """Error dari LIS API. retryable=True untuk 5xx/network/timeout."""

    def __init__(self, status: int, message: str, body: Any = None, retryable: bool = False):
        super().__init__(f"LIS API error {status}: {message}")
        self.status = status
        self.message = message
        self.body = body
        self.retryable = retryable


class LisApiClient:
    """
    Async HTTP client untuk EazyApp LIS, satu instance per alat (1 Bearer key).

    Retry policy: 5xx & network/timeout di-retry dengan exponential backoff
    sampai retry_max. 4xx tidak di-retry (raise immediately).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 30,
        retry_max: int = 3,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._retry_max = retry_max
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session:
            await self._session.close()
            self._session = None

    # ============================================================
    # Endpoint methods
    # ============================================================

    async def get_instrument(self) -> dict:
        _, body = await self._request("GET", "/api/v1/instrument")
        return body

    async def get_orders_pending(self, priority: str | None = None, limit: int | None = None) -> dict:
        params = {}
        if priority:
            params["priority"] = priority
        if limit:
            params["limit"] = limit
        _, body = await self._request("GET", "/api/v1/instrument/orders/pending", params=params)
        return body

    async def post_results(self, payload: dict) -> tuple[int, dict]:
        return await self._request("POST", "/api/v1/instrument/results", json_body=payload)

    async def post_status(self, payload: dict) -> tuple[int, dict]:
        return await self._request("POST", "/api/v1/instrument/status", json_body=payload)

    async def post_log(self, payload: dict) -> tuple[int, dict]:
        return await self._request("POST", "/api/v1/instrument/logs", json_body=payload)

    # ============================================================
    # Internal
    # ============================================================

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, dict]:
        if not self._session:
            raise RuntimeError("LisApiClient harus dipakai dalam 'async with'")

        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        last_exc: LisApiError | None = None
        for attempt in range(1, self._retry_max + 1):
            try:
                async with self._session.request(
                    method, url,
                    headers=headers,
                    json=json_body if json_body is not None else None,
                    params=params or None,
                ) as resp:
                    text_body = await resp.text()
                    try:
                        body = json.loads(text_body) if text_body else {}
                    except json.JSONDecodeError:
                        body = {"raw": text_body}

                    if 200 <= resp.status < 300:
                        return resp.status, body

                    retryable = resp.status >= 500
                    err = LisApiError(
                        status=resp.status,
                        message=body.get("message") or body.get("error") or text_body[:200],
                        body=body,
                        retryable=retryable,
                    )
                    if not retryable:
                        raise err
                    last_exc = err

            except aiohttp.ClientError as e:
                last_exc = LisApiError(status=0, message=str(e), retryable=True)
            except asyncio.TimeoutError:
                last_exc = LisApiError(status=0, message="timeout", retryable=True)

            if attempt < self._retry_max:
                await asyncio.sleep(min(2 ** (attempt - 1), 10))

        assert last_exc is not None
        raise last_exc
