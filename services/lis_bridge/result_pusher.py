"""
services/lis_bridge/result_pusher.py — Push tbl_result pending ke LIS.

Loop: poll tbl_result → POST /results (atau /qc-results untuk baris QC)
→ update send_status.
2xx → 'sent'. 422 → 'failed' permanen. 5xx/network → bump retry_count;
'pending' kalau belum exhausted, 'failed' kalau retry_count>=retry_max.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from lib import timeutil
from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger

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
# Kontrak EazyApp minta KELUARGA protokol ("ASTM"/"HL7"), bukan nama driver,
# dan membatasi field ini 20 karakter (docs/API.md:261).
_PROTOCOL_MAP = {
    "COBAS_C111": "ASTM",
    # Format proprietary <<<...>>>, bukan keluarga ASTM maupun HL7. Dipetakan
    # ke ASTM karena kontrak hanya mengenal itu; field ini cuma label keluarga,
    # bukan penentu parsing.
    "CUSTOM_GLORY_127": "ASTM",
}

# Prefix nama driver → keluarga protokol. Dipakai sebagai aturan umum supaya
# driver baru (HL7_SD_BIOSENSOR_F2400, dst) tidak perlu didaftarkan satu per
# satu — dulu nama driver diteruskan apa adanya dan yang >20 karakter kena
# HTTP 422 sehingga result mandek permanen.
_PROTOCOL_FAMILY_PREFIXES = ("HL7", "ASTM")

# Batas panjang kolom `protocol` di sisi EazyApp.
_PROTOCOL_MAX_LEN = 20


def _normalize_protocol(raw: str) -> str:
    """Nama protocol internal → keluarga protokol wire EazyApp."""
    proto = (raw or "").upper()
    if not proto:
        return "ASTM"
    if proto in _PROTOCOL_MAP:
        return _PROTOCOL_MAP[proto]
    for family in _PROTOCOL_FAMILY_PREFIXES:
        if proto.startswith(family):
            return family
    # Tak dikenal: jangan sampai kena 422 lagi — potong ke batas kontrak.
    return proto[:_PROTOCOL_MAX_LEN]

# Pseudo-result yang dihasilkan parser tapi bukan hasil klinis pasien
# (kalibrasi, absorbansi mentah). Tidak boleh dikirim ke EazyApp.
_NON_CLINICAL_STATUS = {"calibration", "absorbance_raw"}


def _to_iso8601(value) -> str:
    """
    ASTM datetime (YYYYMMDD[HHMM[SS]]) → ISO8601 ber-offset lab.
    Passthrough kalau sudah ISO / format lain / kosong (jangan dirusak).

    Alat emit jam lokal tanpa offset; EazyApp (Laravel) kontraknya pakai
    ISO8601 ber-offset (lihat Postman collection: +07:00).
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
        return timeutil.isoformat(datetime.strptime(v, fmt))
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
        # received_at naive dari DB = jam lokal; isoformat() menambah offset.
        # Tetap fallback ke `sekarang` kalau received_at kosong — kontrak
        # EazyApp mewajibkan message_datetime terisi.
        iso_mdt = timeutil.isoformat(result_row.received_at) or timeutil.isoformat()
    payload["message_datetime"] = iso_mdt

    # protocol: nama driver internal → keluarga protokol wire EazyApp.
    payload["protocol"] = _normalize_protocol(payload.get("protocol"))

    # specimen.collected_at → ISO8601.
    spec = payload.get("specimen")
    if isinstance(spec, dict) and spec.get("collected_at"):
        spec["collected_at"] = _to_iso8601(spec["collected_at"])

    # results: drop non-klinis + normalize status code + reference_range.
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
            # ASTM pakai '\' sbg separator range (low\high); EazyApp '-'.
            rng = r.get("reference_range")
            if isinstance(rng, str) and "\\" in rng:
                r["reference_range"] = rng.replace("\\", "-")
            clean.append(r)
        payload["results"] = clean

    # comments tidak ada di kontrak EazyApp.
    payload.pop("comments", None)

    # Konteks khusus alat yang disimpan driver di tbl_result (mis. jam internal
    # Glory 127 dan field yang belum diketahui artinya). Berguna untuk audit di
    # MidLab, tapi kunci top-level di luar kontrak berisiko ditolak 422.
    payload.pop("instrument_meta", None)

    return payload


# ============================================================
# QC → POST /qc-results (kontrak EazyLab)
# ============================================================

# Level kontrol dari alat → format yang dikenal EazyLab (1/2/3). Huruf L/M/H
# belum didukung di sana.
_QC_LEVEL_MAP = {"L": "1", "M": "2", "H": "3"}


class QcPayloadError(ValueError):
    """Baris QC tidak bisa dibentuk jadi payload valid — tidak ada gunanya retry."""


def is_qc_row(result_row) -> bool:
    """Baris hasil QC: driver menandai specimen.sample_type = "qc"."""
    rj = result_row.result_json or {}
    spec = rj.get("specimen") or {}
    return isinstance(rj.get("qc"), dict) or spec.get("sample_type") == "qc"


def _qc_level(raw: str) -> str:
    level = (raw or "").strip()
    return _QC_LEVEL_MAP.get(level.upper(), level)


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_qc_payload(result_row, instrument) -> dict:
    """
    Bangun payload POST /qc-results dari blok `qc` di result_json.

    message_id deterministik {alat}-{lot}-L{level}-{run_at}: EazyLab dedup per
    kontrol+parameter berdasar message_id, jadi kiriman ulang run yang sama
    (alat retransmit, tombol kirim ulang) harus menghasilkan id yang sama, dan
    id tidak boleh ikut ter-reset seperti nomor urut.

    Raises:
        QcPayloadError bila field wajib EazyLab (lot, level, run_at) kosong.
    """
    rj = result_row.result_json or {}
    qc = rj.get("qc")
    if not isinstance(qc, dict):
        raise QcPayloadError("hasil QC tanpa data kontrol (lot/level) dari driver")

    lot = (qc.get("lot_number") or "").strip()
    level = _qc_level(qc.get("level"))
    run_at = _to_iso8601(qc.get("run_at") or "")
    missing = [n for n, v in (("lot_number", lot), ("level", level), ("run_at", run_at)) if not v]
    if missing:
        raise QcPayloadError(f"field QC wajib kosong: {', '.join(missing)}")

    run_key = re.sub(r"[^0-9T]", "", run_at[:19])     # 2026-07-21T12:54:12 → 20260721T125412
    message_id = f"MID{instrument.id}-{lot}-L{level}-{run_key}"

    targets = qc.get("targets") or {}
    results = []
    for r in rj.get("results") or []:
        if not isinstance(r, dict) or not r.get("test_code"):
            continue
        item = {
            "test_code": r.get("test_code", ""),
            "test_name": r.get("test_name", ""),
            "value": r.get("value", ""),
            "unit": r.get("unit", ""),
            "flag": r.get("flag", ""),
        }
        target = targets.get(item["test_code"]) or {}
        mean, sd = _to_float(target.get("mean")), _to_float(target.get("sd"))
        if mean is not None:
            item["instrument_target_mean"] = mean
        if sd is not None:
            item["instrument_target_sd"] = sd
        results.append(item)
    if not results:
        raise QcPayloadError("hasil QC tanpa parameter")

    payload = {
        "mid_version": "1.0",
        "message_id": message_id,
        "message_datetime": (
            _to_iso8601(rj.get("message_datetime"))
            or timeutil.isoformat(result_row.received_at)
            or timeutil.isoformat()
        ),
        "protocol": _normalize_protocol(rj.get("protocol")),
        "qc": {"lot_number": lot, "level": level},
        "run_at": run_at,
        "results": results,
    }
    for key in ("control_name", "sample_id", "expiry_date"):
        if qc.get(key):
            payload["qc"][key] = qc[key]
    return payload


def summarize_qc_response(body: dict) -> tuple[str, list[str], list[str]]:
    """
    Ringkas respons 201 /qc-results.

    Kontrak EazyLab (13 Okt 2026):
        data.controls_created          int  — kontrol baru dari payload ini
        data.results[].control_created bool — kontrol parameter ini baru dibuat
        data.results[].needs_review    bool — status kontrol; tetap true sampai
                                              ditinjau, jadi TIDAK dicatat sendiri
        data.results[].level_mismatch  bool — level kiriman ≠ level kontrol
    Ketiga field per parameter hanya ada untuk recorded/duplicate.

    Returns:
        (ringkasan satu baris,
         masalah per parameter — unmapped / ditolak Westgard / level_mismatch,
         info per parameter — kontrol baru dibuat)
    """
    data = (body or {}).get("data") or {}
    summary = (
        f"recorded={data.get('recorded_count', 0)} "
        f"duplicate={data.get('duplicate_count', 0)} "
        f"unmapped={data.get('unmapped_count', 0)} "
        f"rejected={data.get('rejected_count', 0)}"
    )
    if data.get("controls_created"):
        summary += f" controls_created={data['controls_created']}"
    issues, infos = [], []
    for r in data.get("results") or []:
        if not isinstance(r, dict):
            continue
        code = r.get("test_code") or "?"
        if r.get("status") == "unmapped":
            issues.append(f"{code}: unmapped ({r.get('reason') or '-'})")
        elif r.get("rejected"):
            flags = r.get("westgard_flags") or []
            flags = ",".join(flags) if isinstance(flags, list) else str(flags)
            issues.append(f"{code}: ditolak Westgard ({flags or '-'})")
        # Level tak cocok berarti setting level alat atau master kontrol perlu
        # dibetulkan — relevan di tiap kiriman, jadi selalu dicatat.
        if r.get("level_mismatch") is True:
            issues.append(f"{code}: level kiriman beda dengan level kontrol (level_mismatch)")
        # needs_review hanya dicatat bersama control_created: nilainya tetap
        # true di setiap kiriman sampai ditinjau, dan akan membanjiri log.
        if r.get("control_created") is True:
            info = f"{code}: kontrol dibuat otomatis oleh EazyLab"
            if r.get("needs_review") is True:
                info += ", perlu ditinjau petugas lab"
            infos.append(info)
    return summary, issues, infos


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
        if is_qc_row(row):
            await self._send_qc(row)
            return
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
            await self._handle_error(row, e)

    async def _send_qc(self, row):
        """Kirim satu baris QC ke POST /qc-results."""
        try:
            payload = build_qc_payload(row, self._instrument)
        except QcPayloadError as e:
            await asyncio.get_event_loop().run_in_executor(
                None, self._update_status, row.id, "failed", f"QC: {e}",
            )
            self._logger.warning(f"result_id={row.id} QC tidak dikirim: {e}")
            return

        try:
            status, body = await self._client.post_qc_results(payload)
            if not 200 <= status < 300:
                raise LisApiError(status=status, message="unexpected status", retryable=False)
        except LisApiError as e:
            await self._handle_error(row, e)
            return

        summary, issues, infos = summarize_qc_response(body)
        # Unmapped tetap 'sent': data tertampung di EazyLab ("QC Belum
        # Terpetakan") dan masuk grafik bila dikirim ulang dengan message_id
        # yang sama setelah kontrol/mapping didaftarkan. Masalahnya dicatat di
        # error_message supaya terlihat di Result Monitor.
        note = "; ".join(issues + infos) or None
        await asyncio.get_event_loop().run_in_executor(
            None, self._update_status, row.id, "sent", note,
        )
        self._logger.info(f"result_id={row.id} QC sent {payload['message_id']} {summary}")
        for issue in issues:
            self._logger.warning(f"result_id={row.id} QC {payload['message_id']} {issue}")
        for info in infos:
            self._logger.info(f"result_id={row.id} QC {payload['message_id']} {info}")

    async def _handle_error(self, row, e: LisApiError):
        """422/4xx → failed permanen; 5xx/network → retry sampai retry_max."""
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
