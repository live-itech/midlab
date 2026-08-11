"""Pytest fixtures: in-memory SQLite mirror tbl_* untuk unit test cepat."""
import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from lib.db import Base

# --- Kompatibilitas aioresponses ↔ aiohttp 3.14 -----------------------------
# aiohttp 3.14 menambah parameter keyword-only WAJIB `stream_writer` di
# ClientResponse.__init__. aioresponses 0.7.9 (rilis terbaru) membangun respons
# palsunya lewat `response_class(method, url, **kwargs)` tanpa parameter itu,
# jadi setiap request yang di-mock mati dengan TypeError. Metadata aioresponses
# menyatakan `aiohttp<4.0`, jadi pip tidak menahan kombinasi yang rusak ini.
#
# Kita tidak mengunci aiohttp ke versi lama: aiohttp itu dependensi RUNTIME
# (LisBridgeService), sayang menahan versinya demi library mock yang cuma
# dipakai saat tes.
#
# Tambalan diterapkan di titik paling sempit — atribut ClientResponse di dalam
# modul aioresponses — sehingga hanya menyentuh respons palsu, bukan aiohttp
# global. Dua pengaman membuatnya hilang sendiri saat tidak lagi dibutuhkan:
# cek signature (aiohttp lama tidak ikut ditambal) dan `setdefault` (kalau
# aioresponses nanti mengirim stream_writer sendiri, nilainya yang dipakai).
def _patch_aioresponses_stream_writer() -> None:
    try:
        import aioresponses.core as _aiocore
        from aiohttp.client_reqrep import ClientResponse
    except ImportError:  # aioresponses tidak terpasang — tes lain tetap jalan
        return

    if "stream_writer" not in inspect.signature(ClientResponse.__init__).parameters:
        return

    class _StubStreamWriter:
        """aiohttp cuma membaca .output_size saat writer=None (aioresponses
        selalu mengirim writer=None: 'Request already sent')."""

        output_size = 0

    class _CompatClientResponse(ClientResponse):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("stream_writer", _StubStreamWriter())
            super().__init__(*args, **kwargs)

    _aiocore.ClientResponse = _CompatClientResponse


_patch_aioresponses_stream_writer()


@pytest.fixture
def db_session():
    """In-memory SQLite session, schema dibuat ulang per-test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
