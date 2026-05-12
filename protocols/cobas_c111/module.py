"""
protocols/cobas_c111/module.py — Cobas c-111 Protocol Module (unidirectional).

Implements BaseProtocolModule. Hanya parse() yang di-implement penuh;
method bidirectional (format_order, query handling, dll) raise
NotImplementedError karena modul ini intentionally unidirectional.

Loaded dynamically oleh protocols.base.load_module("COBAS_C111").
"""

from lib.utils import get_logger
from lib.models import (
    ResultObject, PatientInfo, SpecimenInfo, OrderInfo, TestResult,
)
from protocols.base import BaseProtocolModule
from protocols.cobas_c111.constants import (
    ENQ, ACK, NAK, EOT,
    ENQ_BYTE, ACK_BYTE, NAK_BYTE, EOT_BYTE,
    PROTOCOL_NAME, PROTOCOL_VERSION,
    REC_HEADER, REC_PATIENT, REC_ORDER, REC_RESULT, REC_COMMENT,
    REC_TERMINATOR, REC_MANUFACTURER,
    M_SUBTYPE_CR, M_SUBTYPE_RR,
)
from protocols.cobas_c111.parser import FrameDecoder, RecordParser


_UNIDIRECTIONAL_MSG = "Cobas c-111 module is unidirectional — bidirectional methods not implemented"


class CobasC111Module(BaseProtocolModule):
    """
    Protocol module untuk Roche Cobas c-111 (Host Interface Manual v2.2).

    Unidirectional only. Lower-layer ASTM E1381 handshake reused dari
    services/tcp_socket/receiver.py:_handle_astm_data via dispatcher branch.
    """

    def __init__(self):
        self._decoder = FrameDecoder()
        self._record_parser = RecordParser()
        self._logger = get_logger("cobas_c111")

    @property
    def PROTOCOL_NAME(self) -> str:
        return PROTOCOL_NAME

    @property
    def VERSION(self) -> str:
        return PROTOCOL_VERSION

    # ============================================================
    # parse() — receiver memanggil ini dengan akumulasi bytes setelah EOT
    # ============================================================
    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        instrument_id = instrument.get("id", 0)
        self._logger.info(
            f"Mulai parse {len(raw_bytes)} bytes dari instrument {instrument_id}"
        )

        result = ResultObject(
            instrument_id=instrument_id,
            protocol=PROTOCOL_NAME,
        )

        records_str, frame_errors = self._decoder.decode(raw_bytes)
        result.parse_errors.extend(frame_errors)

        if not records_str:
            self._logger.warning("Tidak ada record valid setelah decode")
            return result.to_dict()

        last_test_code: str = ""  # untuk attribusi C-after-R

        for rec_str in records_str:
            if not rec_str:
                continue
            rtype = rec_str[0].upper()
            try:
                if rtype == REC_HEADER:
                    h = self._record_parser.parse_h(rec_str)
                    if h.get("message_datetime"):
                        result.message_datetime = h["message_datetime"]
                elif rtype == REC_PATIENT:
                    p = self._record_parser.parse_p(rec_str)
                    result.patient = PatientInfo(
                        patient_id=p.get("laboratory_pat_id", ""),
                    )
                elif rtype == REC_ORDER:
                    o = self._record_parser.parse_o(rec_str)
                    result.specimen = SpecimenInfo(
                        sample_id=o.get("sample_id", ""),
                        sample_type=o.get("action_code", ""),  # N/Q/A/C
                        collected_at=o.get("result_datetime", ""),
                    )
                    result.order = OrderInfo(
                        order_id=o.get("sample_id", ""),
                        panel=o.get("report_type", ""),  # F/X/Z/Q
                    )
                elif rtype == REC_RESULT:
                    r = self._record_parser.parse_r(rec_str)
                    last_test_code = r.get("test_code", "")
                    result.results.append(TestResult(
                        test_code=r.get("test_code", ""),
                        test_name="",
                        value=r.get("value", ""),
                        unit=r.get("unit", ""),
                        reference_range=r.get("reference_range", ""),
                        flag=r.get("flag", ""),
                        status=r.get("result_status", ""),
                    ))
                elif rtype == REC_COMMENT:
                    c = self._record_parser.parse_c(rec_str)
                    code = c.get("flag_code", "")
                    text = c.get("flag_comment", "")
                    body = f"{code}: {text}" if text else code
                    attribution = f"result[{last_test_code}]" if last_test_code else "order"
                    result.comments.append(f"{attribution}: {body}")
                elif rtype == REC_MANUFACTURER:
                    m = self._record_parser.parse_m(rec_str)
                    if m.get("_unknown"):
                        result.parse_errors.append(
                            f"Unknown M subtype: {m.get('subtype', '')}"
                        )
                        self._logger.info(f"M record subtype tak dikenal: {m.get('subtype')}")
                    elif m.get("subtype") == M_SUBTYPE_CR:
                        result.results.append(TestResult(
                            test_code=m.get("test_application_code", ""),
                            test_name=m.get("test_short_name", ""),
                            value=m.get("result_state", ""),
                            unit=m.get("unit", ""),
                            reference_range="",
                            flag=m.get("calibration_method", ""),
                            status="calibration",
                        ))
                        self._logger.info("M.CR calibration result captured (summary only)")
                    elif m.get("subtype") == M_SUBTYPE_RR:
                        result.results.append(TestResult(
                            test_code=f"RR-{m.get('sequence', '')}",
                            test_name="",
                            value=m.get("effective_signal", ""),
                            unit="",
                            reference_range="",
                            flag="",
                            status="absorbance_raw",
                        ))
                        self._logger.info("M.RR absorbance summary captured (raw payload dropped)")
                elif rtype == REC_TERMINATOR:
                    l = self._record_parser.parse_l(rec_str)
                    code = l.get("termination_code", "N")
                    if code and code != "N":
                        result.parse_errors.append(f"termination={code}")
                else:
                    result.parse_errors.append(f"Unknown record type: {rtype}")
                    self._logger.warning(f"Record type tak dikenal: {rtype}")
            except Exception as e:
                result.parse_errors.append(
                    f"Parse failure on '{rec_str[:40]}...': {e}"
                )
                self._logger.warning(f"Parse exception: {e}")

        self._logger.info(
            f"Parse selesai: {len(result.results)} results, "
            f"{len(result.comments)} comments, "
            f"{len(result.parse_errors)} errors"
        )
        return result.to_dict()

    # ============================================================
    # is_enq / handle_ack — receiver butuh ini (lower-layer dispatch)
    # ============================================================
    def is_enq(self, raw_bytes: bytes) -> bool:
        """True bila byte pertama adalah ENQ. Q-record query OUT-OF-SCOPE."""
        return bool(raw_bytes) and raw_bytes[0:1] == ENQ_BYTE

    def handle_ack(self, raw_bytes: bytes) -> str:
        """Return 'ACK', 'NAK', 'EOT', atau 'UNKNOWN'."""
        if not raw_bytes:
            return "UNKNOWN"
        first = raw_bytes[0:1]
        if first == ACK_BYTE:
            return "ACK"
        if first == NAK_BYTE:
            return "NAK"
        if first == EOT_BYTE:
            return "EOT"
        return "UNKNOWN"

    # ============================================================
    # Bidirectional methods — intentionally not implemented
    # ============================================================
    def format_order(self, order: dict, instrument: dict) -> bytes:
        raise NotImplementedError(_UNIDIRECTIONAL_MSG)

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        raise NotImplementedError(_UNIDIRECTIONAL_MSG)

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        raise NotImplementedError(_UNIDIRECTIONAL_MSG)

    def format_query_not_found(self, instrument: dict) -> bytes:
        raise NotImplementedError(_UNIDIRECTIONAL_MSG)


# ============================================================
# Unit tests
# ============================================================
if __name__ == "__main__":
    mod = CobasC111Module()

    # Identity
    assert mod.PROTOCOL_NAME == "COBAS_C111"
    assert mod.VERSION == "1.0.0"
    print(f"OK: {mod.PROTOCOL_NAME} v{mod.VERSION}")

    # is_enq / handle_ack
    assert mod.is_enq(b"\x05") is True
    assert mod.is_enq(b"\x06") is False
    assert mod.is_enq(b"") is False
    assert mod.handle_ack(b"\x06") == "ACK"
    assert mod.handle_ack(b"\x15") == "NAK"
    assert mod.handle_ack(b"\x04") == "EOT"
    assert mod.handle_ack(b"") == "UNKNOWN"
    print("OK: is_enq / handle_ack")

    # Bidirectional methods raise NotImplementedError
    for method_name, args in [
        ("format_order", ({}, {})),
        ("handle_enq", (b"\x05", {})),
        ("format_query_response", ({}, {})),
        ("format_query_not_found", ({},)),
    ]:
        raised = False
        try:
            getattr(mod, method_name)(*args)
        except NotImplementedError as e:
            raised = True
            assert "unidirectional" in str(e).lower()
        assert raised, f"{method_name} should raise NotImplementedError"
    print("OK: all bidirectional methods raise NotImplementedError")

    # End-to-end: build a multi-record message and parse it
    from protocols.cobas_c111.parser import FrameDecoder as _FD
    fd_helper = _FD()

    body_text = (
        "1H|\\^&|||c111^Roche^c111^0.5.4.0509^1^1005||||host|RSUPL^BATCH|P|1|20051021152259\r"
        "P|1||\r"
        "O|1||Sample1^^Pos1|^^^111|R||||||N|||||||||||20051021152259|||F\r"
        "R|1|^^^111|1480.00|mmol/l||||F||UnitTest\r"
        "L|1|N\r"
    )
    # Wrap as single end-frame
    body = b"\x02" + body_text.encode("ascii") + b"\x03"
    cs = fd_helper.compute_checksum(body)
    full = body + cs.encode("ascii") + b"\r\n"

    parsed = mod.parse(full, {"id": 42, "name": "Cobas c-111 #1"})
    assert parsed["instrument_id"] == 42
    assert parsed["protocol"] == "COBAS_C111"
    assert parsed["patient"]["patient_id"] == ""
    assert parsed["specimen"]["sample_id"] == "Sample1"
    assert len(parsed["results"]) == 1
    assert parsed["results"][0]["test_code"] == "111"
    assert parsed["results"][0]["value"] == "1480.00"
    assert parsed["results"][0]["unit"] == "mmol/l"
    assert parsed["results"][0]["status"] == "F"
    assert parsed["parse_errors"] == []
    print("OK: parse() end-to-end H+P+O+R+L")

    # End-to-end: message with M.CR
    body_text2 = (
        "1H|\\^&|||c111^Roche^c111^0.5.4.0509^1^1005||||host|RSUPL^BATCH|P|1|20081021130649\r"
        "P|1||\r"
        "M|1|CR^BM^c111^1|211^Ap211|Rea1.1|mmol/l|BS^Rea1||N^R|2|20081021130649|A^admin\r"
        "L|1|N\r"
    )
    body2 = b"\x02" + body_text2.encode("ascii") + b"\x03"
    cs2 = fd_helper.compute_checksum(body2)
    full2 = body2 + cs2.encode("ascii") + b"\r\n"
    parsed2 = mod.parse(full2, {"id": 42})
    assert len(parsed2["results"]) == 1
    assert parsed2["results"][0]["status"] == "calibration"
    assert parsed2["results"][0]["test_code"] == "211"
    assert parsed2["results"][0]["test_name"] == "Ap211"
    print("OK: parse() captures M.CR as calibration result")

    # End-to-end: message with R + C (comment after result)
    body_text3 = (
        "1H|\\^&|||c111^Roche^c111^0.5.4.0509^1^1005||||host|RSUPL^BATCH|P|1|20081021130649\r"
        "P|1||\r"
        "O|1||Sample2^^|^^^687|R||||||N|||||||||||20081021130649|||F\r"
        "R|1|^^^687|49.2|U/L|20.0\\30.0|H||F||admin\r"
        "C|1|I|Sol1^F Dev|I\r"
        "L|1|N\r"
    )
    body3 = b"\x02" + body_text3.encode("ascii") + b"\x03"
    cs3 = fd_helper.compute_checksum(body3)
    full3 = body3 + cs3.encode("ascii") + b"\r\n"
    parsed3 = mod.parse(full3, {"id": 42})
    assert len(parsed3["results"]) == 1
    assert parsed3["results"][0]["flag"] == "H"
    assert len(parsed3["comments"]) == 1
    assert "result[687]" in parsed3["comments"][0]
    assert "Sol1" in parsed3["comments"][0]
    print("OK: parse() captures C-after-R into comments with test attribution")

    print("=== CobasC111Module tests PASSED ===")
