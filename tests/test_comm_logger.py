from lib.comm_logger import _decode_for_log


def test_control_chars_mapped():
    assert _decode_for_log(b"\x05") == "<ENQ>"
    assert _decode_for_log(b"\x06") == "<ACK>"
    assert _decode_for_log(b"\x15") == "<NAK>"
    assert _decode_for_log(b"\x04") == "<EOT>"
    assert _decode_for_log(b"\x02") == "<STX>"
    assert _decode_for_log(b"\x03") == "<ETX>"
    assert _decode_for_log(b"\x17") == "<ETB>"
    assert _decode_for_log(b"\r") == "<CR>"
    assert _decode_for_log(b"\n") == "<LF>"


def test_printable_passthrough():
    assert _decode_for_log(b"ABC123") == "ABC123"


def test_mixed_frame():
    raw = b"\x021H|\\^&|||COBAS\r\x03D2\r\n"
    assert _decode_for_log(raw) == "<STX>1H|\\^&|||COBAS<CR><ETX>D2<CR><LF>"


def test_unknown_byte_hex_fallback():
    assert _decode_for_log(b"\xff") == "\\xff"
    assert _decode_for_log(b"A\xffB") == "A\\xffB"


def test_empty():
    assert _decode_for_log(b"") == ""
