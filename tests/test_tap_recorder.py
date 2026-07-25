"""Test TapRecorder — JSONL, hex, flush-per-event."""

import json
import os

import pytest

from services.tap import recorder as rec_mod
from services.tap.recorder import TapRecorder, read_events


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "sesi.jsonl")


class TestTapRecorder:
    def test_menulis_event_rx(self, path):
        with TapRecorder(path) as r:
            r.write_event("rx", b"\x0bMSH|")
        ev = read_events(path)
        assert len(ev) == 1
        assert ev[0]["dir"] == "rx"
        assert ev[0]["hex"] == "0b4d53487c"

    def test_hex_bukan_base64(self, path):
        # Hex supaya bisa dibaca mata dan di-grep langsung.
        with TapRecorder(path) as r:
            r.write_event("tx", b"\x06")
        assert read_events(path)[0]["hex"] == "06"

    def test_event_punya_timestamp_iso(self, path):
        with TapRecorder(path) as r:
            r.write_event("rx", b"x")
        t = read_events(path)[0]["t"]
        assert "T" in t and len(t) >= 19

    def test_note_ikut_tersimpan(self, path):
        with TapRecorder(path) as r:
            r.write_event("tx", b"\x06", note="ACK")
        assert read_events(path)[0]["note"] == "ACK"

    def test_note_kosong_tidak_ditulis(self, path):
        with TapRecorder(path) as r:
            r.write_event("rx", b"x")
        assert "note" not in read_events(path)[0]

    def test_mark_message(self, path):
        with TapRecorder(path) as r:
            r.write_event("rx", b"x")
            r.mark_message(0)
        ev = read_events(path)
        assert ev[1] == {**ev[1], "dir": "meta", "event": "message_complete", "index": 0}

    def test_mark_query(self, path):
        with TapRecorder(path) as r:
            r.mark_query(3)
        ev = read_events(path)
        assert ev[0]["event"] == "query_detected"
        assert ev[0]["index"] == 3
        assert ev[0]["dir"] == "meta"

    def test_event_kosong_tidak_ditulis(self, path):
        with TapRecorder(path) as r:
            r.write_event("rx", b"")
        assert read_events(path) == []

    def test_ter_flush_sebelum_close(self, path):
        # Inti pengaman: byte harus di disk SEBELUM ACK dikirim. Kalau proses
        # mati setelah ACK tapi sebelum tulisan, hasil pasien hilang diam-diam.
        r = TapRecorder(path)
        r.write_event("rx", b"penting")
        # Belum close — tapi harus sudah terbaca dari file.
        assert len(read_events(path)) == 1
        r.close()

    def test_urutan_event_terjaga(self, path):
        with TapRecorder(path) as r:
            for i in range(20):
                r.write_event("rx", bytes([i]))
        ev = read_events(path)
        assert [e["hex"] for e in ev] == [f"{i:02x}" for i in range(20)]

    def test_direction_tidak_valid_ditolak(self, path):
        with TapRecorder(path) as r:
            with pytest.raises(ValueError, match="direction"):
                r.write_event("sideways", b"x")

    def test_direktori_dibuat_otomatis(self, tmp_path):
        p = str(tmp_path / "belum" / "ada" / "sesi.jsonl")
        with TapRecorder(p) as r:
            r.write_event("rx", b"x")
        assert len(read_events(p)) == 1


class TestResolveTapDir:
    def test_pakai_primary_bila_writable(self, tmp_path, monkeypatch):
        primary = str(tmp_path / "varlog")
        os.makedirs(primary, exist_ok=True)
        monkeypatch.setattr(rec_mod, "LOG_DIR", primary)
        monkeypatch.setattr(rec_mod, "LOG_DIR_FALLBACK", "/tmp/midlab")
        assert rec_mod._resolve_tap_dir() == os.path.join(primary, "tap")

    def test_fallback_saat_primary_tak_writable(self, tmp_path, monkeypatch):
        # Primary tidak ada & induknya tak bisa ditulis → jatuh ke fallback.
        primary = "/proc/tidak-bisa-dibuat/midlab"
        fallback = str(tmp_path / "midlab")
        os.makedirs(fallback, exist_ok=True)
        monkeypatch.setattr(rec_mod, "LOG_DIR", primary)
        monkeypatch.setattr(rec_mod, "LOG_DIR_FALLBACK", fallback)
        assert rec_mod._resolve_tap_dir() == os.path.join(fallback, "tap")


class TestReadEvents:
    def test_file_tidak_ada_list_kosong(self, tmp_path):
        assert read_events(str(tmp_path / "hilang.jsonl")) == []

    def test_baris_rusak_dilewati(self, path):
        with open(path, "w") as f:
            f.write(json.dumps({"t": "x", "dir": "rx", "hex": "06"}) + "\n")
            f.write("{ bukan json\n")
            f.write(json.dumps({"t": "y", "dir": "tx", "hex": "05"}) + "\n")
        ev = read_events(path)
        assert len(ev) == 2
