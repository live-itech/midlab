"""
services/web_console/api.py — FastAPI Backend Web Console MidLab

Router groups:
- Service Control    : start/stop/restart/auto-restart service
- Instrument CRUD    : add/edit/delete alat, test-connection, force-broadcast
- Protocol           : list modul protocol tersedia
- Logs               : ambil log + SSE stream realtime
- Result Monitor     : list result, retry manual
- Order Monitor      : list order, retry/cancel manual
- Dashboard          : summary status, counts, alerts

Konfigurasi dari config.yaml:
  web_console:
    host: "0.0.0.0"
    port: 8000
    api_key: "..."          # opsional
    static_dir: "static"    # path relatif dari project root
"""

import asyncio
import os
import socket
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from lib.config import Config
from lib.db import (
    DBManager,
    TblInstrument,
    TblResult,
    TblOrder,
    TblServiceLog,
    get_all_settings,
    get_setting,
    set_setting,
    update_result_status,
    update_order_status,
)
from lib.network import get_local_ip
from lib.utils import get_logger
from protocols.base import _PROTOCOL_REGISTRY

from services.web_console.watchdog import ServiceWatchdog


# ============================================================
# App & Global Objects
# ============================================================

app = FastAPI(
    title="MidLab Web Console",
    description="Dashboard dan management console untuk MidLab middleware",
    version="1.0.0",
)

logger = get_logger("webconsole")

# ============================================================
# Static Files & Jinja2 Templates
# ============================================================

_WEBCONSOLE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR = os.path.join(_WEBCONSOLE_DIR, "static")
_TEMPLATES_DIR = os.path.join(_WEBCONSOLE_DIR, "templates")

if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

_templates = Jinja2Templates(directory=_TEMPLATES_DIR)


# ============================================================
# Page Routes (HTML)
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    return _templates.TemplateResponse(request, "dashboard.html", {"active_page": "dashboard"})

@app.get("/instruments", response_class=HTMLResponse)
async def page_instruments(request: Request):
    return _templates.TemplateResponse(request, "instruments.html", {"active_page": "instruments"})

@app.get("/protocols", response_class=HTMLResponse)
async def page_protocols(request: Request):
    return _templates.TemplateResponse(request, "instruments.html", {"active_page": "protocols"})

@app.get("/services", response_class=HTMLResponse)
async def page_services(request: Request):
    return _templates.TemplateResponse(request, "services.html", {"active_page": "services"})

@app.get("/logs", response_class=HTMLResponse)
async def page_logs(request: Request):
    return _templates.TemplateResponse(request, "logs.html", {"active_page": "logs"})

@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    return _templates.TemplateResponse(request, "settings.html", {"active_page": "settings"})

@app.get("/api-docs", response_class=HTMLResponse)
async def page_api_docs(request: Request):
    return _templates.TemplateResponse(request, "api_docs.html", {"active_page": "api_docs"})

@app.get("/results", response_class=HTMLResponse)
async def page_results(request: Request):
    return _templates.TemplateResponse(request, "results.html", {"active_page": "results"})

@app.get("/orders", response_class=HTMLResponse)
async def page_orders(request: Request):
    return _templates.TemplateResponse(request, "orders.html", {"active_page": "orders"})

# Watchdog instance — dibuat saat startup
watchdog: ServiceWatchdog | None = None


@app.on_event("startup")
async def _startup():
    global watchdog

    # Pastikan semua tabel ada (idempotent — create_all_tables hanya create
    # tabel yang belum ada). Diperlukan agar tbl_settings tersedia di
    # deployment lama yang belum punya tabel ini.
    try:
        DBManager().create_all_tables()
    except Exception as e:
        logger.warning(f"create_all_tables gagal saat startup: {e}")

    watchdog = ServiceWatchdog()
    watchdog.ensure_core_services()

    # Register instrument services dari DB
    db = DBManager()
    session = db.get_session()
    try:
        rows = session.query(TblInstrument).filter(TblInstrument.is_active == True).all()
        ids = [r.id for r in rows]
        watchdog.register_instrument_services(ids)
    except Exception as e:
        logger.warning(f"Gagal load instruments untuk watchdog: {e}")
    finally:
        session.close()

    await watchdog.start_monitor()
    logger.info("Web Console API started")


@app.on_event("shutdown")
async def _shutdown():
    if watchdog:
        await watchdog.stop_monitor()
    logger.info("Web Console API stopped")


# ============================================================
# API Key Auth (opsional)
# ============================================================

def _verify_api_key(x_api_key: str = None):
    config = Config()
    expected = config.get("web_console.api_key", "")
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ============================================================
# Pydantic Schemas
# ============================================================

class MessageResponse(BaseModel):
    success: bool
    message: str = ""


class ServiceStatusResponse(BaseModel):
    name: str
    running: bool
    pid: Optional[int] = None
    uptime: Optional[int] = None
    auto_restart: bool = False
    instrument_id: Optional[int] = None


class AutoRestartRequest(BaseModel):
    enabled: bool


class InstrumentCreate(BaseModel):
    name: str
    ip_address: str
    port: int
    protocol: str = Field(description="ASTM, HL7, atau BCI")
    mode: str = Field(default="unidirectional", description="unidirectional atau bidirectional")
    bidir_mode: Optional[str] = Field(default=None, description="broadcast, query, atau broadcast+query")
    broadcast_interval: int = Field(default=30)
    connection: str = Field(default="server", description="server atau client")
    is_active: bool = Field(default=True)


class InstrumentUpdate(BaseModel):
    name: Optional[str] = None
    ip_address: Optional[str] = None
    port: Optional[int] = None
    protocol: Optional[str] = None
    mode: Optional[str] = None
    bidir_mode: Optional[str] = None
    broadcast_interval: Optional[int] = None
    connection: Optional[str] = None
    is_active: Optional[bool] = None


class InstrumentResponse(BaseModel):
    id: int
    name: str
    ip_address: str
    port: int
    protocol: str
    mode: str
    bidir_mode: Optional[str] = None
    broadcast_interval: int
    connection: str
    is_active: bool


class ResultResponse(BaseModel):
    id: int
    instrument_id: int
    protocol: str
    send_status: str
    retry_count: int
    error_message: Optional[str] = None
    received_at: Optional[str] = None
    sent_at: Optional[str] = None
    result_json: Optional[dict] = None


class OrderResponse(BaseModel):
    id: int
    instrument_id: int
    instrument_status: str
    failed_at_service: Optional[str] = None
    retry_count: int
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    sent_to_instrument_at: Optional[str] = None
    order_json: Optional[dict] = None


class ProtocolResponse(BaseModel):
    name: str
    module_path: str


class DashboardResponse(BaseModel):
    services: dict
    results_summary: dict
    orders_summary: dict
    alerts: list


# ============================================================
# [Service Control]
# ============================================================

@app.get("/api/services", response_model=list[ServiceStatusResponse])
async def list_services(x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    statuses = watchdog.get_all_status()
    return [ServiceStatusResponse(**s) for s in statuses.values()]


@app.post("/api/services/{name}/start", response_model=MessageResponse)
async def start_service(name: str, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    # Untuk tcp service, parse instrument_id dari nama
    instrument_id = None
    if name.startswith("tcp_"):
        try:
            instrument_id = int(name.split("_", 1)[1])
        except (IndexError, ValueError):
            raise HTTPException(400, "Format nama tcp service: tcp_<instrument_id>")

    result = watchdog.start_service(name, instrument_id)
    if not result["success"]:
        raise HTTPException(409, result["message"])
    return MessageResponse(success=True, message=result["message"])


@app.post("/api/services/{name}/stop", response_model=MessageResponse)
async def stop_service(name: str, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    result = watchdog.stop_service(name)
    if not result["success"]:
        raise HTTPException(409, result["message"])
    return MessageResponse(success=True, message=result["message"])


@app.post("/api/services/{name}/restart", response_model=MessageResponse)
async def restart_service(name: str, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    result = await asyncio.get_event_loop().run_in_executor(
        None, watchdog.restart_service, name
    )
    if not result["success"]:
        raise HTTPException(500, result["message"])
    return MessageResponse(success=True, message=result["message"])


@app.put("/api/services/{name}/auto-restart", response_model=MessageResponse)
async def toggle_auto_restart(
    name: str,
    body: AutoRestartRequest,
    x_api_key: str = Header(None),
):
    _verify_api_key(x_api_key)
    result = watchdog.set_auto_restart(name, body.enabled)
    return MessageResponse(success=True, message=result["message"])


# ============================================================
# [Instrument CRUD]
# ============================================================

def _instrument_to_response(row: TblInstrument) -> InstrumentResponse:
    return InstrumentResponse(
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


@app.get("/api/instruments", response_model=list[InstrumentResponse])
async def list_instruments(x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    db = DBManager()
    session = db.get_session()
    try:
        rows = session.query(TblInstrument).order_by(TblInstrument.id).all()
        return [_instrument_to_response(r) for r in rows]
    finally:
        session.close()


@app.post("/api/instruments", response_model=InstrumentResponse, status_code=201)
async def create_instrument(body: InstrumentCreate, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)

    # Validasi protocol
    if body.protocol.upper() not in _PROTOCOL_REGISTRY:
        raise HTTPException(400, f"Protocol tidak valid: {body.protocol}")

    db = DBManager()
    session = db.get_session()
    try:
        row = TblInstrument(
            name=body.name,
            ip_address=body.ip_address,
            port=body.port,
            protocol=body.protocol.upper(),
            mode=body.mode,
            bidir_mode=body.bidir_mode,
            broadcast_interval=body.broadcast_interval,
            connection=body.connection,
            is_active=body.is_active,
        )
        session.add(row)
        session.commit()
        session.refresh(row)

        # Register ke watchdog
        watchdog.register_service(f"tcp_{row.id}", instrument_id=row.id)

        logger.info(f"Instrument created: id={row.id} name={row.name}")
        return _instrument_to_response(row)
    except Exception as e:
        session.rollback()
        raise HTTPException(500, f"Gagal membuat instrument: {e}")
    finally:
        session.close()


@app.get("/api/instruments/{instrument_id}", response_model=InstrumentResponse)
async def get_instrument(instrument_id: int, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblInstrument).filter(TblInstrument.id == instrument_id).first()
        if row is None:
            raise HTTPException(404, f"Instrument ID {instrument_id} tidak ditemukan")
        return _instrument_to_response(row)
    finally:
        session.close()


@app.put("/api/instruments/{instrument_id}", response_model=InstrumentResponse)
async def update_instrument(
    instrument_id: int,
    body: InstrumentUpdate,
    x_api_key: str = Header(None),
):
    _verify_api_key(x_api_key)
    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblInstrument).filter(TblInstrument.id == instrument_id).first()
        if row is None:
            raise HTTPException(404, f"Instrument ID {instrument_id} tidak ditemukan")

        update_data = body.model_dump(exclude_none=True)
        if "protocol" in update_data:
            if update_data["protocol"].upper() not in _PROTOCOL_REGISTRY:
                raise HTTPException(400, f"Protocol tidak valid: {update_data['protocol']}")
            update_data["protocol"] = update_data["protocol"].upper()

        for key, value in update_data.items():
            setattr(row, key, value)

        session.commit()
        session.refresh(row)

        logger.info(f"Instrument updated: id={instrument_id}")
        return _instrument_to_response(row)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(500, f"Gagal update instrument: {e}")
    finally:
        session.close()


@app.delete("/api/instruments/{instrument_id}", response_model=MessageResponse)
async def delete_instrument(instrument_id: int, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)

    # Stop service dulu jika running
    svc_name = f"tcp_{instrument_id}"
    watchdog.stop_service(svc_name)

    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblInstrument).filter(TblInstrument.id == instrument_id).first()
        if row is None:
            raise HTTPException(404, f"Instrument ID {instrument_id} tidak ditemukan")

        session.delete(row)
        session.commit()

        logger.info(f"Instrument deleted: id={instrument_id}")
        return MessageResponse(success=True, message=f"Instrument {instrument_id} dihapus")
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(500, f"Gagal hapus instrument: {e}")
    finally:
        session.close()


@app.post("/api/instruments/{instrument_id}/test-connection", response_model=MessageResponse)
async def test_connection(instrument_id: int, x_api_key: str = Header(None)):
    """Test koneksi TCP ke alat."""
    _verify_api_key(x_api_key)

    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblInstrument).filter(TblInstrument.id == instrument_id).first()
        if row is None:
            raise HTTPException(404, f"Instrument ID {instrument_id} tidak ditemukan")

        ip = row.ip_address
        port = row.port
    finally:
        session.close()

    # Test TCP connection di thread terpisah
    def _test():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((ip, port))
            sock.close()
            return True, f"Koneksi ke {ip}:{port} berhasil"
        except socket.timeout:
            return False, f"Timeout koneksi ke {ip}:{port}"
        except ConnectionRefusedError:
            return False, f"Koneksi ditolak oleh {ip}:{port}"
        except OSError as e:
            return False, f"Gagal koneksi ke {ip}:{port}: {e}"

    success, message = await asyncio.get_event_loop().run_in_executor(None, _test)
    logger.info(f"Test connection instrument {instrument_id}: {message}")

    if not success:
        raise HTTPException(502, message)
    return MessageResponse(success=True, message=message)


@app.post("/api/instruments/{instrument_id}/force-broadcast", response_model=MessageResponse)
async def force_broadcast(instrument_id: int, x_api_key: str = Header(None)):
    """
    Trigger broadcast sekali untuk instrument tertentu.

    Set semua pending orders ke status 'pending' (reset failed) agar
    BroadcastWorker segera mengirimnya di cycle berikutnya.
    """
    _verify_api_key(x_api_key)

    db = DBManager()
    session = db.get_session()
    try:
        # Verifikasi instrument ada
        inst = session.query(TblInstrument).filter(TblInstrument.id == instrument_id).first()
        if inst is None:
            raise HTTPException(404, f"Instrument ID {instrument_id} tidak ditemukan")

        # Reset failed orders ke pending agar di-broadcast ulang
        updated = (
            session.query(TblOrder)
            .filter(
                TblOrder.instrument_id == instrument_id,
                TblOrder.instrument_status == "failed",
            )
            .update({"instrument_status": "pending", "error_message": "force-broadcast reset"})
        )
        session.commit()

        message = f"Force broadcast: {updated} failed order(s) di-reset ke pending"
        logger.info(f"Instrument {instrument_id}: {message}")
        return MessageResponse(success=True, message=message)

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(500, str(e))
    finally:
        session.close()


# ============================================================
# [Protocol]
# ============================================================

@app.get("/api/protocols", response_model=list[ProtocolResponse])
async def list_protocols(x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    return [
        ProtocolResponse(name=name, module_path=path)
        for name, path in _PROTOCOL_REGISTRY.items()
    ]


# ============================================================
# [Settings] — LIS bridging configuration
# ============================================================

class SettingsResponse(BaseModel):
    """
    Settings bridging MidLab ↔ LIS.

    - order_api_url: URL endpoint MidLab yang dipakai LIS untuk POST order
                     (auto-detect dari IP server, read-only).
    - order_api_key: API key untuk Order Receiver (set via config.yaml,
                     read-only di sini — keamanan; key tidak dikirim ke
                     UI dalam bentuk plaintext).
    - order_api_key_set: True jika api_key Order Receiver di-config.
    - lis_api_url: URL LIS REST API untuk POST hasil (editable).
    - lis_api_key_masked: API key LIS (di-mask, hanya 4 char terakhir).
    - lis_api_key_set: True jika lis_api_key sudah di-set.
    - local_ip: IP LAN aktif server (untuk diagnostic).
    """
    order_api_url: str
    order_api_key_set: bool
    lis_api_url: str
    lis_api_key_masked: str
    lis_api_key_set: bool
    local_ip: str


class SettingsUpdateRequest(BaseModel):
    """Body PUT /api/settings — kosong / null = jangan ubah field tsb."""
    lis_api_url: Optional[str] = None
    # Kirim string kosong "" untuk hapus key; null untuk tidak ubah
    lis_api_key: Optional[str] = None


class LisTestRequest(BaseModel):
    """Body POST /api/settings/test-lis — opsional override URL/key untuk dry-run."""
    lis_api_url: Optional[str] = None
    lis_api_key: Optional[str] = None


def _mask_key(key: str) -> str:
    """Mask API key — tampilkan hanya 4 karakter terakhir."""
    if not key:
        return ""
    if len(key) <= 4:
        return "*" * len(key)
    return "*" * (len(key) - 4) + key[-4:]


def _build_settings_response() -> SettingsResponse:
    """Assemble SettingsResponse dari DB + config + auto-detect IP."""
    config = Config()
    local_ip = get_local_ip()
    order_port = config.get("order_receiver.port", 8001)
    order_api_key = config.get("order_receiver.api_key", "") or ""

    # LIS settings: DB override → config.yaml fallback
    lis_url = (
        get_setting("lis.api_url", default=None)
        or config.get("lis.api_url", "")
        or ""
    )
    lis_key = (
        get_setting("lis.api_key", default=None)
        or config.get("lis.api_key", "")
        or ""
    )

    return SettingsResponse(
        order_api_url=f"http://{local_ip}:{order_port}/api/orders",
        order_api_key_set=bool(order_api_key),
        lis_api_url=lis_url,
        lis_api_key_masked=_mask_key(lis_key),
        lis_api_key_set=bool(lis_key),
        local_ip=local_ip,
    )


@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings(x_api_key: str = Header(None)):
    """
    Ambil settings bridging LIS.

    `order_api_url` di-generate dari IP LAN server saat ini — jika MidLab
    di-deploy di server lain, URL akan otomatis menyesuaikan.
    """
    _verify_api_key(x_api_key)
    return _build_settings_response()


@app.put("/api/settings", response_model=SettingsResponse)
async def update_settings(
    body: SettingsUpdateRequest,
    x_api_key: str = Header(None),
):
    """
    Update LIS API URL & key. Disimpan di tbl_settings (override config.yaml).
    Field yang null tidak diubah; string kosong "" akan menghapus override
    (kembali pakai value dari config.yaml).

    ResultSenderService auto-reload settings ini setiap poll cycle —
    tidak perlu restart service.
    """
    _verify_api_key(x_api_key)

    if body.lis_api_url is not None:
        url = body.lis_api_url.strip()
        if url and not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(400, "lis_api_url harus dimulai http:// atau https://")
        ok = set_setting("lis.api_url", url)
        if not ok:
            raise HTTPException(500, "Gagal simpan lis.api_url")
        logger.info(f"Setting lis.api_url updated: {url or '(cleared)'}")

    if body.lis_api_key is not None:
        ok = set_setting("lis.api_key", body.lis_api_key)
        if not ok:
            raise HTTPException(500, "Gagal simpan lis.api_key")
        logger.info("Setting lis.api_key updated")

    return _build_settings_response()


@app.post("/api/settings/test-lis", response_model=MessageResponse)
async def test_lis_connection(
    body: LisTestRequest,
    x_api_key: str = Header(None),
):
    """
    Test koneksi ke LIS API: POST dummy payload, return status.

    URL & key dari body kalau diisi (untuk preview sebelum save), kalau
    tidak pakai value yang tersimpan di DB/config.
    """
    _verify_api_key(x_api_key)

    config = Config()
    url = body.lis_api_url or get_setting("lis.api_url") or config.get("lis.api_url", "")
    key = body.lis_api_key if body.lis_api_key is not None else (
        get_setting("lis.api_key") or config.get("lis.api_key", "")
    )

    if not url:
        raise HTTPException(400, "lis.api_url belum di-set")

    import aiohttp

    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key

    # Dummy payload — LIS yang well-behaved akan return 4xx (validasi),
    # tapi kalau bisa konek + parse JSON, koneksi dianggap OK.
    probe = {"mid_version": "1.0", "_probe": True, "instrument_id": 0}

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=probe, headers=headers) as resp:
                status = resp.status
                if 200 <= status < 500:
                    # 2xx atau 4xx → endpoint reachable & parse JSON OK
                    return MessageResponse(
                        success=True,
                        message=f"Koneksi OK — LIS merespon HTTP {status}",
                    )
                raise HTTPException(502, f"LIS error: HTTP {status}")
    except aiohttp.ClientConnectorError as e:
        raise HTTPException(502, f"Tidak bisa konek ke LIS: {e}")
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timeout konek ke LIS (10s)")
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"LIS error: {e}")


# ============================================================
# [Logs]
# ============================================================

LOG_DIR = "/var/log/midlab"


def _resolve_log_file(service: str) -> str:
    """Tentukan nama file log berdasarkan service name."""
    if service.startswith("tcp_"):
        return os.path.join(LOG_DIR, f"{service}.log")
    return os.path.join(LOG_DIR, f"{service}.log")


@app.get("/api/logs/{service}")
async def get_logs(
    service: str,
    lines: int = Query(default=100, ge=1, le=5000),
    level: str = Query(default=None, description="Filter level: INFO, WARNING, ERROR"),
    search: str = Query(default=None, description="Search text"),
    x_api_key: str = Header(None),
):
    """Ambil N baris terakhir dari log file."""
    _verify_api_key(x_api_key)

    log_path = _resolve_log_file(service)
    if not os.path.exists(log_path):
        raise HTTPException(404, f"Log file untuk {service} tidak ditemukan")

    # Baca baris terakhir dari file
    def _read_tail():
        result_lines = []
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                # Baca semua baris (untuk file kecil-sedang ini OK)
                all_lines = f.readlines()
                tail = all_lines[-lines:]

                for line in tail:
                    line = line.rstrip("\n\r")
                    if not line:
                        continue

                    # Filter level
                    if level and f"[{level.upper()}]" not in line:
                        continue

                    # Filter search text
                    if search and search.lower() not in line.lower():
                        continue

                    result_lines.append(line)

        except Exception as e:
            result_lines.append(f"Error membaca log: {e}")

        return result_lines

    log_lines = await asyncio.get_event_loop().run_in_executor(None, _read_tail)
    return {"service": service, "lines": log_lines, "total": len(log_lines)}


@app.get("/api/logs/{service}/stream")
async def stream_logs(
    service: str,
    x_api_key: str = Header(None),
):
    """SSE stream log realtime (tail -f style)."""
    _verify_api_key(x_api_key)

    log_path = _resolve_log_file(service)
    if not os.path.exists(log_path):
        raise HTTPException(404, f"Log file untuk {service} tidak ditemukan")

    async def _event_generator():
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                # Seek ke akhir file
                f.seek(0, 2)

                while True:
                    line = f.readline()
                    if line:
                        line = line.rstrip("\n\r")
                        if line:
                            yield f"data: {line}\n\n"
                    else:
                        await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# [Result Monitor]
# ============================================================

@app.get("/api/results", response_model=list[ResultResponse])
async def list_results(
    status: str = Query(default=None, description="Filter: pending, sent, failed"),
    instrument_id: int = Query(default=None),
    date_from: str = Query(default=None, description="ISO8601 date"),
    date_to: str = Query(default=None, description="ISO8601 date"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    x_api_key: str = Header(None),
):
    _verify_api_key(x_api_key)

    db = DBManager()
    session = db.get_session()
    try:
        q = session.query(TblResult)

        if status:
            q = q.filter(TblResult.send_status == status)
        if instrument_id:
            q = q.filter(TblResult.instrument_id == instrument_id)
        if date_from:
            try:
                dt_from = datetime.fromisoformat(date_from)
                q = q.filter(TblResult.received_at >= dt_from)
            except ValueError:
                raise HTTPException(400, f"date_from format invalid: {date_from}")
        if date_to:
            try:
                dt_to = datetime.fromisoformat(date_to)
                q = q.filter(TblResult.received_at <= dt_to)
            except ValueError:
                raise HTTPException(400, f"date_to format invalid: {date_to}")

        rows = (
            q.order_by(TblResult.received_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        return [
            ResultResponse(
                id=r.id,
                instrument_id=r.instrument_id,
                protocol=r.protocol,
                send_status=r.send_status,
                retry_count=r.retry_count or 0,
                error_message=r.error_message,
                received_at=r.received_at.isoformat() if r.received_at else None,
                sent_at=r.sent_at.isoformat() if r.sent_at else None,
                result_json=r.result_json,
            )
            for r in rows
        ]
    finally:
        session.close()


@app.post("/api/results/{result_id}/retry", response_model=MessageResponse)
async def retry_result(result_id: int, x_api_key: str = Header(None)):
    """Reset result ke pending agar ResultSenderService kirim ulang."""
    _verify_api_key(x_api_key)

    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblResult).filter(TblResult.id == result_id).first()
        if row is None:
            raise HTTPException(404, f"Result ID {result_id} tidak ditemukan")

        row.send_status = "pending"
        row.error_message = "manual retry via web console"
        session.commit()

        logger.info(f"Result {result_id} di-reset ke pending (manual retry)")
        return MessageResponse(success=True, message=f"Result {result_id} di-set ke pending")
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(500, str(e))
    finally:
        session.close()


# ============================================================
# [Order Monitor]
# ============================================================

@app.get("/api/orders", response_model=list[OrderResponse])
async def list_orders(
    status: str = Query(default=None, description="Filter: pending, sent, failed"),
    instrument_id: int = Query(default=None),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    x_api_key: str = Header(None),
):
    _verify_api_key(x_api_key)

    db = DBManager()
    session = db.get_session()
    try:
        q = session.query(TblOrder)

        if status:
            q = q.filter(TblOrder.instrument_status == status)
        if instrument_id:
            q = q.filter(TblOrder.instrument_id == instrument_id)
        if date_from:
            try:
                dt_from = datetime.fromisoformat(date_from)
                q = q.filter(TblOrder.created_at >= dt_from)
            except ValueError:
                raise HTTPException(400, f"date_from format invalid: {date_from}")
        if date_to:
            try:
                dt_to = datetime.fromisoformat(date_to)
                q = q.filter(TblOrder.created_at <= dt_to)
            except ValueError:
                raise HTTPException(400, f"date_to format invalid: {date_to}")

        rows = (
            q.order_by(TblOrder.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        return [
            OrderResponse(
                id=o.id,
                instrument_id=o.instrument_id,
                instrument_status=o.instrument_status,
                failed_at_service=o.failed_at_service,
                retry_count=o.retry_count or 0,
                error_message=o.error_message,
                created_at=o.created_at.isoformat() if o.created_at else None,
                sent_to_instrument_at=(
                    o.sent_to_instrument_at.isoformat()
                    if o.sent_to_instrument_at
                    else None
                ),
                order_json=o.order_json,
            )
            for o in rows
        ]
    finally:
        session.close()


@app.post("/api/orders/{order_id}/retry", response_model=MessageResponse)
async def retry_order(order_id: int, x_api_key: str = Header(None)):
    """Reset order ke pending agar TCPSocketService kirim ulang."""
    _verify_api_key(x_api_key)

    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblOrder).filter(TblOrder.id == order_id).first()
        if row is None:
            raise HTTPException(404, f"Order ID {order_id} tidak ditemukan")

        row.instrument_status = "pending"
        row.error_message = "manual retry via web console"
        row.failed_at_service = None
        session.commit()

        logger.info(f"Order {order_id} di-reset ke pending (manual retry)")
        return MessageResponse(success=True, message=f"Order {order_id} di-set ke pending")
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(500, str(e))
    finally:
        session.close()


@app.post("/api/orders/{order_id}/cancel", response_model=MessageResponse)
async def cancel_order(order_id: int, x_api_key: str = Header(None)):
    """Cancel order — set status ke failed dengan keterangan cancelled."""
    _verify_api_key(x_api_key)

    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblOrder).filter(TblOrder.id == order_id).first()
        if row is None:
            raise HTTPException(404, f"Order ID {order_id} tidak ditemukan")

        if row.instrument_status == "sent":
            raise HTTPException(400, "Order sudah terkirim, tidak bisa di-cancel")

        row.instrument_status = "failed"
        row.error_message = "cancelled via web console"
        row.failed_at_service = "web_console"
        session.commit()

        logger.info(f"Order {order_id} cancelled via web console")
        return MessageResponse(success=True, message=f"Order {order_id} cancelled")
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(500, str(e))
    finally:
        session.close()


# ============================================================
# [Dashboard]
# ============================================================

@app.get("/api/dashboard", response_model=DashboardResponse)
async def dashboard(x_api_key: str = Header(None)):
    """
    Summary dashboard:
    - Status semua service
    - Jumlah result per status
    - Jumlah order per status
    - Alert terbaru (failed results/orders)
    """
    _verify_api_key(x_api_key)

    services = watchdog.get_all_status()

    db = DBManager()
    session = db.get_session()
    try:
        # Result counts per status
        from sqlalchemy import func

        result_counts = (
            session.query(TblResult.send_status, func.count(TblResult.id))
            .group_by(TblResult.send_status)
            .all()
        )
        results_summary = {status: count for status, count in result_counts}

        # Order counts per status
        order_counts = (
            session.query(TblOrder.instrument_status, func.count(TblOrder.id))
            .group_by(TblOrder.instrument_status)
            .all()
        )
        orders_summary = {status: count for status, count in order_counts}

        # Alert: recent failed results (max 10)
        alerts = []
        failed_results = (
            session.query(TblResult)
            .filter(TblResult.send_status == "failed")
            .order_by(TblResult.received_at.desc())
            .limit(10)
            .all()
        )
        for r in failed_results:
            alerts.append({
                "type": "result_failed",
                "id": r.id,
                "instrument_id": r.instrument_id,
                "message": r.error_message or "Send failed",
                "timestamp": r.received_at.isoformat() if r.received_at else None,
            })

        # Alert: recent failed orders (max 10)
        failed_orders = (
            session.query(TblOrder)
            .filter(TblOrder.instrument_status == "failed")
            .order_by(TblOrder.created_at.desc())
            .limit(10)
            .all()
        )
        for o in failed_orders:
            alerts.append({
                "type": "order_failed",
                "id": o.id,
                "instrument_id": o.instrument_id,
                "message": o.error_message or "Send to instrument failed",
                "failed_at": o.failed_at_service,
                "timestamp": o.created_at.isoformat() if o.created_at else None,
            })

        # Sort alerts by timestamp descending
        alerts.sort(key=lambda a: a.get("timestamp") or "", reverse=True)

        return DashboardResponse(
            services=services,
            results_summary=results_summary,
            orders_summary=orders_summary,
            alerts=alerts[:20],
        )

    finally:
        session.close()


# ============================================================
# Request Logging Middleware
# ============================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(
        f"{request.method} {request.url.path} "
        f"from {request.client.host if request.client else 'unknown'}"
    )
    response = await call_next(request)
    return response
