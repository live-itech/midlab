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


from lib.comm_logger import CommLogger


def test_logger_writes_rx_tx(tmp_path, monkeypatch):
    monkeypatch.setattr("lib.comm_logger.LOG_DIR", str(tmp_path))
    CommLogger._cache.clear()
    cl = CommLogger.for_instrument(7)
    cl.rx(b"\x05")
    cl.tx(b"\x06")
    cl.rx(b"\x021H|\r\x03\r\n")
    for h in cl._logger.handlers:
        h.flush()
    log_file = tmp_path / "tcp_7.comm.log"
    assert log_file.exists()
    content = log_file.read_text()
    assert "[tcp_7] ← RX <ENQ>" in content
    assert "[tcp_7] → TX <ACK>" in content
    assert "<STX>1H|<CR><ETX><CR><LF>" in content


def test_logger_singleton_per_instrument(tmp_path, monkeypatch):
    monkeypatch.setattr("lib.comm_logger.LOG_DIR", str(tmp_path))
    CommLogger._cache.clear()
    a = CommLogger.for_instrument(1)
    b = CommLogger.for_instrument(1)
    assert a is b
    c = CommLogger.for_instrument(2)
    assert c is not a
