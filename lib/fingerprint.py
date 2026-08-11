"""
lib/fingerprint.py — Sidik jari isi klinis sebuah hasil pemeriksaan.

Dipakai untuk membedakan kiriman ulang dari alat (isi persis sama) dengan
pemeriksaan baru. Sengaja hanya memperhitungkan apa yang operator sebut
"hasilnya": nomor sampel dan daftar tes beserta nilai, satuan, dan flag.

Yang diabaikan: message_id dan message_datetime (di-generate ulang tiap
parsing), patient/order/comments, serta reference_range dan status yang bisa
berubah tanpa isi pemeriksaan ikut berubah. Kalau semua itu ikut di-hash,
kiriman ulang tidak akan pernah terdeteksi.

Fungsi ini murni: tidak menyentuh database maupun jaringan, dan tidak pernah
melempar exception — pemanggil di jalur simpan hasil tidak boleh jatuh gara-gara
sidik jari.
"""

import hashlib
import json

# Field tes yang ikut menentukan identitas sebuah hasil.
_TEST_FIELDS = ("test_code", "value", "unit", "flag")


def _text(value) -> str:
    """Nilai apa pun → teks rapi. None jadi string kosong, bukan 'None'."""
    if value is None:
        return ""
    return str(value).strip()


def result_fingerprint(result_json) -> str:
    """
    SHA-256 hex (64 karakter) dari isi klinis sebuah hasil.

    Urutan tes tidak berpengaruh: daftarnya diurutkan dulu supaya alat yang
    mengirim tes dalam urutan berbeda tetap menghasilkan sidik jari yang sama.
    """
    data = result_json if isinstance(result_json, dict) else {}

    specimen = data.get("specimen")
    if not isinstance(specimen, dict):
        specimen = {}
    sample_id = _text(specimen.get("sample_id"))

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raw_results = []

    rows = sorted(
        [_text(t.get(k)) for k in _TEST_FIELDS]
        for t in raw_results
        if isinstance(t, dict)
    )

    canonical = json.dumps(
        {"sample_id": sample_id, "results": rows},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
