"""
services/order_receiver/api.py — FastAPI App OrderReceiverService

Endpoints:
  POST /api/orders      — terima order dari LIS, validasi, simpan ke DB
  GET  /api/orders/{id} — cek status order
  GET  /api/health      — health check

Autentikasi via header X-API-Key (key dari config.yaml: order_receiver.api_key).

Konfigurasi dari config.yaml:
  order_receiver:
    api_key: "..."       # API key untuk autentikasi
    port: 8080           # port uvicorn (dipakai di main.py)
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from lib.config import Config
from lib.db import DBManager, TblOrder, save_order
from lib.utils import get_logger


# ============================================================
# Pydantic Models — validasi request body sesuai OrderObject
# ============================================================

class OrderPatientSchema(BaseModel):
    patient_id: str = ""
    name: str = ""
    dob: str = ""
    gender: str = ""


class OrderSpecimenSchema(BaseModel):
    sample_id: str = ""
    sample_type: str = ""
    priority: str = ""


class OrderTestSchema(BaseModel):
    test_code: str = ""
    test_name: str = ""


class OrderCreateRequest(BaseModel):
    """Request body POST /api/orders — sesuai OrderObject di CLAUDE.md."""
    mid_version: str = Field(default="1.0")
    order_id: str = Field(default="")
    instrument_id: int
    request_datetime: str = Field(default="")
    patient: OrderPatientSchema = Field(default_factory=OrderPatientSchema)
    specimen: OrderSpecimenSchema = Field(default_factory=OrderSpecimenSchema)
    tests: List[OrderTestSchema] = Field(default_factory=list)


class OrderCreateResponse(BaseModel):
    success: bool
    order_id: int


class OrderStatusResponse(BaseModel):
    id: int
    instrument_id: int
    instrument_status: str
    retry_count: int
    failed_at_service: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    sent_to_instrument_at: Optional[str] = None
    order_json: Optional[dict] = None


class HealthResponse(BaseModel):
    status: str
    service: str


# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(
    title="MidLab Order Receiver",
    description="Terima order dari LIS untuk dikirim ke alat lab",
    version="1.0.0",
)

logger = get_logger("order_receiver")


# ============================================================
# Dependency — API Key Auth
# ============================================================

def _get_api_key() -> str:
    """Ambil API key dari config.yaml."""
    config = Config()
    return config.get("order_receiver.api_key", "")


def _verify_api_key(x_api_key: str = Header(None)):
    """Verifikasi X-API-Key header."""
    expected = _get_api_key()
    # Jika API key tidak dikonfigurasi, skip autentikasi
    if not expected:
        return
    if x_api_key != expected:
        logger.warning(f"Unauthorized request — invalid API key")
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ============================================================
# Endpoints
# ============================================================

@app.post("/api/orders", response_model=OrderCreateResponse)
async def create_order(
    body: OrderCreateRequest,
    x_api_key: str = Header(None),
):
    """
    Terima order dari LIS.

    Validasi body sesuai OrderObject schema, simpan ke tbl_order
    dengan instrument_status='pending'.
    """
    _verify_api_key(x_api_key)

    # Validasi instrument_id
    if body.instrument_id <= 0:
        logger.warning(f"Invalid instrument_id: {body.instrument_id}")
        raise HTTPException(
            status_code=400,
            detail="instrument_id harus > 0",
        )

    # Verifikasi instrument exists di database
    db = DBManager()
    session = db.get_session()
    try:
        from lib.db import TblInstrument
        instrument = (
            session.query(TblInstrument)
            .filter(TblInstrument.id == body.instrument_id)
            .first()
        )
        if instrument is None:
            logger.warning(
                f"Instrument ID {body.instrument_id} tidak ditemukan"
            )
            raise HTTPException(
                status_code=404,
                detail=f"Instrument ID {body.instrument_id} tidak ditemukan",
            )
    finally:
        session.close()

    # Set request_datetime jika kosong
    order_dict = body.model_dump()
    if not order_dict.get("request_datetime"):
        order_dict["request_datetime"] = datetime.now(timezone.utc).isoformat()

    # Simpan ke database
    order_id = save_order(body.instrument_id, order_dict)

    if order_id is None:
        logger.error(
            f"Gagal simpan order untuk instrument_id={body.instrument_id}"
        )
        raise HTTPException(
            status_code=500,
            detail="Gagal menyimpan order ke database",
        )

    logger.info(
        f"Order created: order_id={order_id} "
        f"instrument_id={body.instrument_id} "
        f"lis_order_id={body.order_id} "
        f"tests={len(body.tests)}"
    )

    return OrderCreateResponse(success=True, order_id=order_id)


@app.get("/api/orders/{order_id}", response_model=OrderStatusResponse)
async def get_order_status(
    order_id: int,
    x_api_key: str = Header(None),
):
    """Cek status order berdasarkan ID."""
    _verify_api_key(x_api_key)

    db = DBManager()
    session = db.get_session()
    try:
        order = (
            session.query(TblOrder)
            .filter(TblOrder.id == order_id)
            .first()
        )

        if order is None:
            raise HTTPException(
                status_code=404,
                detail=f"Order ID {order_id} tidak ditemukan",
            )

        logger.info(
            f"Order status queried: order_id={order_id} "
            f"status={order.instrument_status}"
        )

        return OrderStatusResponse(
            id=order.id,
            instrument_id=order.instrument_id,
            instrument_status=order.instrument_status,
            retry_count=order.retry_count or 0,
            failed_at_service=order.failed_at_service,
            error_message=order.error_message,
            created_at=order.created_at.isoformat() if order.created_at else None,
            sent_to_instrument_at=(
                order.sent_to_instrument_at.isoformat()
                if order.sent_to_instrument_at
                else None
            ),
            order_json=order.order_json,
        )

    finally:
        session.close()


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    logger.info("Health check OK")
    return HealthResponse(status="ok", service="order_receiver")


# ============================================================
# Request Logging Middleware
# ============================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log setiap incoming request."""
    logger.info(
        f"{request.method} {request.url.path} "
        f"from {request.client.host if request.client else 'unknown'}"
    )
    response = await call_next(request)
    logger.info(
        f"{request.method} {request.url.path} → {response.status_code}"
    )
    return response
