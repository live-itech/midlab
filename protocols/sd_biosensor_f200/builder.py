"""
protocols/sd_biosensor_f200/builder.py — Builder balasan SD Biosensor STANDARD F

Alat memasang `MSH-15 = AL` (Accept Acknowledgment: Always), jadi ACK **wajib**
— tanpa balasan alat menganggap pengiriman gagal. `MSH-16 = NE` berarti alat
tidak menunggu application-ACK terpisah, jadi satu ACK^R01 sudah cukup.

Bentuk ACK mengikuti byte yang terekam di log (2026-07-13):

    <VT>MSH|^~\\&|Factory|Chemistry Analyzer|||20260713113952||ACK^R01|
        {b73a7615-2687-47bf-862e-b4465cde8332}|P|2.3.1||||||UNICODE<CR>
        MSA|AA|{b73a7615-2687-47bf-862e-b4465cde8332}||||0|<CR><FS><CR>

Dua hal yang sengaja dipertahankan meski secara standar janggal:

1. **MSH-12 = 2.3.1** pada balasan, padahal alat berbicara 2.6. Balasan yang
   tidak sesuai versi biasanya ditolak, tapi log membuktikan alat ini
   menerimanya — sembilan hasil berturut-turut ter-ACK tanpa retry.
2. **MSH-3/MSH-4 = Factory / Chemistry Analyzer**, string bawaan driver lama.

Keduanya dipertahankan supaya driver ini drop-in terhadap perilaku yang sudah
terbukti di lapangan. `build_ack_r01(pcd_conformant=True)` menghasilkan balasan
yang mengikuti PCD-01 (v2.6, identitas alat dipantulkan) bila suatu saat perlu.
"""

from lib.utils import get_logger
from protocols.sd_biosensor_f200.constants import (
    MLLP_START_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    MESSAGE_ENCODING,
    PROC_PRODUCTION,
    ACK_SENDING_APP, ACK_SENDING_FACILITY,
    ACK_HL7_VERSION, ACK_CHARACTER_SET,
    DEVICE_HL7_VERSION,
    ACK_AA, STATUS_CODE_OK,
    EVENT_ACK_R01,
)


logger = get_logger("sd_biosensor_f200_builder")


class SDBiosensorF200Builder:
    """Builder balasan ACK^R01 (arah LIS → alat)."""

    def __init__(self, now=None):
        """
        Args:
            now: callable pengganti jam sistem yang mengembalikan string
                 `YYYYMMDDHHMMSS` — dipakai unit test untuk mengunci MSH-7.
        """
        self._now = now or _now_timestamp

    def _wrap_mllp(self, segments: list) -> bytes:
        """Gabung segment (tiap-tiap diakhiri CR) lalu bungkus envelope MLLP."""
        body = "".join(f"{seg}{SEGMENT_TERMINATOR}" for seg in segments)
        return (
            MLLP_START_BYTE
            + body.encode(MESSAGE_ENCODING, errors="replace")
            + MLLP_TRAILER
        )

    def build_ack_r01(self, context: dict, pcd_conformant: bool = False) -> bytes:
        """
        Bangun ACK^R01 atas pesan ORU^R01 dari alat.

        Control ID alat berupa GUID dalam kurung kurawal
        (`{b73a7615-2687-47bf-862e-b4465cde8332}`) dan harus dipantulkan **utuh
        termasuk kurawalnya** ke MSA-2 — alat mencocokkan string persis untuk
        menutup transaksi.

        Args:
            context: hasil SDBiosensorF200Parser.parse_msh() pesan ORU
            pcd_conformant: True → balasan HL7 v2.6 yang memantulkan identitas
                alat ke MSH-5/MSH-6 sesuai PCD-01. False (default) → byte-identik
                dengan driver lama yang sudah terbukti diterima alat.
        """
        context = context or {}
        control_id = context.get("control_id") or ""

        if pcd_conformant:
            sending_app = "MidLab"
            sending_facility = ""
            receiving_app = context.get("device_id", "")
            version = DEVICE_HL7_VERSION
            charset = "UNICODE UTF-8"
        else:
            sending_app = ACK_SENDING_APP
            sending_facility = ACK_SENDING_FACILITY
            receiving_app = ""
            version = ACK_HL7_VERSION
            charset = ACK_CHARACTER_SET

        msh = FIELD_SEPARATOR.join([
            "MSH",
            ENCODING_CHARACTERS,   # MSH-2
            sending_app,           # MSH-3
            sending_facility,      # MSH-4
            receiving_app,         # MSH-5
            "",                    # MSH-6
            self._now(),           # MSH-7
            "",                    # MSH-8
            EVENT_ACK_R01,         # MSH-9
            control_id,            # MSH-10
            PROC_PRODUCTION,       # MSH-11
            version,               # MSH-12
            "", "", "", "", "",    # MSH-13..17
            charset,               # MSH-18
        ])

        msa = FIELD_SEPARATOR.join(
            ["MSA", ACK_AA, control_id, "", "", "", STATUS_CODE_OK, ""]
        )

        logger.info(
            f"ACK^R01 dibangun untuk control_id={control_id or '-'}"
            f"{' (PCD-01 conformant)' if pcd_conformant else ''}"
        )
        return self._wrap_mllp([msh, msa])


def _now_timestamp() -> str:
    """Waktu lokal lab sekarang dalam format `YYYYMMDDHHMMSS`."""
    from lib import timeutil
    return timeutil.stamp("%Y%m%d%H%M%S")
