"""
services/lis_bridge/result_pusher.py — Push tbl_result pending ke LIS.

Loop: poll tbl_result → POST /results → update send_status.
2xx → 'sent'. 422 → 'failed' permanen. 5xx/network → bump retry_count;
'pending' kalau belum exhausted, 'failed' kalau retry_count>=retry_max.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger


# Offset zona waktu lab. Alat emit jam lokal tanpa offset; EazyApp (Laravel)
# kontraknya pakai ISO8601 ber-offset (lihat Postman collection: +07:00).
# Ubah di sini kalau lab pindah zona waktu.
_LAB_TZ = timezone(timedelta(hours=7))

# ASTM datetime: YYYYMMDD atau YYYYMMDDHHMMSS (12/14 digit juga ditoleransi).
_ASTM_DT_RE = re.compile(r"^\d{8}(\d{4}|\d{6})?$")

# Kode status hasil ASTM (Cobas/umumnya) → label yang dipakai kontrak EazyApp.
_STATUS_MAP = {
    "F": "final",
    "P": "preliminary",
    "C": "correction",
    "X": "cancelled",
    "I": "pending",
    "R": "final",
}

# Nama protocol internal MidLab → wire protocol yang dikenal EazyApp.
_PROTOCOL_MAP = {
    "COBAS_C111": "ASTM",
}

# Pseudo-result yang dihasilkan parser tapi bukan hasil klinis pasien
# (kalibrasi, absorbansi mentah). Tidak boleh dikirim ke EazyApp.
_NON_CLINICAL_STATUS = {"calibration", "absorbance_raw"}


def _to_iso8601(value) -> str:
    """
    ASTM datetime (YYYYMMDD[HHMM[SS]]) → ISO8601 ber-offset lab.
    Passthrough kalau sudah ISO / format lain / kosong (jangan dirusak).
    """
    if not value or not isinstance(value, str):
        return value or ""
    v = value.strip()
    if not v or not _ASTM_DT_RE.match(v):
        return v
    fmt = {8: "%Y%m%d", 12: "%Y%m%d%H%M", 14: "%Y%m%d%H%M%S"}.get(len(v))
    if fmt is None:
        return v
    try:
        dt = datetime.strptime(v, fmt).replace(tzinfo=_LAB_TZ)
        return dt.isoformat()
    except ValueError:
        return v


def build_mid_payload(result_row, instrument) -> dict:
    """
    Build MID v1.0 payload sesuai kontrak EazyApp Instrument API.

    Normalisasi di boundary egress (tbl_result.result_json tetap menyimpan
    data parsed mentah untuk audit):
    - instrument_id → string LIS
    - message_datetime / specimen.collected_at → ISO8601
    - protocol internal → wire protocol EazyApp
    - results[].status kode ASTM → label
    - buang pseudo-result kalibrasi/absorbansi
    - drop `comments` (tidak ada di kontrak EazyApp)
    """
    payload = dict(result_row.result_json or {})
    payload["instrument_id"] = instrument.lis_instrument_id
    payload["mid_version"] = "1.0"
    payload.setdefault("message_id", f"MSG-{instrument.id}-{result_row.id}")

    # message_datetime: ASTM→ISO; fallback ke received_at kalau kosong.
    iso_mdt = _to_iso8601(payload.get("message_datetime"))
    if not iso_mdt:
        ts = result_row.received_at or datetime.now(timezone.utc)
        iso_mdt = ts.isoformat()
    payload["message_datetime"] = iso_mdt

    # protocol: map nama internal → wire protocol EazyApp.
    proto = (payload.get("protocol") or "").upper()
    payload["protocol"] = _PROTOCOL_MAP.get(proto, proto or "ASTM")

    # specimen.collected_at → ISO8601.
    spec = payload.get("specimen")
    if isinstance(spec, dict) and spec.get("collected_at"):
        spec["collected_at"] = _to_iso8601(spec["collected_at"])

    # results: drop non-klinis + normalize status code.
    results = payload.get("results")
    if isinstance(results, list):
        clean = []
        for r in results:
            if not isinstance(r, dict):
                continue
            if r.get("status") in _NON_CLINICAL_STATUS:
                continue
            code = (r.get("status") or "").upper()
            r["status"] = _STATUS_MAP.get(code, r.get("status") or "")
            clean.append(r)
        payload["results"] = clean

    # comments tidak ada di kontrak EazyApp.
    payload.pop("comments", None)

    return payload


class ResultPusher:
    def __init__(
        self,
        instrument,
        client: LisApiClient,
        poll_results_fn,        # callable() -> list[TblResult]
        update_status_fn,       # callable(result_id, status, error, *, increment_retry=False)
        retry_max: int,
        batch_size: int = 50,
        poll_interval: float = 5.0,
    ):
        self._instrument = instrument
        self._client = client
        self._poll_results = poll_results_fn
        self._update_status = update_status_fn
        self._retry_max = retry_max
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._logger = get_logger(f"lis_bridge.{instrument.id}.result_pusher")
        self._running = True

    async def run_once(self):
        rows = await asyncio.get_event_loop().run_in_executor(None, self._poll_results)
        for row in rows or []:
            if not self._running:
                break
            await self._send(row)

    async def run_forever(self):
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                self._logger.error(f"result_pusher cycle error: {e}")
            await asyncio.sleep(self._poll_interval)

    def stop(self):
        self._running = False

    async def _send(self, row):
        payload = build_mid_payload(row, self._instrument)
        try:
            status, _ = await self._client.post_results(payload)
            if 200 <= status < 300:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_status, row.id, "sent", None,
                )
                self._logger.info(f"result_id={row.id} sent")
                return
            raise LisApiError(status=status, message="unexpected status", retryable=False)
        except LisApiError as e:
            if not e.retryable:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_status, row.id, "failed", f"{e.status}: {e.message}",
                )
                self._logger.warning(f"result_id={row.id} failed (permanent): {e}")
                return
            next_count = (row.retry_count or 0) + 1
            if next_count >= self._retry_max:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_status, row.id, "failed",
                    f"{e.status}: {e.message} (retry exhausted)",
                )
                self._logger.warning(f"result_id={row.id} retry exhausted")
            else:
                await asyncio.get_event_loop().run_in_executor(
                    None, _call_with_retry_kwarg, self._update_status,
                    row.id, "pending", f"{e.status}: {e.message}",
                )
                self._logger.info(f"result_id={row.id} retry {next_count}/{self._retry_max}")


def _call_with_retry_kwarg(fn, *args):
    return fn(*args, increment_retry=True)
