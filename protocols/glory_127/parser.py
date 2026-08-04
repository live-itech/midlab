"""
Parser record Glory 127 — fungsi murni, tanpa I/O maupun state.

Semua fungsi di sini menerima teks/angka apa adanya dari kabel dan tidak
pernah melempar exception: alat lapangan mengirim apa saja, dan satu record
rusak tidak boleh menjatuhkan koneksi alat lain.
"""

from .constants import (
    ENCODING,
    FIELD_SEPARATOR,
    FLAG_HIGH,
    FLAG_LOW,
    FLAG_NONE,
    FLAG_NORMAL,
    FRAME_END,
    FRAME_START,
)


def strip_frame(raw_bytes: bytes) -> bytes:
    """
    Buang delimiter `<<<`/`>>>` bila ada.

    Receiver menyerahkan frame lengkap berikut delimiternya (supaya raw_data
    byte-identik dengan kabel), tapi parse() juga harus tahan dipanggil dengan
    isi yang sudah telanjang — mis. dari test atau replay manual.
    """
    body = (raw_bytes or b"").strip()
    if body.startswith(FRAME_START):
        body = body[len(FRAME_START):]
    if body.endswith(FRAME_END):
        body = body[: -len(FRAME_END)]
    return body


def split_fields(raw_bytes: bytes) -> list[str]:
    """Frame → daftar field teks, sudah dibuang delimiter dan spasi tepinya."""
    body = strip_frame(raw_bytes).decode(ENCODING, errors="replace")
    return [f.strip() for f in body.split(FIELD_SEPARATOR)]


def field(fields: list[str], index: int) -> str:
    """Ambil field ke-`index`, string kosong bila record lebih pendek."""
    return fields[index] if 0 <= index < len(fields) else ""


def to_float(value: str):
    """Angka desimal → float; None bila bukan angka (mis. '----', '***', '')."""
    try:
        return float((value or "").strip())
    except (TypeError, ValueError):
        return None


def trim_number(value: str) -> str:
    """
    `0.0000` → `0`, `40.0000` → `40`, `3.5000` → `3.5`.

    Kontrak EazyApp mencontohkan reference_range bergaya `"3.5-5.5"`
    (docs/API.md:267); empat desimal nol dari alat cuma bikin ramai di layar
    LIS tanpa menambah informasi.
    """
    text = (value or "").strip()
    if "." not in text:
        return text
    trimmed = text.rstrip("0").rstrip(".")
    return trimmed if trimmed not in ("", "-") else "0"


def format_reference_range(low: str, high: str) -> str:
    """
    `0.0000` + `40.0000` → `0-40`.

    Kosong bila salah satu batas bukan angka, atau bila keduanya nol —
    alat memakai 0/0 untuk menandai rentang yang belum diisi, dan
    memancarkannya sebagai "0-0" akan terbaca sebagai rentang sungguhan.
    """
    lo, hi = to_float(low), to_float(high)
    if lo is None or hi is None:
        return ""
    if lo == 0 and hi == 0:
        return ""
    return f"{trim_number(low)}-{trim_number(high)}"


def derive_flag(value: str, low: str, high: str) -> str:
    """
    Turunkan H/L/N dari CONC terhadap Normal Low/High.

    Glory 127 TIDAK mengirim flag di kabel — foto layar menampilkan `H` untuk
    LDL 189.084 (batas atas 130) sementara string serialnya tidak memuatnya
    sama sekali. Driver MidLab lain semuanya mengambil flag mentah dari wire;
    ini satu-satunya yang harus menghitungnya sendiri.
    """
    conc, lo, hi = to_float(value), to_float(low), to_float(high)
    if conc is None or lo is None or hi is None:
        return FLAG_NONE
    if lo == 0 and hi == 0:
        return FLAG_NONE
    if conc > hi:
        return FLAG_HIGH
    if conc < lo:
        return FLAG_LOW
    return FLAG_NORMAL
