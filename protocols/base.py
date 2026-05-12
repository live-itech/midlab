"""
protocols/base.py — Abstract Base Class untuk Protocol Module MidLab

Mendefinisikan kontrak yang harus dipenuhi setiap protocol module (ASTM, HL7, BCI, dll).
Juga menyediakan fungsi load_module() untuk dynamic loading protocol via importlib.

Setiap protocol module harus inherit class ini dan implementasi semua abstract method.
"""

import importlib
import logging
from abc import ABC, abstractmethod

from lib.utils import get_logger


logger = get_logger("protocol_loader")


class BaseProtocolModule(ABC):
    """
    Abstract base class untuk semua protocol module di MidLab.

    Setiap implementasi (ASTMModule, HL7Module, dll) harus mengimplementasi
    semua method abstract di bawah ini. TCPSocketService akan memanggil
    method-method ini sesuai mode operasi (unidirectional/bidirectional).
    """

    # ============================================================
    # Abstract Properties — identitas protocol
    # ============================================================

    @property
    @abstractmethod
    def PROTOCOL_NAME(self) -> str:
        """Nama protocol, misal 'ASTM', 'HL7', 'BCI'."""
        ...

    @property
    @abstractmethod
    def VERSION(self) -> str:
        """Versi module protocol, misal '1.0.0'."""
        ...

    # ============================================================
    # Abstract Methods — kontrak yang wajib diimplementasi
    # ============================================================

    @abstractmethod
    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Parse raw bytes dari alat menjadi ResultObject dict.
        Dipanggil di semua mode (unidirectional & bidirectional).

        Args:
            raw_bytes: Data mentah dari TCP socket
            instrument: Dict info instrumen dari tbl_instrument

        Returns:
            Dict ResultObject sesuai format di CLAUDE.md
        """
        ...

    @abstractmethod
    def format_order(self, order: dict, instrument: dict) -> bytes:
        """
        Format order menjadi bytes untuk dikirim ke alat (broadcast mode).

        Args:
            order: Dict OrderObject dari tbl_order.order_json
            instrument: Dict info instrumen

        Returns:
            Bytes pesan lengkap siap kirim ke alat
        """
        ...

    @abstractmethod
    def is_enq(self, raw_bytes: bytes) -> bool:
        """
        Deteksi apakah raw_bytes merupakan ENQ / query trigger dari alat.

        ASTM: byte pertama == 0x05
        HL7: MSH message type QBP^Q22 atau QRY

        Args:
            raw_bytes: Data yang diterima dari socket

        Returns:
            True jika merupakan ENQ/query trigger
        """
        ...

    @abstractmethod
    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Handle ENQ dari alat, ekstrak informasi query.
        Dipanggil setelah is_enq() return True.

        Args:
            raw_bytes: Data ENQ dari alat
            instrument: Dict info instrumen

        Returns:
            Dict dengan keys: type, sample_id, patient_id, raw_query
        """
        ...

    @abstractmethod
    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        """
        Format response berisi order data untuk dikirim ke alat (query mode).
        Dipanggil saat order ditemukan di database.

        Args:
            order: Dict OrderObject yang ditemukan
            instrument: Dict info instrumen

        Returns:
            Bytes response lengkap
        """
        ...

    @abstractmethod
    def format_query_not_found(self, instrument: dict) -> bytes:
        """
        Format response NAK / empty jika order tidak ditemukan (query mode).

        Args:
            instrument: Dict info instrumen

        Returns:
            Bytes NAK atau empty response
        """
        ...

    @abstractmethod
    def handle_ack(self, raw_bytes: bytes) -> str:
        """
        Identifikasi tipe acknowledgement dari alat.

        Args:
            raw_bytes: Data yang diterima

        Returns:
            String: 'ACK', 'NAK', 'EOT', atau 'UNKNOWN'
        """
        ...


# ============================================================
# Dynamic Module Loader
# ============================================================

# Registry nama protocol → modul Python path
_PROTOCOL_REGISTRY = {
    "ASTM": "protocols.astm.module",
    "HL7":  "protocols.hl7.module",
    "BCI":  "protocols.bci.module",
    "COBAS_C111": "protocols.cobas_c111.module",
}

# Cache instance yang sudah di-load
_loaded_modules: dict[str, BaseProtocolModule] = {}


def load_module(protocol_name: str) -> BaseProtocolModule:
    """
    Load protocol module secara dynamic menggunakan importlib.

    Args:
        protocol_name: Nama protocol ('ASTM', 'HL7', 'BCI')

    Returns:
        Instance dari protocol module yang sudah terinisialisasi

    Raises:
        ValueError: Jika protocol_name tidak dikenali
        ImportError: Jika module gagal di-load
    """
    protocol_name = protocol_name.upper()

    # Return dari cache jika sudah pernah di-load
    if protocol_name in _loaded_modules:
        logger.info(f"Menggunakan cached module untuk protocol {protocol_name}")
        return _loaded_modules[protocol_name]

    if protocol_name not in _PROTOCOL_REGISTRY:
        raise ValueError(
            f"Protocol '{protocol_name}' tidak dikenali. "
            f"Tersedia: {list(_PROTOCOL_REGISTRY.keys())}"
        )

    module_path = _PROTOCOL_REGISTRY[protocol_name]
    logger.info(f"Loading protocol module: {protocol_name} dari {module_path}")

    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        logger.error(f"Gagal import module {module_path}: {e}")
        raise ImportError(f"Gagal load protocol module '{protocol_name}': {e}")

    # Cari class yang inherit BaseProtocolModule di dalam module
    module_class = None
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, BaseProtocolModule)
            and attr is not BaseProtocolModule
        ):
            module_class = attr
            break

    if module_class is None:
        raise ImportError(
            f"Module {module_path} tidak memiliki class "
            f"yang inherit BaseProtocolModule"
        )

    instance = module_class()
    _loaded_modules[protocol_name] = instance
    logger.info(
        f"Protocol module loaded: {instance.PROTOCOL_NAME} v{instance.VERSION}"
    )
    return instance


def clear_module_cache():
    """Hapus cache module (untuk hot-swap protocol saat runtime)."""
    _loaded_modules.clear()
    logger.info("Module cache cleared")


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test BaseProtocolModule ===\n")

    # Test 1: Tidak bisa instantiate abstract class
    try:
        obj = BaseProtocolModule()
        print("FAIL: Seharusnya tidak bisa instantiate abstract class")
    except TypeError as e:
        print(f"OK: Abstract class tidak bisa di-instantiate: {e}\n")

    # Test 2: Subclass harus implementasi semua method
    class IncompleteModule(BaseProtocolModule):
        pass

    try:
        obj = IncompleteModule()
        print("FAIL: Seharusnya tidak bisa instantiate tanpa semua method")
    except TypeError as e:
        print(f"OK: Incomplete subclass gagal: {e}\n")

    # Test 3: Subclass lengkap bisa di-instantiate
    class DummyModule(BaseProtocolModule):
        @property
        def PROTOCOL_NAME(self):
            return "DUMMY"

        @property
        def VERSION(self):
            return "0.1.0"

        def parse(self, raw_bytes, instrument):
            return {"status": "parsed"}

        def format_order(self, order, instrument):
            return b"\x05"

        def is_enq(self, raw_bytes):
            return raw_bytes == b"\x05"

        def handle_enq(self, raw_bytes, instrument):
            return {"type": "enq", "sample_id": "", "patient_id": "", "raw_query": ""}

        def format_query_response(self, order, instrument):
            return b"\x06"

        def format_query_not_found(self, instrument):
            return b"\x15"

        def handle_ack(self, raw_bytes):
            return "ACK"

    dummy = DummyModule()
    print(f"OK: DummyModule instantiated: {dummy.PROTOCOL_NAME} v{dummy.VERSION}")
    print(f"    parse result: {dummy.parse(b'data', {})}")
    print(f"    is_enq(0x05): {dummy.is_enq(b'\\x05')}")
    print(f"    is_enq(0x06): {dummy.is_enq(b'\\x06')}")
    print(f"    handle_ack: {dummy.handle_ack(b'\\x06')}")
    print()

    # Test 4: load_module dengan protocol yang tidak ada
    try:
        load_module("UNKNOWN")
        print("FAIL: Seharusnya ValueError")
    except ValueError as e:
        print(f"OK: Unknown protocol ditolak: {e}\n")

    # Test 5: Registry tersedia
    print(f"OK: Protocol registry: {list(_PROTOCOL_REGISTRY.keys())}")
    print()

    # Test 6: clear_module_cache
    _loaded_modules["TEST"] = dummy
    assert len(_loaded_modules) == 1
    clear_module_cache()
    assert len(_loaded_modules) == 0
    print("OK: Module cache clear berfungsi\n")

    print("=== Semua test BaseProtocolModule PASSED ===")
