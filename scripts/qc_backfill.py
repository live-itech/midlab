"""
scripts/qc_backfill.py — kirim (ulang) hasil QC dari log komunikasi ke EazyLab

Kegunaan: QC yang masuk sebelum alat memakai POST /qc-results (mis. sebelum
upgrade MidLab di sebuah site) tidak pernah sampai ke grafik Levey-Jennings.
Skrip ini mengambil pesan QC asli dari /var/log/midlab/tcp_<id>.comm.log*,
mem-parse ulang dengan driver alat, membentuk payload dengan build_qc_payload()
dan POST memakai LisApiClient — jalur kode yang sama dengan LisBridgeService.
tbl_result TIDAK diubah.

Aman diulang: message_id deterministik (MID{id}-{lot}-L{level}-{run_at}),
sehingga run yang sudah pernah sampai dibalas "duplicate" oleh EazyLab.

Batasan: hanya driver HL7/MLLP yang menghasilkan blok `qc` (saat ini
HL7_MINDRAY_BS200E) dan hanya selama comm log masih tersimpan (rotasi).

Jalankan sebagai user service (hanya dia yang bisa baca config/DB):
    sudo -u midlab /opt/midlab/.venv/bin/python /opt/midlab/scripts/qc_backfill.py \\
        --instrument-id 1 --date 20261013 [--dry-run]

    --date menerima prefiks: 20261013 (satu hari), 202610 (satu bulan).
    --dry-run hanya menampilkan apa yang akan dikirim, tanpa POST.
"""
import argparse
import asyncio
import glob
import json
import logging
import sys
from datetime import datetime

sys.path.insert(0, "/opt/midlab")

from lib.db import TblResult, get_instrument_by_id, get_setting  # noqa: E402
from lib.lis_client import LisApiClient, LisApiError  # noqa: E402
from protocols.base import load_module  # noqa: E402
from services.lis_bridge.result_pusher import (  # noqa: E402
    QcPayloadError, build_qc_payload, summarize_qc_response,
)

LOG_GLOB = "/var/log/midlab/tcp_{id}.comm.log*"


def raw_messages(instrument_id: int):
    """Semua pesan RX dari comm log alat, didecode kembali ke bytes MLLP."""
    for path in sorted(glob.glob(LOG_GLOB.format(id=instrument_id))):
        for line in open(path, errors="replace"):
            if "← RX " not in line or "MSH|" not in line:
                continue
            raw = (line.split("← RX ", 1)[1].rstrip("\n")
                   .replace("<CR>", "\r").replace("\\x0b", "\x0b").replace("\\x1c", "\x1c"))
            yield raw.encode()


def collect_payloads(instrument, module, date_prefix: str) -> list[dict]:
    """
    Payload QC untuk run_at berawalan date_prefix, urut waktu run lalu tes.

    Dedup per (message_id, test_code): alat kadang retransmit pesan yang sama.
    """
    found = {}
    # Parse ulang ribuan pesan (pasien juga) — jangan banjiri log driver produksi.
    logging.disable(logging.WARNING)
    try:
        _collect(instrument, module, date_prefix, found)
    finally:
        logging.disable(logging.NOTSET)
    return [found[k] for k in sorted(found)]


def _collect(instrument, module, date_prefix: str, found: dict):
    for raw in raw_messages(instrument.id):
        parsed = module.parse(raw, {"id": instrument.id, "name": instrument.name})
        qc = parsed.get("qc")
        if not isinstance(qc, dict) or not str(qc.get("run_at", "")).startswith(date_prefix):
            continue
        row = TblResult()
        row.id = 0
        row.result_json = parsed
        row.received_at = datetime.now()
        try:
            payload = build_qc_payload(row, instrument)
        except QcPayloadError as e:
            print(f"  lewati: {e}")
            continue
        key = (payload["message_id"], payload["results"][0]["test_code"])
        found.setdefault(key, payload)


class _OfflineInstrument:
    """Pengganti row tbl_instrument untuk --dry-run tanpa akses DB."""

    def __init__(self, instrument_id: int, protocol: str):
        self.id = instrument_id
        self.name = f"instrument {instrument_id}"
        self.protocol = protocol
        self.lis_instrument_id = "-"


async def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instrument-id", type=int, required=True)
    ap.add_argument("--date", required=True, help="prefiks run_at, mis. 20261013 atau 202610")
    ap.add_argument("--dry-run", action="store_true", help="tampilkan saja, jangan POST")
    ap.add_argument("--protocol", help="hanya untuk --dry-run tanpa DB, mis. HL7_MINDRAY_BS200E")
    args = ap.parse_args()

    if args.protocol:
        if not args.dry_run:
            ap.error("--protocol hanya boleh bersama --dry-run")
        inst = _OfflineInstrument(args.instrument_id, args.protocol)
    else:
        inst = get_instrument_by_id(args.instrument_id)
        if not inst:
            sys.exit(f"Instrument id={args.instrument_id} tidak ditemukan")

    module = load_module(inst.protocol)
    payloads = collect_payloads(inst, module, args.date)
    runs = len({p["message_id"] for p in payloads})
    print(f"Alat: {inst.name} ({inst.lis_instrument_id}), protocol {inst.protocol}")
    print(f"QC ditemukan untuk run_at {args.date}*: {len(payloads)} parameter dari {runs} run\n")

    if args.dry_run:
        for i, p in enumerate(payloads, 1):
            r = p["results"][0]
            print(f"[{i}] {p['message_id']} {r['test_code']}={r['value']} "
                  f"target={r.get('instrument_target_mean')}±{r.get('instrument_target_sd')}")
        return

    base_url = get_setting("lis.base_url", "https://eazy.vespahobby.xyz")
    print(f"POST ke {base_url}\n")
    async with LisApiClient(base_url=base_url, api_key=inst.lis_api_key, retry_max=1) as client:
        for i, payload in enumerate(payloads, 1):
            res = payload["results"][0]
            label = f"[{i}] {payload['message_id']} {res['test_code']}={res['value']}"
            try:
                status, body = await client.post_qc_results(payload)
                _, issues, infos = summarize_qc_response(body)
                item = ((body.get("data") or {}).get("results") or [{}])[0]
                extra = "".join(
                    f" {k}={item[k]}" for k in ("status", "reason", "z_score", "westgard_flags",
                                                "rejected", "control_created", "needs_review",
                                                "level_mismatch")
                    if k in item
                )
                print(f"{label} → HTTP {status}{extra}")
                for line in issues + infos:
                    print(f"      {line}")
            except LisApiError as e:
                print(f"{label} → ERROR HTTP {e.status}: {e.message}")
                if e.body:
                    print(json.dumps(e.body, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
