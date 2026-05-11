"""
services/tcp_socket/config.py — Instrument Configuration

Load konfigurasi instrument dari tbl_instrument via lib/db.py.
Menyediakan InstrumentConfig dataclass untuk digunakan oleh TCPSocketService
dan komponen-komponennya.
"""

from __future__ import annotations

from dataclasses import dataclass

from lib.db import DBManager, TblInstrument
from lib.utils import get_logger


logger = get_logger("tcp_config")


@dataclass
class InstrumentConfig:
    """
    Konfigurasi satu instrument dari tbl_instrument.

    Digunakan oleh TCPSocketService untuk menentukan:
    - Koneksi TCP (ip, port, server/client)
    - Protocol yang dipakai (ASTM/HL7/BCI)
    - Mode operasi (unidirectional/bidirectional)
    - Sub-mode bidirectional (broadcast/query/broadcast+query)
    """
    id: int
    name: str
    ip_address: str
    port: int
    protocol: str            # "ASTM", "HL7", "BCI"
    mode: str                # "unidirectional", "bidirectional"
    bidir_mode: str | None   # None, "broadcast", "query", "broadcast+query"
    broadcast_interval: int  # detik, default 30
    connection: str          # "server", "client"
    is_active: bool

    @property
    def is_bidirectional(self) -> bool:
        return self.mode == "bidirectional"

    @property
    def has_broadcast(self) -> bool:
        return self.bidir_mode in ("broadcast", "broadcast+query")

    @property
    def has_query(self) -> bool:
        return self.bidir_mode in ("query", "broadcast+query")

    @property
    def is_server(self) -> bool:
        return self.connection == "server"

    @property
    def is_client(self) -> bool:
        return self.connection == "client"

    def to_dict(self) -> dict:
        """Konversi ke dict (untuk dipakai protocol module)."""
        return {
            "id": self.id,
            "name": self.name,
            "ip_address": self.ip_address,
            "port": self.port,
            "protocol": self.protocol,
            "mode": self.mode,
            "bidir_mode": self.bidir_mode,
            "broadcast_interval": self.broadcast_interval,
            "connection": self.connection,
            "is_active": self.is_active,
        }


def load_instrument_config(instrument_id: int) -> InstrumentConfig | None:
    """
    Load konfigurasi instrument dari database berdasarkan ID.

    Args:
        instrument_id: ID instrument di tbl_instrument

    Returns:
        InstrumentConfig atau None jika tidak ditemukan
    """
    db = DBManager()
    session = db.get_session()
    try:
        row = (
            session.query(TblInstrument)
            .filter(TblInstrument.id == instrument_id)
            .first()
        )
        if row is None:
            logger.warning(f"Instrument ID {instrument_id} tidak ditemukan di database")
            return None

        config = InstrumentConfig(
            id=row.id,
            name=row.name,
            ip_address=row.ip_address,
            port=row.port,
            protocol=row.protocol,
            mode=row.mode,
            bidir_mode=row.bidir_mode,
            broadcast_interval=row.broadcast_interval or 30,
            connection=row.connection,
            is_active=row.is_active,
        )

        logger.info(
            f"Loaded config: {config.name} ({config.protocol}) "
            f"@ {config.ip_address}:{config.port} "
            f"mode={config.mode} bidir={config.bidir_mode} "
            f"conn={config.connection}"
        )
        return config

    except Exception as e:
        logger.error(f"Gagal load instrument config ID {instrument_id}: {e}")
        return None
    finally:
        session.close()


def load_active_instruments() -> list[InstrumentConfig]:
    """
    Load semua instrument yang aktif (is_active=True).

    Returns:
        List InstrumentConfig
    """
    db = DBManager()
    session = db.get_session()
    try:
        rows = (
            session.query(TblInstrument)
            .filter(TblInstrument.is_active == True)
            .all()
        )
        configs = []
        for row in rows:
            configs.append(InstrumentConfig(
                id=row.id,
                name=row.name,
                ip_address=row.ip_address,
                port=row.port,
                protocol=row.protocol,
                mode=row.mode,
                bidir_mode=row.bidir_mode,
                broadcast_interval=row.broadcast_interval or 30,
                connection=row.connection,
                is_active=row.is_active,
            ))
        logger.info(f"Loaded {len(configs)} active instruments")
        return configs

    except Exception as e:
        logger.error(f"Gagal load active instruments: {e}")
        return []
    finally:
        session.close()


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test InstrumentConfig ===\n")

    # Test dataclass
    cfg = InstrumentConfig(
        id=1,
        name="Sysmex XN-1000",
        ip_address="192.168.1.100",
        port=9100,
        protocol="ASTM",
        mode="bidirectional",
        bidir_mode="broadcast+query",
        broadcast_interval=30,
        connection="server",
        is_active=True,
    )
    print(f"Config: {cfg.name}")
    print(f"  Protocol: {cfg.protocol}")
    print(f"  Address: {cfg.ip_address}:{cfg.port}")
    print(f"  Mode: {cfg.mode}, bidir: {cfg.bidir_mode}")
    print(f"  Connection: {cfg.connection}")

    # Test properties
    assert cfg.is_bidirectional is True
    assert cfg.has_broadcast is True
    assert cfg.has_query is True
    assert cfg.is_server is True
    assert cfg.is_client is False
    print("OK: Properties bidirectional+broadcast+query+server benar")

    # Test unidirectional
    cfg_uni = InstrumentConfig(
        id=2, name="Test", ip_address="10.0.0.1", port=5000,
        protocol="HL7", mode="unidirectional", bidir_mode=None,
        broadcast_interval=30, connection="client", is_active=True,
    )
    assert cfg_uni.is_bidirectional is False
    assert cfg_uni.has_broadcast is False
    assert cfg_uni.has_query is False
    assert cfg_uni.is_server is False
    assert cfg_uni.is_client is True
    print("OK: Properties unidirectional+client benar")

    # Test broadcast only
    cfg_bc = InstrumentConfig(
        id=3, name="Test2", ip_address="10.0.0.2", port=5001,
        protocol="ASTM", mode="bidirectional", bidir_mode="broadcast",
        broadcast_interval=60, connection="server", is_active=True,
    )
    assert cfg_bc.has_broadcast is True
    assert cfg_bc.has_query is False
    print("OK: Properties broadcast-only benar")

    # Test query only
    cfg_qr = InstrumentConfig(
        id=4, name="Test3", ip_address="10.0.0.3", port=5002,
        protocol="HL7", mode="bidirectional", bidir_mode="query",
        broadcast_interval=30, connection="server", is_active=True,
    )
    assert cfg_qr.has_broadcast is False
    assert cfg_qr.has_query is True
    print("OK: Properties query-only benar")

    # Test to_dict
    d = cfg.to_dict()
    assert d["id"] == 1
    assert d["name"] == "Sysmex XN-1000"
    assert d["protocol"] == "ASTM"
    assert d["broadcast_interval"] == 30
    print("OK: to_dict() benar")

    print("\n=== Semua test InstrumentConfig PASSED ===")
