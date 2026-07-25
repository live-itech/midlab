"""
services/tap/detect.py — Deteksi protokol + hint baud rate.

Heuristik deteksi diambil langsung dari PANDUAN-ALAT-BARU.md bab 2, yang selama
ini dijalankan manual lewat Wireshark:
  - byte pertama 0x05 (ENQ)  → kemungkinan ASTM
  - ada string "MSH|"        → kemungkinan HL7

Deteksi hanya MENYARANKAN; operator yang memutuskan. Responder tidak pernah
berganti sendiri di tengah sesi.
"""

from protocols.astm.constants import ENQ
from protocols.hl7.constants import MLLP_START
from services.tap.responder.base import BaseResponder
from services.tap.responder.astm import AstmResponder
from services.tap.responder.mllp import MllpResponder
from services.tap.responder.raw import RawResponder


# Setelah sekian byte tanpa satu pun pesan valid, curigai setelan baud.
BAUD_HINT_THRESHOLD = 256

_RESPONDERS = {
    "ASTM": AstmResponder,
    "HL7": MllpResponder,
    "RAW": RawResponder,
}


def detect_protocol(data: bytes) -> str | None:
    """
    Tebak basis protokol dari byte awal.

    Returns:
        'ASTM', 'HL7', atau None bila tidak dikenali.
    """
    if not data:
        return None
    if data[0] == ENQ:
        return "ASTM"
    if data[0] == MLLP_START:
        return "HL7"
    if b"MSH|" in data:
        return "HL7"
    return None


def should_hint_baud(basis: str, bytes_seen: int, messages_found: int) -> bool:
    """
    True bila operator perlu diingatkan memeriksa baud rate.

    Salah setel baud menghasilkan byte sampah yang persis mirip masalah
    protokol — jebakan klasik yang memakan waktu berjam-jam. Bila basisnya
    ASTM/HL7 tapi tidak satu pun frame terbentuk setelah cukup banyak byte,
    setelan serial-nya yang lebih mungkin salah, bukan alatnya.

    RAW dikecualikan: ia memang tidak punya konsep pesan.
    """
    if basis == "RAW":
        return False
    return bytes_seen >= BAUD_HINT_THRESHOLD and messages_found == 0


def is_query(message: bytes, basis: str) -> bool:
    """
    True bila pesan ini alat MEMINTA order (bukan mengirim hasil).

    Dipakai mode `bidi` untuk menandai query di capture. MidLab sengaja TIDAK
    menjawabnya: bentuk jawaban berbeda per protokol dan per tipe query, dan
    mengarangnya bisa membuat alat mencatat error — mengotori capture yang justru
    sedang diamati. Yang dikejar adalah format query-nya.

    HL7  : MSH-9 bertipe QBP / QRY
    ASTM : ada record Q dalam sesi
    """
    if not message or basis == "RAW":
        return False

    if basis == "HL7":
        teks = message.decode("utf-8", errors="replace")
        # Pesan mungkin masih terbungkus MLLP (<VT>..<FS><CR>); buang byte
        # framing di tepi tiap segmen sebelum mencocokkan MSH.
        msh = next(
            (s for s in teks.split("\r")
             if s.strip("\x0b\x1c\r\n").startswith("MSH")),
            None,
        )
        if msh is None:
            return False
        msh = msh.strip("\x0b\x1c\r\n")
        f = msh.split("|")
        tipe = f[8] if len(f) > 8 else ""
        return tipe.startswith("QBP") or tipe.startswith("QRY")

    if basis == "ASTM":
        # Record ASTM: <seq-digit><tipe>|... — cari record bertipe Q.
        teks = message.decode("ascii", errors="replace")
        for baris in teks.replace("\x02", "\n").split("\n"):
            b = baris.strip()
            if len(b) >= 2 and b[0].isdigit() and b[1] == "Q":
                return True
        return False

    return False


def build_responder(basis: str) -> BaseResponder:
    """Buat responder sesuai basis protokol."""
    kelas = _RESPONDERS.get(basis)
    if kelas is None:
        raise ValueError(
            f"Basis protokol '{basis}' tidak dikenali. "
            f"Tersedia: {list(_RESPONDERS)}"
        )
    return kelas()
