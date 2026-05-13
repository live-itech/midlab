"""
lib/db.py — Database Manager untuk MidLab

Mendefinisikan semua tabel (tbl_instrument, tbl_result, tbl_order, tbl_service_log)
menggunakan SQLAlchemy Core + ORM. Menyediakan connection pooling dan helper functions
untuk operasi database yang sering digunakan oleh service-service MidLab.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Integer,
    String,
    Text,
    JSON,
    Boolean,
    DateTime,
    Enum,
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.pool import QueuePool

from lib.config import Config


Base = declarative_base()
metadata = MetaData()


# ============================================================
# ORM Models — definisi tabel sesuai schema di CLAUDE.md
# ============================================================

class TblInstrument(Base):
    """Tabel instrumen/alat lab yang terhubung ke MidLab."""
    __tablename__ = "tbl_instrument"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    ip_address = Column(String(45), nullable=False)
    port = Column(Integer, nullable=False)
    protocol = Column(Enum("ASTM", "HL7", "BCI", name="protocol_enum"), nullable=False)
    mode = Column(
        Enum("unidirectional", "bidirectional", name="mode_enum"),
        nullable=False,
        default="unidirectional",
    )
    bidir_mode = Column(
        Enum("broadcast", "query", "broadcast+query", name="bidir_mode_enum"),
        nullable=True,
    )
    broadcast_interval = Column(Integer, default=30)
    connection = Column(
        Enum("server", "client", name="connection_enum"),
        nullable=False,
        default="server",
    )
    is_active = Column(Boolean, default=True)


class TblResult(Base):
    """Tabel hasil pemeriksaan dari alat, dikirim ke LIS oleh ResultSenderService."""
    __tablename__ = "tbl_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_id = Column(Integer, nullable=False)
    protocol = Column(String(10), nullable=False)
    raw_data = Column(Text, nullable=True)
    result_json = Column(JSON, nullable=True)
    send_status = Column(
        Enum("pending", "sent", "failed", name="send_status_enum"),
        nullable=False,
        default="pending",
    )
    retry_count = Column(Integer, default=0)
    sent_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    received_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TblOrder(Base):
    """Tabel order dari LIS, dikirim ke alat oleh TCPSocketService."""
    __tablename__ = "tbl_order"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_id = Column(Integer, nullable=False)
    order_json = Column(JSON, nullable=True)
    instrument_status = Column(
        Enum("pending", "sent", "failed", name="instrument_status_enum"),
        nullable=False,
        default="pending",
    )
    failed_at_service = Column(String(100), nullable=True)
    retry_count = Column(Integer, default=0)
    sent_to_instrument_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TblServiceLog(Base):
    """Tabel log service untuk monitoring via Web Console."""
    __tablename__ = "tbl_service_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service = Column(String(100), nullable=False)
    level = Column(
        Enum("INFO", "WARNING", "ERROR", name="log_level_enum"),
        nullable=False,
        default="INFO",
    )
    message = Column(Text, nullable=True)
    logged_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TblSetting(Base):
    """
    Tabel key/value untuk runtime settings yang bisa diubah dari Web Console
    tanpa restart (mis. LIS API URL/key untuk ResultSender).

    Service yang membaca settings ini WAJIB punya fallback ke /etc/midlab/config.yaml
    agar tetap jalan saat tabel kosong / DB down.
    """
    __tablename__ = "tbl_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ============================================================
# DBManager — engine, session, dan table management
# ============================================================

class DBManager:
    """
    Database manager dengan connection pooling.

    Contoh penggunaan:
        db = DBManager()
        with db.get_session() as session:
            results = session.query(TblResult).filter_by(send_status="pending").all()
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        config = Config()
        db_host = config.get("database.host", "127.0.0.1")
        db_port = config.get("database.port", 3306)
        db_user = config.get("database.user", "midlab")
        db_pass = config.get("database.password", "")
        db_name = config.get("database.database", "midlab_db")
        pool_size = config.get("database.pool_size", 10)
        pool_recycle = config.get("database.pool_recycle", 3600)

        self._engine = create_engine(
            f"mysql+pymysql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}",
            poolclass=QueuePool,
            pool_size=pool_size,
            pool_recycle=pool_recycle,
            pool_pre_ping=True,
            echo=False,
        )
        self._SessionFactory = sessionmaker(bind=self._engine)
        self._initialized = True

    def get_session(self):
        """
        Buat session baru. Gunakan sebagai context manager:
            with db.get_session() as session:
                ...
        """
        return self._SessionFactory()

    def create_all_tables(self):
        """Buat semua tabel di database jika belum ada."""
        Base.metadata.create_all(self._engine)

    @property
    def engine(self):
        return self._engine

    @classmethod
    def reset(cls):
        """Reset singleton (untuk testing)."""
        if cls._instance and cls._instance._initialized:
            cls._instance._engine.dispose()
        cls._instance = None


# ============================================================
# Helper Functions — operasi DB yang sering dipakai service
# ============================================================

def get_pending_results(limit: int = 100) -> list:
    """Ambil hasil dengan status pending untuk dikirim ke LIS."""
    db = DBManager()
    session = db.get_session()
    try:
        results = (
            session.query(TblResult)
            .filter(TblResult.send_status == "pending")
            .order_by(TblResult.received_at.asc())
            .limit(limit)
            .all()
        )
        # Detach dari session agar bisa dipakai di luar
        session.expunge_all()
        return results
    except Exception:
        return []
    finally:
        session.close()


def get_pending_orders(instrument_id: int = None, limit: int = 100) -> list:
    """Ambil order dengan status pending untuk dikirim ke alat."""
    db = DBManager()
    session = db.get_session()
    try:
        query = session.query(TblOrder).filter(
            TblOrder.instrument_status == "pending"
        )
        if instrument_id is not None:
            query = query.filter(TblOrder.instrument_id == instrument_id)
        orders = query.order_by(TblOrder.created_at.asc()).limit(limit).all()
        session.expunge_all()
        return orders
    except Exception:
        return []
    finally:
        session.close()


def update_result_status(
    result_id: int,
    status: str,
    error_message: str = None,
):
    """
    Update send_status di tbl_result.
    Owned by ResultSenderService.
    """
    db = DBManager()
    session = db.get_session()
    try:
        result = session.query(TblResult).filter(TblResult.id == result_id).first()
        if result is None:
            return False
        result.send_status = status
        if status == "sent":
            result.sent_at = datetime.now(timezone.utc)
        if status == "failed":
            result.retry_count = (result.retry_count or 0) + 1
            result.error_message = error_message
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


def update_order_status(
    order_id: int,
    status: str,
    failed_at_service: str = None,
    error_message: str = None,
):
    """
    Update instrument_status di tbl_order.
    Owned by TCPSocketService/ProtocolModule.
    """
    db = DBManager()
    session = db.get_session()
    try:
        order = session.query(TblOrder).filter(TblOrder.id == order_id).first()
        if order is None:
            return False
        order.instrument_status = status
        if status == "sent":
            order.sent_to_instrument_at = datetime.now(timezone.utc)
        if status == "failed":
            order.retry_count = (order.retry_count or 0) + 1
            order.failed_at_service = failed_at_service
            order.error_message = error_message
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


def save_result(
    instrument_id: int,
    protocol: str,
    raw_data: str,
    result_json: dict,
) -> int | None:
    """
    Simpan hasil baru ke tbl_result dengan status pending.
    Dipanggil oleh ProtocolModule setelah parsing.
    Returns: ID record baru atau None jika gagal.
    """
    db = DBManager()
    session = db.get_session()
    try:
        new_result = TblResult(
            instrument_id=instrument_id,
            protocol=protocol,
            raw_data=raw_data,
            result_json=result_json,
            send_status="pending",
        )
        session.add(new_result)
        session.commit()
        return new_result.id
    except Exception:
        session.rollback()
        return None
    finally:
        session.close()


def get_setting(key: str, default: str | None = None) -> str | None:
    """
    Ambil value dari tbl_settings. Return default jika key tidak ada atau DB error.
    Dipanggil oleh ResultSender tiap poll cycle untuk auto-reload LIS URL/key.
    """
    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblSetting).filter(TblSetting.key == key).first()
        if row is None or row.value is None:
            return default
        return row.value
    except Exception:
        return default
    finally:
        session.close()


def set_setting(key: str, value: str | None) -> bool:
    """
    Upsert key/value ke tbl_settings.
    Returns: True jika berhasil.
    """
    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblSetting).filter(TblSetting.key == key).first()
        if row is None:
            row = TblSetting(key=key, value=value)
            session.add(row)
        else:
            row.value = value
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


def get_all_settings() -> dict:
    """Return semua key/value sebagai dict. Empty dict jika error."""
    db = DBManager()
    session = db.get_session()
    try:
        rows = session.query(TblSetting).all()
        return {r.key: r.value for r in rows}
    except Exception:
        return {}
    finally:
        session.close()


def save_order(instrument_id: int, order_json: dict) -> int | None:
    """
    Simpan order baru ke tbl_order dengan status pending.
    Dipanggil oleh OrderReceiverService saat menerima order dari LIS.
    Returns: ID record baru atau None jika gagal.
    """
    db = DBManager()
    session = db.get_session()
    try:
        new_order = TblOrder(
            instrument_id=instrument_id,
            order_json=order_json,
            instrument_status="pending",
        )
        session.add(new_order)
        session.commit()
        return new_order.id
    except Exception:
        session.rollback()
        return None
    finally:
        session.close()
