"""
Driver CUSTOM_GLORY_127 — Glory 127 Chemistry Analyzer.

Unidirectional: alat mendorong hasil lewat tombol "Data Sync", MidLab hanya
mem-parse dan menyimpan. Tidak ada jalur order maupun query — semua method
kontrak ke arah alat di-stub sebagai tidak didukung.

Satu pesan `<<<...>>>` = SATU analit. Sepuluh tes dari satu sampel datang
sebagai sepuluh pesan terpisah yang berbagi `Nr` yang sama, jadi sepuluh baris
tbl_result dengan sample_id identik. Penggabungan (kalau nanti diperlukan)
adalah urusan LIS, bukan driver.

Sumber format: tapping lapangan 2026-08-04 (Hercules, serial 115200 8N1),
dua record berpasangan dengan foto layar Result List alat. Tidak ada dokumen
interface vendor — lihat §8 "Asumsi terbuka" di
docs/superpowers/specs/2026-08-04-glory-127-design.md
"""

from lib.models import ResultObject, TestResult
from lib.utils import get_logger
from lib import timeutil
from protocols.base import BaseProtocolModule

from .constants import (
    EXPECTED_FIELD_COUNT,
    FIELD_CONC,
    FIELD_DATETIME,
    FIELD_DEVICE_NAME,
    FIELD_MODE,
    FIELD_NORMAL_HIGH,
    FIELD_NORMAL_LOW,
    FIELD_NR,
    FIELD_PROGRAM_NAME,
    FIELD_RATE_OD,
    FIELD_REF,
    FIELD_UNIT,
    FRAME_END,
    FRAME_START,
    LOG_PREVIEW_LIMIT,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    STATUS_FINAL,
    UNKNOWN_FIELDS,
)
from .parser import derive_flag, field, format_reference_range, split_fields, to_float


class Glory127Module(BaseProtocolModule):
    """Protocol module untuk Glory 127 chemistry analyzer."""

    # Dibaca ResultReceiver untuk memotong frame dari stream TCP. Tanpa ini
    # cabang generic memperlakukan seluruh isi buffer sebagai satu pesan.
    FRAME_START = FRAME_START
    FRAME_END = FRAME_END

    def __init__(self, now=None):
        """
        Args:
            now: callable() -> str ISO8601. Disuntik di test supaya jam server
                 bisa dikunci; default memakai jam lab dari lib.timeutil.
        """
        self._now = now or timeutil.isoformat
        self._logger = get_logger("glory_127")

    @property
    def PROTOCOL_NAME(self) -> str:
        return PROTOCOL_NAME

    @property
    def VERSION(self) -> str:
        return PROTOCOL_VERSION

    # ============================================================
    # Parsing
    # ============================================================

    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        """Frame `<<<...>>>` → ResultObject dict (satu analit)."""
        fields = split_fields(raw_bytes)

        result = ResultObject(
            instrument_id=instrument.get("id", 0),
            protocol=PROTOCOL_NAME,
            # docs/API.md:262 — "saat hasil di-parse oleh MidLab", bukan jam
            # alat. Jam Glory di lapangan masih default 2000-01-01; memakainya
            # akan menaruh hasil hari ini di tahun 2000 pada LIS.
            message_datetime=self._now(),
        )

        if len(fields) != EXPECTED_FIELD_COUNT:
            result.parse_errors.append(
                f"jumlah field {len(fields)}, diharapkan {EXPECTED_FIELD_COUNT}"
            )

        result.specimen.sample_id = field(fields, FIELD_NR)

        test_code = field(fields, FIELD_PROGRAM_NAME)
        value = field(fields, FIELD_CONC)
        unit = field(fields, FIELD_UNIT)
        low = field(fields, FIELD_NORMAL_LOW)
        high = field(fields, FIELD_NORMAL_HIGH)

        if not test_code:
            result.parse_errors.append("program name kosong")
        if to_float(value) is None:
            result.parse_errors.append(f"CONC bukan angka: {value!r}")

        if test_code and to_float(value) is not None:
            if not unit:
                result.parse_errors.append("satuan kosong")
            result.results.append(
                TestResult(
                    test_code=test_code,
                    test_name=test_code,
                    value=value,
                    unit=unit,
                    reference_range=format_reference_range(low, high),
                    flag=derive_flag(value, low, high),
                    status=STATUS_FINAL,
                )
            )

        payload = result.to_dict()
        # Konteks alat yang tidak punya tempat di kontrak EazyApp. Disimpan di
        # tbl_result supaya tidak ada informasi yang hilang, lalu dibuang oleh
        # build_mid_payload() sebelum push — kunci top-level tak dikenal
        # berisiko 422 (regresi yang menahan 44 hasil F2400).
        payload["instrument_meta"] = {
            "instrument_time": field(fields, FIELD_DATETIME),
            "nr": field(fields, FIELD_NR),
            "ref": field(fields, FIELD_REF),
            "rate_or_od": field(fields, FIELD_RATE_OD),
            "mode": field(fields, FIELD_MODE),
            "device_name": field(fields, FIELD_DEVICE_NAME),
            "unknown": {
                name: field(fields, index) for name, index in UNKNOWN_FIELDS.items()
            },
        }
        return payload

    def should_store_result(self, result_dict: dict, raw_bytes: bytes) -> bool:
        """
        Tolak pesan yang tidak menghasilkan hasil klinis apa pun.

        Baris tbl_result tanpa hasil cuma jadi sampah yang gagal dikirim ke LIS
        berulang kali sampai kena retry_max. Yang ditolak tetap terlihat sebagai
        WARNING — dan LisBridgeService.LogPusher meneruskannya ke EazyApp, jadi
        tidak hilang diam-diam.
        """
        if result_dict.get("results"):
            return True

        preview = (raw_bytes or b"")[:LOG_PREVIEW_LIMIT]
        self._logger.warning(
            f"Pesan tanpa hasil klinis, tidak disimpan "
            f"({'; '.join(result_dict.get('parse_errors') or ['-'])}): {preview!r}"
        )
        return False

    # ============================================================
    # Arah ke alat — tidak didukung (unidirectional)
    # ============================================================

    def format_order(self, order: dict, instrument: dict) -> bytes:
        raise NotImplementedError(
            "Glory 127 unidirectional: alat tidak menerima order dari MidLab"
        )

    def is_enq(self, raw_bytes: bytes) -> bool:
        return False

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        raise NotImplementedError("Glory 127 tidak pernah mengirim query")

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        raise NotImplementedError("Glory 127 tidak punya jalur query")

    def format_query_not_found(self, instrument: dict) -> bytes:
        raise NotImplementedError("Glory 127 tidak punya jalur query")

    def handle_ack(self, raw_bytes: bytes) -> str:
        """Alat tidak memakai handshake ACK/NAK — tidak ada yang perlu dibaca."""
        return "UNKNOWN"
