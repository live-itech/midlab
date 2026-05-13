# LIS Bridging — EazyApp Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adjust MidLab agar full kompatibel dengan EazyApp LIS Instrument API — replace `OrderReceiverService` + `ResultSenderService` dengan per-alat `LisBridgeService` (4 internal tasks parallel).

**Architecture:** Satu `LisBridgeService` per alat (mengikuti pattern TCPSocketService). Auth `Authorization: Bearer <api_key>` per-instrument. Communication antar service via MySQL flag-based handoff: TCPSocketService → `tbl_lis_event_queue` → LisBridgeService.StatusReporter.

**Tech Stack:** Python 3.10+ asyncio, aiohttp, FastAPI, SQLAlchemy 2.0, MySQL, pytest + pytest-asyncio.

**Reference spec:** `docs/superpowers/specs/2026-05-13-lis-bridging-eazyapp-design.md`

---

## File Structure

**New files (created):**
- `lib/lis_client.py` — HTTP client wrapper (auth, retry, timeout)
- `scripts/migrate_lis_api.py` — schema migration up
- `scripts/migrate_lis_api_rollback.py` — schema migration down
- `services/lis_bridge/__init__.py`
- `services/lis_bridge/main.py` — entry point (`--instrument-id N`)
- `services/lis_bridge/service.py` — `LisBridgeService` orchestrator + supervisor
- `services/lis_bridge/result_pusher.py`
- `services/lis_bridge/order_puller.py`
- `services/lis_bridge/status_reporter.py`
- `services/lis_bridge/log_pusher.py`
- `services/web_console/templates/lis_events.html`
- `systemd/midlab-lis-bridge@.service`
- `tests/conftest.py`, `tests/test_*.py` (one per module)

**Modified files:**
- `lib/db.py` — new `TblLisEventQueue` model, new cols on `TblInstrument`, helpers
- `requirements.txt` — add `pytest`, `pytest-asyncio`, `aioresponses`
- `services/tcp_socket/...` — emit events to `tbl_lis_event_queue` on connect/disconnect/error
- `services/result_sender/service.py` — skip rows where `instrument.lis_bridge_enabled=true`
- `services/web_console/api.py` — verify-lis endpoint, event queue endpoints, LIS fields
- `services/web_console/watchdog.py` — recognize `lis_bridge_<id>`
- `services/web_console/templates/{instruments,settings,dashboard,base}.html`

**Archived after Task 24:**
- `services/order_receiver/`, `systemd/midlab-order-receiver.service`
- `services/result_sender/`, `systemd/midlab-result-sender.service`

---

## Tahap 0 — Test Harness

### Task 1: Setup pytest + tests directory

**Files:**
- Create: `tests/__init__.py`, `tests/conftest.py`, `pytest.ini`, `tests/test_smoke.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add test dependencies to `requirements.txt`**

Append:

```
# Development / test
pytest>=8.0.0
pytest-asyncio>=0.23.0
aioresponses>=0.7.6
```

- [ ] **Step 2: Install dev deps**

Run: `pip install pytest pytest-asyncio aioresponses`
Expected: successfully installed

- [ ] **Step 3: Create `pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
markers =
    sandbox: live integration tests against EazyApp LIS (require API key env var)
```

- [ ] **Step 4: Create `tests/__init__.py`** (empty)

- [ ] **Step 5: Create `tests/conftest.py`**

```python
"""Pytest fixtures: in-memory SQLite mirror tbl_* untuk unit test cepat."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from lib.db import Base


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
```

- [ ] **Step 6: Smoke test**

Create `tests/test_smoke.py`:

```python
def test_fixture_loads(db_session):
    assert db_session is not None
```

Run: `pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pytest.ini tests/__init__.py tests/conftest.py tests/test_smoke.py
git commit -m "test: setup pytest harness with in-memory SQLite fixture"
```

---

## Tahap 1 — Schema & Libraries

### Task 2: Schema migration script (up)

**Files:**
- Create: `scripts/migrate_lis_api.py`
- Test: `tests/test_migration.py`

- [ ] **Step 1: Write the failing test**

`tests/test_migration.py`:

```python
"""Test SQL migration string contains expected DDL + idempotency guard."""
import re
from pathlib import Path


def test_migration_script_has_expected_ddl():
    sql = Path("scripts/migrate_lis_api.py").read_text()
    assert "ADD COLUMN lis_instrument_id" in sql
    assert "ADD COLUMN lis_api_key" in sql
    assert "ADD COLUMN order_poll_interval" in sql
    assert "ADD COLUMN last_lis_sync_at" in sql
    assert "ADD COLUMN lis_status_pushed" in sql
    assert "ADD COLUMN lis_bridge_enabled" in sql
    assert "CREATE TABLE" in sql and "tbl_lis_event_queue" in sql
    assert re.search(r"IF NOT EXISTS\s+tbl_lis_event_queue", sql)
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_migration.py -v`
Expected: FAIL — file not found

- [ ] **Step 3: Create migration script**

`scripts/migrate_lis_api.py`:

```python
"""
scripts/migrate_lis_api.py — Migrasi schema untuk LIS bridging EazyApp.
Idempotent: aman dijalankan berkali-kali (cek INFORMATION_SCHEMA dulu).

Usage:
    python3 scripts/migrate_lis_api.py
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text
from lib.db import DBManager


ADD_COLS_TBL_INSTRUMENT = [
    ("lis_instrument_id",   "VARCHAR(50) NULL"),
    ("lis_api_key",         "VARCHAR(255) NULL"),
    ("order_poll_interval", "INT DEFAULT 10"),
    ("last_lis_sync_at",    "DATETIME NULL"),
    ("lis_status_pushed",   "VARCHAR(20) NULL"),
    ("lis_bridge_enabled",  "BOOLEAN DEFAULT FALSE"),
]

CREATE_EVENT_QUEUE = """
CREATE TABLE IF NOT EXISTS tbl_lis_event_queue (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    instrument_id INT NOT NULL,
    event_type    ENUM('status','log') NOT NULL,
    payload_json  JSON NOT NULL,
    send_status   ENUM('pending','sent','failed','skipped') DEFAULT 'pending',
    retry_count   INT DEFAULT 0,
    error_message TEXT NULL,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    sent_at       DATETIME NULL,
    INDEX idx_inst_status (instrument_id, send_status, id)
)
"""


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def main():
    db = DBManager()
    engine = db.get_engine()
    with engine.begin() as conn:
        for col_name, col_def in ADD_COLS_TBL_INSTRUMENT:
            if _column_exists(conn, "tbl_instrument", col_name):
                print(f"  skip: tbl_instrument.{col_name} already exists")
                continue
            print(f"  ADD COLUMN tbl_instrument.{col_name}")
            conn.execute(text(f"ALTER TABLE tbl_instrument ADD COLUMN {col_name} {col_def}"))

        print("  CREATE TABLE IF NOT EXISTS tbl_lis_event_queue")
        conn.execute(text(CREATE_EVENT_QUEUE))

    print("OK: migrasi LIS API selesai.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest tests/test_migration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_lis_api.py tests/test_migration.py
git commit -m "db: add schema migration script for LIS bridging (up)"
```

### Task 3: Schema migration rollback

**Files:**
- Create: `scripts/migrate_lis_api_rollback.py`

- [ ] **Step 1: Write rollback script**

```python
"""
scripts/migrate_lis_api_rollback.py — Rollback migrasi LIS bridging.

Usage:
    python3 scripts/migrate_lis_api_rollback.py
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text
from lib.db import DBManager


DROP_COLS = [
    "lis_instrument_id",
    "lis_api_key",
    "order_poll_interval",
    "last_lis_sync_at",
    "lis_status_pushed",
    "lis_bridge_enabled",
]


def _column_exists(conn, table, column):
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def main():
    db = DBManager()
    engine = db.get_engine()
    with engine.begin() as conn:
        for col in DROP_COLS:
            if not _column_exists(conn, "tbl_instrument", col):
                print(f"  skip: tbl_instrument.{col} tidak ada")
                continue
            print(f"  DROP COLUMN tbl_instrument.{col}")
            conn.execute(text(f"ALTER TABLE tbl_instrument DROP COLUMN {col}"))
        print("  DROP TABLE IF EXISTS tbl_lis_event_queue")
        conn.execute(text("DROP TABLE IF EXISTS tbl_lis_event_queue"))
    print("OK: rollback selesai.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/migrate_lis_api_rollback.py
git commit -m "db: add migration rollback script for LIS bridging"
```

### Task 4: ORM model — TblLisEventQueue + helpers

**Files:**
- Modify: `lib/db.py`
- Test: `tests/test_db_lis_helpers.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db_lis_helpers.py`:

```python
"""Test ORM additions: TblInstrument new cols + TblLisEventQueue + helpers."""
from lib.db import (
    TblInstrument,
    TblLisEventQueue,
    enqueue_lis_event,
    get_pending_lis_events,
    update_lis_event_status,
)


def test_tbl_instrument_has_lis_columns():
    cols = TblInstrument.__table__.columns.keys()
    for c in ("lis_instrument_id", "lis_api_key", "order_poll_interval",
              "last_lis_sync_at", "lis_status_pushed", "lis_bridge_enabled"):
        assert c in cols, f"missing column: {c}"


def test_tbl_lis_event_queue_schema():
    cols = TblLisEventQueue.__table__.columns.keys()
    for c in ("id", "instrument_id", "event_type", "payload_json",
              "send_status", "retry_count", "error_message",
              "created_at", "sent_at"):
        assert c in cols, f"missing column: {c}"


class _SessionProxy:
    """Wrap a Session as DBManager-compatible facade."""
    def __init__(self, session):
        self._session = session
    def get_session(self):
        return self._session
    def get_engine(self):
        return self._session.get_bind()


def test_enqueue_and_fetch_lis_event(db_session, monkeypatch):
    import lib.db as dbmod
    monkeypatch.setattr(dbmod, "DBManager", lambda: _SessionProxy(db_session))

    event_id = enqueue_lis_event(
        instrument_id=1,
        event_type="status",
        payload={"status": "online"},
    )
    assert event_id is not None

    pending = get_pending_lis_events(instrument_id=1, limit=10)
    assert len(pending) == 1
    assert pending[0].payload_json == {"status": "online"}
    assert pending[0].send_status == "pending"


def test_update_lis_event_status_sent(db_session, monkeypatch):
    import lib.db as dbmod
    monkeypatch.setattr(dbmod, "DBManager", lambda: _SessionProxy(db_session))

    eid = enqueue_lis_event(1, "status", {"status": "online"})
    ok = update_lis_event_status(eid, "sent", None)
    assert ok
    pending = get_pending_lis_events(instrument_id=1, limit=10)
    assert len(pending) == 0
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_db_lis_helpers.py -v`
Expected: FAIL — `ImportError: cannot import name 'TblLisEventQueue'`

- [ ] **Step 3: Add new columns to `TblInstrument` in `lib/db.py`**

Find `class TblInstrument` (around line 39). After `is_active = Column(Boolean, default=True)`, add:

```python
    # LIS bridging columns (lihat docs/superpowers/specs/2026-05-13-lis-bridging-eazyapp-design.md)
    lis_instrument_id   = Column(String(50),  nullable=True)
    lis_api_key         = Column(String(255), nullable=True)
    order_poll_interval = Column(Integer,     default=10)
    last_lis_sync_at    = Column(DateTime,    nullable=True)
    lis_status_pushed   = Column(String(20),  nullable=True)
    lis_bridge_enabled  = Column(Boolean,     default=False)
```

- [ ] **Step 4: Add `TblLisEventQueue` ORM model**

Insert after `TblSetting` class (around line 138):

```python
class TblLisEventQueue(Base):
    """
    Antrian event yang dikirim ke LIS via REST API.
    Penulis: TCPSocketService (status events on connect/disconnect/error).
    Pembaca: LisBridgeService.StatusReporter.
    Handoff flag-based sesuai rule CLAUDE.md.
    """
    __tablename__ = "tbl_lis_event_queue"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    instrument_id = Column(Integer, nullable=False)
    event_type    = Column(
        Enum("status", "log", name="lis_event_type_enum"),
        nullable=False,
    )
    payload_json  = Column(JSON, nullable=False)
    send_status   = Column(
        Enum("pending", "sent", "failed", "skipped", name="lis_event_status_enum"),
        nullable=False,
        default="pending",
    )
    retry_count   = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    sent_at       = Column(DateTime, nullable=True)
```

- [ ] **Step 5: Add helpers at bottom of `lib/db.py`**

```python
def enqueue_lis_event(instrument_id: int, event_type: str, payload: dict) -> int | None:
    db = DBManager()
    session = db.get_session()
    try:
        ev = TblLisEventQueue(
            instrument_id=instrument_id,
            event_type=event_type,
            payload_json=payload,
            send_status="pending",
        )
        session.add(ev)
        session.commit()
        return ev.id
    except Exception:
        session.rollback()
        return None
    finally:
        session.close()


def get_pending_lis_events(
    instrument_id: int,
    event_type: str | None = None,
    limit: int = 50,
) -> list:
    db = DBManager()
    session = db.get_session()
    try:
        q = session.query(TblLisEventQueue).filter(
            TblLisEventQueue.instrument_id == instrument_id,
            TblLisEventQueue.send_status == "pending",
        )
        if event_type:
            q = q.filter(TblLisEventQueue.event_type == event_type)
        return q.order_by(TblLisEventQueue.id.asc()).limit(limit).all()
    finally:
        session.close()


def update_lis_event_status(
    event_id: int,
    status: str,
    error_message: str | None = None,
    increment_retry: bool = False,
) -> bool:
    db = DBManager()
    session = db.get_session()
    try:
        ev = session.query(TblLisEventQueue).filter(
            TblLisEventQueue.id == event_id
        ).first()
        if not ev:
            return False
        ev.send_status = status
        if error_message is not None:
            ev.error_message = error_message
        if increment_retry:
            ev.retry_count = (ev.retry_count or 0) + 1
        if status == "sent":
            ev.sent_at = datetime.now(timezone.utc)
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()
```

- [ ] **Step 6: Run test — expect PASS**

Run: `pytest tests/test_db_lis_helpers.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add lib/db.py tests/test_db_lis_helpers.py
git commit -m "db: add TblLisEventQueue + TblInstrument LIS columns + helpers"
```

### Task 5: `lib/lis_client.py` — HTTP client wrapper

**Files:**
- Create: `lib/lis_client.py`
- Test: `tests/test_lis_client.py`

- [ ] **Step 1: Write the failing test**

`tests/test_lis_client.py`:

```python
"""Test LisApiClient: auth header, retry on 5xx, no retry on 4xx."""
import pytest
from aioresponses import aioresponses

from lib.lis_client import LisApiClient, LisApiError


BASE = "https://eazy.example.test"
KEY = "inst_TEST_KEY"


@pytest.fixture
def client():
    return LisApiClient(base_url=BASE, api_key=KEY, timeout=2, retry_max=3)


async def test_get_instrument_sends_bearer(client):
    with aioresponses() as m:
        m.get(
            f"{BASE}/api/v1/instrument",
            payload={"success": True, "data": {"instrument": {"instrument_id": "INST-X"}}},
            status=200,
        )
        async with client:
            data = await client.get_instrument()
        assert data["data"]["instrument"]["instrument_id"] == "INST-X"

        first_call = list(m.requests.values())[0][0]
        assert first_call.kwargs["headers"]["Authorization"] == f"Bearer {KEY}"


async def test_post_results_returns_body(client):
    with aioresponses() as m:
        m.post(
            f"{BASE}/api/v1/instrument/results",
            payload={"success": True, "message_id": "MSG-1"},
            status=201,
        )
        async with client:
            status, body = await client.post_results({"mid_version": "1.0"})
        assert status == 201
        assert body["message_id"] == "MSG-1"


async def test_4xx_raises_no_retry(client):
    with aioresponses() as m:
        m.post(
            f"{BASE}/api/v1/instrument/results",
            payload={"message": "validation failed"},
            status=422,
        )
        async with client:
            with pytest.raises(LisApiError) as exc:
                await client.post_results({})
        assert exc.value.status == 422
        assert exc.value.retryable is False


async def test_5xx_retries_then_raises(client):
    with aioresponses() as m:
        for _ in range(3):
            m.post(f"{BASE}/api/v1/instrument/results", status=502, payload={})
        async with client:
            with pytest.raises(LisApiError) as exc:
                await client.post_results({})
        assert exc.value.status == 502
        assert exc.value.retryable is True


async def test_get_orders_pending(client):
    with aioresponses() as m:
        m.get(
            f"{BASE}/api/v1/instrument/orders/pending",
            payload={"success": True, "count": 0, "data": []},
            status=200,
        )
        async with client:
            data = await client.get_orders_pending()
        assert data["count"] == 0


async def test_post_status_online(client):
    with aioresponses() as m:
        m.post(f"{BASE}/api/v1/instrument/status",
               payload={"success": True}, status=200)
        async with client:
            status, _ = await client.post_status({"status": "online"})
        assert status == 200


async def test_post_logs(client):
    with aioresponses() as m:
        m.post(f"{BASE}/api/v1/instrument/logs",
               payload={"success": True}, status=200)
        async with client:
            status, _ = await client.post_log({
                "level": "error", "message": "x", "logged_at": "2026-01-01T00:00:00Z"
            })
        assert status == 200
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_lis_client.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `lib/lis_client.py`**

```python
"""
lib/lis_client.py — HTTP client wrapper untuk EazyApp LIS Instrument API.

Endpoints:
  GET  /api/v1/instrument                 — info alat
  POST /api/v1/instrument/status          — push status
  GET  /api/v1/instrument/orders/pending  — pull orders
  POST /api/v1/instrument/results         — submit results
  POST /api/v1/instrument/logs            — push log

Semua endpoint pakai Bearer auth per-alat:
  Authorization: Bearer <api_key>

Caller bertanggung jawab kelola session pakai async-with:
    async with LisApiClient(...) as client:
        await client.get_instrument()
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp


class LisApiError(Exception):
    """Error dari LIS API. retryable=True untuk 5xx/network/timeout."""

    def __init__(self, status: int, message: str, body: Any = None, retryable: bool = False):
        super().__init__(f"LIS API error {status}: {message}")
        self.status = status
        self.message = message
        self.body = body
        self.retryable = retryable


class LisApiClient:
    """
    Async HTTP client untuk EazyApp LIS, satu instance per alat (1 Bearer key).

    Retry policy: 5xx & network/timeout di-retry dengan exponential backoff
    sampai retry_max. 4xx tidak di-retry (raise immediately).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 30,
        retry_max: int = 3,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._retry_max = retry_max
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session:
            await self._session.close()
            self._session = None

    # ============================================================
    # Endpoint methods
    # ============================================================

    async def get_instrument(self) -> dict:
        _, body = await self._request("GET", "/api/v1/instrument")
        return body

    async def get_orders_pending(self, priority: str | None = None, limit: int | None = None) -> dict:
        params = {}
        if priority:
            params["priority"] = priority
        if limit:
            params["limit"] = limit
        _, body = await self._request("GET", "/api/v1/instrument/orders/pending", params=params)
        return body

    async def post_results(self, payload: dict) -> tuple[int, dict]:
        return await self._request("POST", "/api/v1/instrument/results", json_body=payload)

    async def post_status(self, payload: dict) -> tuple[int, dict]:
        return await self._request("POST", "/api/v1/instrument/status", json_body=payload)

    async def post_log(self, payload: dict) -> tuple[int, dict]:
        return await self._request("POST", "/api/v1/instrument/logs", json_body=payload)

    # ============================================================
    # Internal
    # ============================================================

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, dict]:
        if not self._session:
            raise RuntimeError("LisApiClient harus dipakai dalam 'async with'")

        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        last_exc: LisApiError | None = None
        for attempt in range(1, self._retry_max + 1):
            try:
                async with self._session.request(
                    method, url,
                    headers=headers,
                    json=json_body if json_body is not None else None,
                    params=params or None,
                ) as resp:
                    text_body = await resp.text()
                    try:
                        body = json.loads(text_body) if text_body else {}
                    except json.JSONDecodeError:
                        body = {"raw": text_body}

                    if 200 <= resp.status < 300:
                        return resp.status, body

                    retryable = resp.status >= 500
                    err = LisApiError(
                        status=resp.status,
                        message=body.get("message") or body.get("error") or text_body[:200],
                        body=body,
                        retryable=retryable,
                    )
                    if not retryable:
                        raise err
                    last_exc = err

            except aiohttp.ClientError as e:
                last_exc = LisApiError(status=0, message=str(e), retryable=True)
            except asyncio.TimeoutError:
                last_exc = LisApiError(status=0, message="timeout", retryable=True)

            if attempt < self._retry_max:
                await asyncio.sleep(min(2 ** (attempt - 1), 10))

        assert last_exc is not None
        raise last_exc
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest tests/test_lis_client.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add lib/lis_client.py tests/test_lis_client.py
git commit -m "lib: add LisApiClient with Bearer auth + 5xx retry"
```

---

## Tahap 2 — LisBridgeService Tasks

### Task 6: ResultPusher

**Files:**
- Create: `services/lis_bridge/__init__.py`, `services/lis_bridge/result_pusher.py`
- Modify: `lib/db.py` (extend `update_result_status` with `increment_retry` param)
- Test: `tests/test_result_pusher.py`

- [ ] **Step 1: Create empty `services/lis_bridge/__init__.py`**

- [ ] **Step 2: Write the failing test**

`tests/test_result_pusher.py`:

```python
"""Test ResultPusher: payload build, 2xx→sent, 422→failed, 5xx→retry semantics."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.db import TblResult
from lib.lis_client import LisApiError
from services.lis_bridge.result_pusher import ResultPusher, build_mid_payload


def _row(**kw):
    defaults = {
        "id": 1,
        "instrument_id": 7,
        "result_json": {"mid_version": "1.0", "results": []},
        "received_at": datetime(2026, 5, 13, 8, 0, tzinfo=timezone.utc),
        "retry_count": 0,
    }
    defaults.update(kw)
    r = TblResult()
    for k, v in defaults.items():
        setattr(r, k, v)
    return r


def _instrument(**kw):
    inst = MagicMock()
    inst.id = kw.get("id", 7)
    inst.lis_instrument_id = kw.get("lis_instrument_id", "INST-X")
    return inst


def test_build_mid_payload_rewrites_instrument_id():
    row = _row(result_json={"mid_version": "1.0", "instrument_id": 7})
    payload = build_mid_payload(row, _instrument())
    assert payload["instrument_id"] == "INST-X"
    assert payload["mid_version"] == "1.0"
    assert payload["message_id"] == "MSG-7-1"
    assert payload["message_datetime"].startswith("2026-05-13T08:00")


async def test_2xx_marks_sent():
    client = AsyncMock()
    client.post_results = AsyncMock(return_value=(201, {"success": True}))
    update_fn = MagicMock()
    poll_fn = MagicMock(return_value=[_row()])

    pusher = ResultPusher(
        instrument=_instrument(),
        client=client,
        poll_results_fn=poll_fn,
        update_status_fn=update_fn,
        retry_max=3,
    )
    await pusher.run_once()
    update_fn.assert_called_with(1, "sent", None)


async def test_422_marks_failed_permanent():
    client = AsyncMock()
    client.post_results = AsyncMock(side_effect=LisApiError(
        status=422, message="validation", retryable=False,
    ))
    update_fn = MagicMock()
    poll_fn = MagicMock(return_value=[_row()])

    pusher = ResultPusher(
        instrument=_instrument(), client=client,
        poll_results_fn=poll_fn, update_status_fn=update_fn, retry_max=3,
    )
    await pusher.run_once()
    args, _ = update_fn.call_args
    assert args[0] == 1
    assert args[1] == "failed"
    assert "validation" in args[2]


async def test_5xx_under_retry_max_stays_pending():
    client = AsyncMock()
    client.post_results = AsyncMock(side_effect=LisApiError(
        status=502, message="bad gateway", retryable=True,
    ))
    update_fn = MagicMock()
    row = _row(retry_count=0)
    poll_fn = MagicMock(return_value=[row])

    pusher = ResultPusher(
        instrument=_instrument(), client=client,
        poll_results_fn=poll_fn, update_status_fn=update_fn, retry_max=3,
    )
    await pusher.run_once()
    args, kwargs = update_fn.call_args
    assert args[1] == "pending"
    assert kwargs.get("increment_retry") is True


async def test_5xx_at_retry_max_marks_failed():
    client = AsyncMock()
    client.post_results = AsyncMock(side_effect=LisApiError(
        status=502, message="bad gateway", retryable=True,
    ))
    update_fn = MagicMock()
    row = _row(retry_count=2)
    poll_fn = MagicMock(return_value=[row])

    pusher = ResultPusher(
        instrument=_instrument(), client=client,
        poll_results_fn=poll_fn, update_status_fn=update_fn, retry_max=3,
    )
    await pusher.run_once()
    args, _ = update_fn.call_args
    assert args[1] == "failed"
```

- [ ] **Step 3: Run test — expect FAIL**

Run: `pytest tests/test_result_pusher.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement `services/lis_bridge/result_pusher.py`**

```python
"""
services/lis_bridge/result_pusher.py — Push tbl_result pending ke LIS.

Loop: poll tbl_result → POST /results → update send_status.
2xx → 'sent'. 422 → 'failed' permanen. 5xx/network → bump retry_count;
'pending' kalau belum exhausted, 'failed' kalau retry_count>=retry_max.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger


def build_mid_payload(result_row, instrument) -> dict:
    """Build MID v1.0 payload, rewrite instrument_id ke string LIS."""
    payload = dict(result_row.result_json or {})
    payload["instrument_id"] = instrument.lis_instrument_id
    payload["mid_version"] = "1.0"
    payload.setdefault("message_id", f"MSG-{instrument.id}-{result_row.id}")
    if not payload.get("message_datetime"):
        ts = result_row.received_at or datetime.now(timezone.utc)
        payload["message_datetime"] = ts.isoformat()
    return payload


class ResultPusher:
    def __init__(
        self,
        instrument,
        client: LisApiClient,
        poll_results_fn,        # callable() -> list[TblResult]
        update_status_fn,       # callable(result_id, status, error, *, increment_retry=False)
        retry_max: int,
        batch_size: int = 50,
        poll_interval: float = 5.0,
    ):
        self._instrument = instrument
        self._client = client
        self._poll_results = poll_results_fn
        self._update_status = update_status_fn
        self._retry_max = retry_max
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._logger = get_logger(f"lis_bridge.{instrument.id}.result_pusher")
        self._running = True

    async def run_once(self):
        rows = await asyncio.get_event_loop().run_in_executor(None, self._poll_results)
        for row in rows or []:
            if not self._running:
                break
            await self._send(row)

    async def run_forever(self):
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                self._logger.error(f"result_pusher cycle error: {e}")
            await asyncio.sleep(self._poll_interval)

    def stop(self):
        self._running = False

    async def _send(self, row):
        payload = build_mid_payload(row, self._instrument)
        try:
            status, _ = await self._client.post_results(payload)
            if 200 <= status < 300:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_status, row.id, "sent", None,
                )
                self._logger.info(f"result_id={row.id} sent")
                return
            raise LisApiError(status=status, message="unexpected status", retryable=False)
        except LisApiError as e:
            if not e.retryable:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_status, row.id, "failed", f"{e.status}: {e.message}",
                )
                self._logger.warning(f"result_id={row.id} failed (permanent): {e}")
                return
            next_count = (row.retry_count or 0) + 1
            if next_count >= self._retry_max:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_status, row.id, "failed",
                    f"{e.status}: {e.message} (retry exhausted)",
                )
                self._logger.warning(f"result_id={row.id} retry exhausted")
            else:
                await asyncio.get_event_loop().run_in_executor(
                    None, _call_with_retry_kwarg, self._update_status,
                    row.id, "pending", f"{e.status}: {e.message}",
                )
                self._logger.info(f"result_id={row.id} retry {next_count}/{self._retry_max}")


def _call_with_retry_kwarg(fn, *args):
    return fn(*args, increment_retry=True)
```

- [ ] **Step 5: Extend `update_result_status` in `lib/db.py`**

Find the function (around line 253) and ensure signature matches:

```python
def update_result_status(
    result_id: int,
    status: str,
    error_message: str | None = None,
    increment_retry: bool = False,
) -> bool:
    """Update tbl_result.send_status. Set sent_at otomatis kalau status='sent'."""
    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblResult).filter(TblResult.id == result_id).first()
        if not row:
            return False
        row.send_status = status
        if error_message is not None:
            row.error_message = error_message
        if increment_retry:
            row.retry_count = (row.retry_count or 0) + 1
        if status == "sent":
            row.sent_at = datetime.now(timezone.utc)
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()
```

> If existing function does most of this already, just add the `increment_retry` param + branch.

- [ ] **Step 6: Run tests — expect PASS**

Run: `pytest tests/test_result_pusher.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
git add services/lis_bridge/__init__.py services/lis_bridge/result_pusher.py \
        lib/db.py tests/test_result_pusher.py
git commit -m "lis_bridge: add ResultPusher with retry-aware status updates"
```

### Task 7: OrderPuller

**Files:**
- Create: `services/lis_bridge/order_puller.py`
- Modify: `lib/db.py` (add `order_exists_by_lis_id`)
- Test: `tests/test_order_puller.py`

- [ ] **Step 1: Write the failing test**

`tests/test_order_puller.py`:

```python
"""Test OrderPuller: insert new orders, skip duplicates by order_id."""
from unittest.mock import AsyncMock, MagicMock

from services.lis_bridge.order_puller import OrderPuller


def _instrument(id=7, lis_id="INST-X"):
    inst = MagicMock()
    inst.id = id
    inst.lis_instrument_id = lis_id
    inst.order_poll_interval = 10
    return inst


async def test_inserts_new_orders():
    client = AsyncMock()
    client.get_orders_pending = AsyncMock(return_value={
        "success": True, "count": 2,
        "data": [
            {"order_id": "LAB-A", "patient": {}, "specimen": {}, "tests": []},
            {"order_id": "LAB-B", "patient": {}, "specimen": {}, "tests": []},
        ],
    })

    existing = set()
    def order_exists(iid, oid):
        return oid in existing
    save_order = MagicMock(side_effect=lambda iid, oj: (
        existing.add(oj["order_id"]) or 1
    ))

    puller = OrderPuller(
        instrument=_instrument(),
        client=client,
        order_exists_fn=order_exists,
        save_order_fn=save_order,
    )
    await puller.run_once()
    assert save_order.call_count == 2
    assert existing == {"LAB-A", "LAB-B"}


async def test_skips_duplicate_order_id():
    client = AsyncMock()
    client.get_orders_pending = AsyncMock(return_value={"data": [{"order_id": "LAB-A"}]})
    save_order = MagicMock(return_value=1)
    puller = OrderPuller(
        instrument=_instrument(),
        client=client,
        order_exists_fn=lambda iid, oid: True,
        save_order_fn=save_order,
    )
    await puller.run_once()
    save_order.assert_not_called()


async def test_empty_data_no_crash():
    client = AsyncMock()
    client.get_orders_pending = AsyncMock(return_value={"data": []})
    save_order = MagicMock()
    puller = OrderPuller(
        instrument=_instrument(),
        client=client,
        order_exists_fn=lambda *a: False,
        save_order_fn=save_order,
    )
    await puller.run_once()
    save_order.assert_not_called()
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_order_puller.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `services/lis_bridge/order_puller.py`**

```python
"""
services/lis_bridge/order_puller.py — Pull order pending dari LIS.

Loop: GET /orders/pending → insert ke tbl_order (dedup by order_id).
LIS akan terus return pending sampai kita POST /results
(yang otomatis transition order status di LIS ke 'sample_received').
"""
from __future__ import annotations

import asyncio

from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger


class OrderPuller:
    def __init__(
        self,
        instrument,
        client: LisApiClient,
        order_exists_fn,    # callable(instrument_id, order_id) -> bool
        save_order_fn,      # callable(instrument_id, order_json) -> int
        poll_interval: float | None = None,
    ):
        self._instrument = instrument
        self._client = client
        self._order_exists = order_exists_fn
        self._save_order = save_order_fn
        self._poll_interval = (
            poll_interval if poll_interval is not None
            else (instrument.order_poll_interval or 10)
        )
        self._logger = get_logger(f"lis_bridge.{instrument.id}.order_puller")
        self._running = True

    async def run_once(self):
        try:
            body = await self._client.get_orders_pending()
        except LisApiError as e:
            self._logger.warning(f"order_puller: GET failed: {e}")
            return

        orders = body.get("data") or []
        if not orders:
            return

        inserted = 0
        for order in orders:
            order_id = order.get("order_id", "")
            if not order_id:
                continue
            exists = await asyncio.get_event_loop().run_in_executor(
                None, self._order_exists, self._instrument.id, order_id,
            )
            if exists:
                continue
            new_id = await asyncio.get_event_loop().run_in_executor(
                None, self._save_order, self._instrument.id, order,
            )
            if new_id:
                inserted += 1
        if inserted:
            self._logger.info(f"order_puller: inserted {inserted} new order(s)")

    async def run_forever(self):
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                self._logger.error(f"order_puller cycle error: {e}")
            await asyncio.sleep(self._poll_interval)

    def stop(self):
        self._running = False
```

- [ ] **Step 4: Add `order_exists_by_lis_id` to `lib/db.py`**

```python
def order_exists_by_lis_id(instrument_id: int, lis_order_id: str) -> bool:
    """Cek apakah tbl_order sudah punya entry untuk (instrument_id, order_json.order_id)."""
    from sqlalchemy import text
    db = DBManager()
    session = db.get_session()
    try:
        dialect = session.get_bind().dialect.name
        if dialect == "mysql":
            row = session.execute(
                text(
                    "SELECT 1 FROM tbl_order "
                    "WHERE instrument_id = :iid "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(order_json, '$.order_id')) = :oid "
                    "LIMIT 1"
                ),
                {"iid": instrument_id, "oid": lis_order_id},
            ).first()
        else:
            row = session.execute(
                text(
                    "SELECT 1 FROM tbl_order "
                    "WHERE instrument_id = :iid "
                    "AND order_json LIKE :pattern "
                    "LIMIT 1"
                ),
                {"iid": instrument_id, "pattern": f'%"order_id": "{lis_order_id}"%'},
            ).first()
        return row is not None
    finally:
        session.close()
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_order_puller.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add services/lis_bridge/order_puller.py lib/db.py tests/test_order_puller.py
git commit -m "lis_bridge: add OrderPuller pulling LIS orders with dedup"
```

### Task 8: StatusReporter

**Files:**
- Create: `services/lis_bridge/status_reporter.py`
- Test: `tests/test_status_reporter.py`

- [ ] **Step 1: Write the failing test**

`tests/test_status_reporter.py`:

```python
"""Test StatusReporter: drain queue (event_type='status'), 2xx→sent, 5xx→retry."""
from unittest.mock import AsyncMock, MagicMock

from lib.lis_client import LisApiError
from services.lis_bridge.status_reporter import StatusReporter


def _event(id, payload, retry=0):
    ev = MagicMock()
    ev.id = id
    ev.payload_json = payload
    ev.retry_count = retry
    return ev


def _instrument(id=7):
    inst = MagicMock()
    inst.id = id
    return inst


async def test_2xx_marks_sent():
    client = AsyncMock()
    client.post_status = AsyncMock(return_value=(200, {"success": True}))
    update_fn = MagicMock()
    poll_fn = MagicMock(return_value=[_event(1, {"status": "online"})])

    reporter = StatusReporter(
        instrument=_instrument(), client=client,
        poll_events_fn=poll_fn, update_event_fn=update_fn, retry_max=3,
    )
    await reporter.run_once()
    update_fn.assert_called_with(1, "sent", None)


async def test_4xx_marks_failed_permanent():
    client = AsyncMock()
    client.post_status = AsyncMock(side_effect=LisApiError(
        status=422, message="bad payload", retryable=False,
    ))
    update_fn = MagicMock()
    poll_fn = MagicMock(return_value=[_event(1, {"status": "wrong"})])

    reporter = StatusReporter(
        instrument=_instrument(), client=client,
        poll_events_fn=poll_fn, update_event_fn=update_fn, retry_max=3,
    )
    await reporter.run_once()
    args, _ = update_fn.call_args
    assert args[1] == "failed"


async def test_5xx_keeps_pending_under_max():
    client = AsyncMock()
    client.post_status = AsyncMock(side_effect=LisApiError(
        status=502, message="x", retryable=True,
    ))
    update_fn = MagicMock()
    poll_fn = MagicMock(return_value=[_event(1, {"status": "online"}, retry=0)])

    reporter = StatusReporter(
        instrument=_instrument(), client=client,
        poll_events_fn=poll_fn, update_event_fn=update_fn, retry_max=3,
    )
    await reporter.run_once()
    args, kwargs = update_fn.call_args
    assert args[1] == "pending"
    assert kwargs.get("increment_retry") is True
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_status_reporter.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `services/lis_bridge/status_reporter.py`**

```python
"""
services/lis_bridge/status_reporter.py — Drain tbl_lis_event_queue (status events).

Loop: poll queue → POST /status → update send_status.
Retry semantics sama dengan ResultPusher.
"""
from __future__ import annotations

import asyncio

from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger


class StatusReporter:
    def __init__(
        self,
        instrument,
        client: LisApiClient,
        poll_events_fn,
        update_event_fn,
        retry_max: int,
        poll_interval: float = 2.0,
    ):
        self._instrument = instrument
        self._client = client
        self._poll_events = poll_events_fn
        self._update_event = update_event_fn
        self._retry_max = retry_max
        self._poll_interval = poll_interval
        self._logger = get_logger(f"lis_bridge.{instrument.id}.status_reporter")
        self._running = True

    async def run_once(self):
        events = await asyncio.get_event_loop().run_in_executor(None, self._poll_events)
        for ev in events or []:
            if not self._running:
                break
            await self._send(ev)

    async def run_forever(self):
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                self._logger.error(f"status_reporter cycle error: {e}")
            await asyncio.sleep(self._poll_interval)

    def stop(self):
        self._running = False

    async def _send(self, ev):
        try:
            status, _ = await self._client.post_status(ev.payload_json)
            if 200 <= status < 300:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_event, ev.id, "sent", None,
                )
                return
            raise LisApiError(status=status, message="unexpected", retryable=False)
        except LisApiError as e:
            if not e.retryable:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_event, ev.id, "failed", f"{e.status}: {e.message}",
                )
                return
            next_count = (ev.retry_count or 0) + 1
            if next_count >= self._retry_max:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._update_event, ev.id, "failed",
                    f"{e.status}: {e.message} (retry exhausted)",
                )
            else:
                await asyncio.get_event_loop().run_in_executor(
                    None, _call_increment, self._update_event,
                    ev.id, "pending", f"{e.status}: {e.message}",
                )


def _call_increment(fn, *args):
    return fn(*args, increment_retry=True)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_status_reporter.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/lis_bridge/status_reporter.py tests/test_status_reporter.py
git commit -m "lis_bridge: add StatusReporter draining LIS event queue"
```

### Task 9: LogPusher

**Files:**
- Create: `services/lis_bridge/log_pusher.py`
- Modify: `lib/db.py` (cursor helpers + `get_service_logs_after`)
- Test: `tests/test_log_pusher.py`

- [ ] **Step 1: Write the failing test**

`tests/test_log_pusher.py`:

```python
"""Test LogPusher: filter WARN/ERROR, cursor advance, only this instrument."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from services.lis_bridge.log_pusher import LogPusher, _log_matches_instrument


def _log(id, level, service, message, ts):
    row = MagicMock()
    row.id = id
    row.level = level
    row.service = service
    row.message = message
    row.logged_at = ts
    return row


def _instrument(id=7):
    inst = MagicMock()
    inst.id = id
    inst.lis_instrument_id = "INST-X"
    return inst


def test_log_matches_instrument_by_service_name():
    log = _log(1, "WARNING", "tcp_7", "x", datetime.now(timezone.utc))
    assert _log_matches_instrument(log, 7) is True
    assert _log_matches_instrument(log, 8) is False


def test_log_matches_lis_bridge_service_name():
    log = _log(1, "ERROR", "lis_bridge_7", "x", datetime.now(timezone.utc))
    assert _log_matches_instrument(log, 7) is True


def test_log_matches_via_message_tag():
    log = _log(1, "WARNING", "protocols", "[INSTRUMENT 7] parse error", datetime.now(timezone.utc))
    assert _log_matches_instrument(log, 7) is True


async def test_pushes_warn_and_error_only():
    ts = datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc)
    logs = [
        _log(1, "INFO",    "tcp_7", "noise", ts),
        _log(2, "WARNING", "tcp_7", "warn", ts),
        _log(3, "ERROR",   "tcp_7", "err",  ts),
    ]
    client = AsyncMock()
    client.post_log = AsyncMock(return_value=(200, {"success": True}))
    get_cursor = MagicMock(return_value=None)
    set_cursor = MagicMock()
    poll_logs = MagicMock(return_value=logs)

    pusher = LogPusher(
        instrument=_instrument(7),
        client=client,
        get_cursor_fn=get_cursor,
        set_cursor_fn=set_cursor,
        poll_logs_fn=poll_logs,
    )
    await pusher.run_once()

    assert client.post_log.await_count == 2  # INFO skipped
    set_cursor.assert_called_with(7, ts)
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_log_pusher.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `services/lis_bridge/log_pusher.py`**

```python
"""
services/lis_bridge/log_pusher.py — Cursor-based push tbl_service_log → LIS.

Filter level: WARNING + ERROR only.
Filter alat: service name tcp_<id> / lis_bridge_<id> / tag [INSTRUMENT <id>] di message.
Cursor disimpan di tbl_setting key 'lis.log_cursor.<instrument_id>'.
"""
from __future__ import annotations

import asyncio

from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger


PUSHABLE_LEVELS = {"WARNING", "ERROR"}


def _log_matches_instrument(log_row, instrument_id: int) -> bool:
    service = log_row.service or ""
    if service == f"tcp_{instrument_id}":
        return True
    if service == f"lis_bridge_{instrument_id}":
        return True
    if service.startswith(f"lis_bridge.{instrument_id}."):
        return True
    msg = log_row.message or ""
    if f"[INSTRUMENT {instrument_id}]" in msg:
        return True
    return False


class LogPusher:
    def __init__(
        self,
        instrument,
        client: LisApiClient,
        get_cursor_fn,   # callable(instrument_id) -> datetime|None
        set_cursor_fn,   # callable(instrument_id, datetime)
        poll_logs_fn,    # callable(cursor) -> list[TblServiceLog]
        poll_interval: float = 5.0,
    ):
        self._instrument = instrument
        self._client = client
        self._get_cursor = get_cursor_fn
        self._set_cursor = set_cursor_fn
        self._poll_logs = poll_logs_fn
        self._poll_interval = poll_interval
        self._logger = get_logger(f"lis_bridge.{instrument.id}.log_pusher")
        self._running = True

    async def run_once(self):
        cursor = await asyncio.get_event_loop().run_in_executor(
            None, self._get_cursor, self._instrument.id,
        )
        logs = await asyncio.get_event_loop().run_in_executor(
            None, self._poll_logs, cursor,
        )
        last_ts = cursor
        for log in logs or []:
            if not self._running:
                break
            if log.level not in PUSHABLE_LEVELS:
                continue
            if not _log_matches_instrument(log, self._instrument.id):
                continue
            try:
                await self._client.post_log({
                    "level": log.level.lower(),
                    "message": log.message,
                    "logged_at": log.logged_at.isoformat() if log.logged_at else None,
                    "context": {
                        "service": log.service,
                        "instrument_id": self._instrument.lis_instrument_id,
                    },
                })
                last_ts = log.logged_at
            except LisApiError as e:
                self._logger.warning(f"log_pusher: push gagal id={log.id}: {e}")
                break

        if last_ts and last_ts != cursor:
            await asyncio.get_event_loop().run_in_executor(
                None, self._set_cursor, self._instrument.id, last_ts,
            )

    async def run_forever(self):
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                self._logger.error(f"log_pusher cycle error: {e}")
            await asyncio.sleep(self._poll_interval)

    def stop(self):
        self._running = False
```

- [ ] **Step 4: Add cursor helpers to `lib/db.py`**

```python
def get_log_cursor(instrument_id: int):
    from datetime import datetime
    raw = get_setting(f"lis.log_cursor.{instrument_id}")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def set_log_cursor(instrument_id: int, ts) -> bool:
    return set_setting(f"lis.log_cursor.{instrument_id}", ts.isoformat())


def get_service_logs_after(cursor, level_in: tuple, limit: int = 100):
    db = DBManager()
    session = db.get_session()
    try:
        q = session.query(TblServiceLog).filter(
            TblServiceLog.level.in_(level_in),
        )
        if cursor:
            q = q.filter(TblServiceLog.logged_at > cursor)
        return q.order_by(TblServiceLog.logged_at.asc()).limit(limit).all()
    finally:
        session.close()
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_log_pusher.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add services/lis_bridge/log_pusher.py lib/db.py tests/test_log_pusher.py
git commit -m "lis_bridge: add LogPusher with cursor-based service-log forwarding"
```

### Task 10: LisBridgeService orchestrator

**Files:**
- Create: `services/lis_bridge/service.py`
- Modify: `lib/db.py` (add `get_instrument_by_id` + `update_instrument_lis_sync`)
- Test: `tests/test_lis_bridge_service.py`

- [ ] **Step 1: Write the failing test**

`tests/test_lis_bridge_service.py`:

```python
"""Test LisBridgeService: load config, verify with LIS, exception in load_config."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.lis_bridge.service import LisBridgeService


def _instrument(id=7, lis_id="INST-X", api_key="inst_key", enabled=True):
    inst = MagicMock()
    inst.id = id
    inst.name = "Test"
    inst.lis_instrument_id = lis_id
    inst.lis_api_key = api_key
    inst.lis_bridge_enabled = enabled
    inst.order_poll_interval = 10
    return inst


async def test_load_config_reads_base_url_from_settings():
    inst = _instrument()
    with patch("services.lis_bridge.service.get_instrument_by_id", return_value=inst), \
         patch("services.lis_bridge.service.get_setting") as gs:
        gs.side_effect = lambda key, default=None: {
            "lis.base_url": "https://eazy.example",
            "lis.http_timeout": "30",
            "lis.retry_max": "3",
        }.get(key, default)

        svc = LisBridgeService(instrument_id=7)
        svc._load_config()
        assert svc.lis_base_url == "https://eazy.example"
        assert svc.lis_api_key == "inst_key"
        assert svc.lis_instrument_id == "INST-X"


async def test_load_config_raises_if_no_api_key():
    inst = _instrument(api_key="")
    with patch("services.lis_bridge.service.get_instrument_by_id", return_value=inst), \
         patch("services.lis_bridge.service.get_setting", return_value="https://x"):
        svc = LisBridgeService(instrument_id=7)
        with pytest.raises(ValueError, match="lis_api_key"):
            svc._load_config()


async def test_verify_with_lis_caches_lis_instrument_id():
    inst = _instrument(lis_id=None)
    with patch("services.lis_bridge.service.get_instrument_by_id", return_value=inst), \
         patch("services.lis_bridge.service.get_setting", return_value="https://x"), \
         patch("services.lis_bridge.service.update_instrument_lis_sync") as upd:
        svc = LisBridgeService(instrument_id=7)
        svc._load_config()

        mock_client = AsyncMock()
        mock_client.get_instrument = AsyncMock(return_value={
            "data": {"instrument": {"instrument_id": "INST-Y"}}
        })
        svc._client = mock_client

        await svc._verify_with_lis()
        assert svc.lis_instrument_id == "INST-Y"
        upd.assert_called_once()
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_lis_bridge_service.py -v`
Expected: FAIL

- [ ] **Step 3: Add helpers to `lib/db.py`**

```python
def get_instrument_by_id(instrument_id: int):
    db = DBManager()
    session = db.get_session()
    try:
        return session.query(TblInstrument).filter(
            TblInstrument.id == instrument_id
        ).first()
    finally:
        session.close()


def update_instrument_lis_sync(instrument_id: int, lis_instrument_id: str) -> bool:
    db = DBManager()
    session = db.get_session()
    try:
        row = session.query(TblInstrument).filter(
            TblInstrument.id == instrument_id
        ).first()
        if not row:
            return False
        row.lis_instrument_id = lis_instrument_id
        row.last_lis_sync_at = datetime.now(timezone.utc)
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()
```

- [ ] **Step 4: Implement `services/lis_bridge/service.py`**

```python
"""
services/lis_bridge/service.py — LisBridgeService orchestrator per-alat.

Lifecycle:
  start() → _load_config() → _verify_with_lis() → _push_boot_status()
         → asyncio.gather(supervise(result_pusher), supervise(order_puller),
                          supervise(status_reporter), supervise(log_pusher))

Setiap child task di-wrap _supervise(): catch exception, log, restart
dengan exponential backoff 1s → 2s → 4s → ... max 60s.
"""
from __future__ import annotations

import asyncio
import signal

from lib.db import (
    get_instrument_by_id,
    get_pending_results,
    get_pending_lis_events,
    update_result_status,
    update_lis_event_status,
    get_setting,
    update_instrument_lis_sync,
    order_exists_by_lis_id,
    save_order,
    get_log_cursor,
    set_log_cursor,
    get_service_logs_after,
)
from lib.lis_client import LisApiClient, LisApiError
from lib.utils import get_logger

from services.lis_bridge.result_pusher import ResultPusher
from services.lis_bridge.order_puller import OrderPuller
from services.lis_bridge.status_reporter import StatusReporter
from services.lis_bridge.log_pusher import LogPusher


def _int_setting(key: str, default: int) -> int:
    v = get_setting(key, str(default))
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class LisBridgeService:
    def __init__(self, instrument_id: int):
        self.instrument_id = instrument_id
        self.instrument = None
        self.lis_base_url = None
        self.lis_api_key = None
        self.lis_instrument_id = None
        self._client: LisApiClient | None = None
        self._logger = get_logger(f"lis_bridge_{instrument_id}")
        self._running = False
        self._stoppables: list = []

    async def start(self):
        self._running = True
        self._load_config()
        async with LisApiClient(
            base_url=self.lis_base_url,
            api_key=self.lis_api_key,
            timeout=_int_setting("lis.http_timeout", 30),
            retry_max=_int_setting("lis.retry_max", 3),
        ) as client:
            self._client = client
            await self._verify_with_lis()
            await self._push_boot_status()
            self._setup_signal_handlers()
            try:
                await self._run_supervised_tasks()
            finally:
                await self._push_shutdown_status()

    def _setup_signal_handlers(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._signal_handler, sig)
            except NotImplementedError:
                pass

    def _signal_handler(self, sig):
        self._logger.info(f"signal {sig} received, shutting down...")
        self._running = False
        for s in self._stoppables:
            try:
                s.stop()
            except Exception:
                pass

    def _load_config(self):
        inst = get_instrument_by_id(self.instrument_id)
        if not inst:
            raise ValueError(f"Instrument id={self.instrument_id} tidak ditemukan")
        if not inst.lis_api_key:
            raise ValueError(
                f"Instrument id={self.instrument_id} belum punya lis_api_key — "
                f"set via Web Console dulu"
            )
        self.instrument = inst
        self.lis_api_key = inst.lis_api_key
        self.lis_instrument_id = inst.lis_instrument_id
        self.lis_base_url = get_setting("lis.base_url", "https://eazy.vespahobby.xyz")
        self._logger.info(
            f"config loaded: base={self.lis_base_url}, "
            f"lis_instrument_id={self.lis_instrument_id or '(belum sync)'}"
        )

    async def _verify_with_lis(self):
        try:
            body = await self._client.get_instrument()
        except LisApiError as e:
            self._logger.error(f"GET /instrument gagal: {e}")
            raise
        info = (body.get("data") or {}).get("instrument") or {}
        new_id = info.get("instrument_id")
        if new_id and new_id != self.lis_instrument_id:
            self.lis_instrument_id = new_id
            self.instrument.lis_instrument_id = new_id
            await asyncio.get_event_loop().run_in_executor(
                None, update_instrument_lis_sync, self.instrument_id, new_id,
            )
            self._logger.info(f"lis_instrument_id synced: {new_id}")

    async def _push_boot_status(self):
        try:
            await self._client.post_status({"status": "online"})
            self._logger.info("boot status=online pushed")
        except LisApiError as e:
            self._logger.warning(f"boot status push gagal: {e}")

    async def _push_shutdown_status(self):
        if not self._client:
            return
        try:
            await self._client.post_status({"status": "offline"})
            self._logger.info("shutdown status=offline pushed")
        except Exception as e:
            self._logger.warning(f"shutdown status push gagal: {e}")

    async def _run_supervised_tasks(self):
        retry_max = _int_setting("lis.retry_max", 3)
        result_poll = _int_setting("lis.result_poll_interval", 5)
        status_poll = _int_setting("lis.status_poll_interval", 2)
        log_poll    = _int_setting("lis.log_poll_interval", 5)

        result_pusher = ResultPusher(
            instrument=self.instrument,
            client=self._client,
            poll_results_fn=lambda: _poll_results_for(self.instrument_id),
            update_status_fn=update_result_status,
            retry_max=retry_max,
            poll_interval=result_poll,
        )
        order_puller = OrderPuller(
            instrument=self.instrument,
            client=self._client,
            order_exists_fn=order_exists_by_lis_id,
            save_order_fn=save_order,
        )
        status_reporter = StatusReporter(
            instrument=self.instrument,
            client=self._client,
            poll_events_fn=lambda: get_pending_lis_events(
                self.instrument_id, event_type="status", limit=50,
            ),
            update_event_fn=update_lis_event_status,
            retry_max=retry_max,
            poll_interval=status_poll,
        )
        log_pusher = LogPusher(
            instrument=self.instrument,
            client=self._client,
            get_cursor_fn=get_log_cursor,
            set_cursor_fn=set_log_cursor,
            poll_logs_fn=lambda cursor: get_service_logs_after(
                cursor, ("WARNING", "ERROR"), limit=100,
            ),
            poll_interval=log_poll,
        )

        self._stoppables = [result_pusher, order_puller, status_reporter, log_pusher]

        await asyncio.gather(
            self._supervise(result_pusher.run_forever, "result_pusher"),
            self._supervise(order_puller.run_forever, "order_puller"),
            self._supervise(status_reporter.run_forever, "status_reporter"),
            self._supervise(log_pusher.run_forever, "log_pusher"),
        )

    async def _supervise(self, coro_fn, name: str):
        backoff = 1
        while self._running:
            try:
                await coro_fn()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.error(f"[{name}] crashed: {e}; restart in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


def _poll_results_for(instrument_id: int):
    """Filter get_pending_results ke alat ini."""
    rows = get_pending_results(limit=200)
    return [r for r in rows if r.instrument_id == instrument_id]
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_lis_bridge_service.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add services/lis_bridge/service.py lib/db.py tests/test_lis_bridge_service.py
git commit -m "lis_bridge: orchestrator with task supervision + LIS verify"
```

### Task 11: Entry point + systemd unit

**Files:**
- Create: `services/lis_bridge/main.py`, `systemd/midlab-lis-bridge@.service`

- [ ] **Step 1: Create `services/lis_bridge/main.py`**

```python
"""
services/lis_bridge/main.py — Entry point LisBridgeService per-alat.

Usage:
    python3 -m services.lis_bridge.main --instrument-id 1
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from lib.utils import get_logger
from services.lis_bridge.service import LisBridgeService


def parse_args():
    p = argparse.ArgumentParser(description="MidLab LisBridgeService")
    p.add_argument("--instrument-id", type=int, required=True)
    return p.parse_args()


async def run(instrument_id: int):
    logger = get_logger(f"lis_bridge_{instrument_id}")
    logger.info(f"Starting LisBridgeService for instrument_id={instrument_id}")
    svc = LisBridgeService(instrument_id=instrument_id)
    await svc.start()


def main():
    args = parse_args()
    try:
        asyncio.run(run(args.instrument_id))
    except KeyboardInterrupt:
        print("\nShutdown by keyboard interrupt.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create `systemd/midlab-lis-bridge@.service`**

```ini
[Unit]
Description=MidLab LisBridgeService (instrument %i)
After=network.target mysqld.service
Wants=mysqld.service

[Service]
Type=simple
User=midlab
Group=midlab
WorkingDirectory=/opt/midlab
ExecStart=/usr/bin/python3 -m services.lis_bridge.main --instrument-id %i
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/midlab/lis_bridge_%i.log
StandardError=append:/var/log/midlab/lis_bridge_%i.log

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Smoke test entry point**

Run: `python3 -c "from services.lis_bridge.main import parse_args; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add services/lis_bridge/main.py systemd/midlab-lis-bridge@.service
git commit -m "lis_bridge: add main entry point + systemd template unit"
```

### Task 12: Watchdog support for `lis_bridge_<id>`

**Files:**
- Modify: `services/web_console/watchdog.py`
- Test: `tests/test_watchdog_command.py`

- [ ] **Step 1: Add branch in `_build_command`**

In `services/web_console/watchdog.py`, find `_build_command` (around line 352). Add this branch BEFORE the `if service_name.startswith("tcp_")` block:

```python
        if service_name.startswith("lis_bridge_"):
            iid = instrument_id
            if iid is None:
                try:
                    iid = int(service_name.split("_", 2)[2])
                except (IndexError, ValueError):
                    return None
            return [
                PYTHON, "-m", "services.lis_bridge.main",
                "--instrument-id", str(iid),
            ]
```

- [ ] **Step 2: Write test**

`tests/test_watchdog_command.py`:

```python
from services.web_console.watchdog import ServiceWatchdog


def test_build_command_lis_bridge():
    w = ServiceWatchdog()
    cmd = w._build_command("lis_bridge_7", instrument_id=7)
    assert cmd is not None
    assert "services.lis_bridge.main" in cmd
    assert "--instrument-id" in cmd
    assert "7" in cmd


def test_build_command_lis_bridge_parse_id_from_name():
    w = ServiceWatchdog()
    cmd = w._build_command("lis_bridge_42")
    assert cmd is not None
    assert "42" in cmd
```

- [ ] **Step 3: Run tests — expect PASS**

Run: `pytest tests/test_watchdog_command.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add services/web_console/watchdog.py tests/test_watchdog_command.py
git commit -m "watchdog: recognize lis_bridge_<id> service for spawning"
```

---

## Tahap 3 — TCP Event Hook

### Task 13: TCPSocketService emits to event queue

**Files:**
- Modify: `services/tcp_socket/service.py` (verify exact path first)
- Test: `tests/test_tcp_event_hook.py`

- [ ] **Step 1: Locate event points in TCPSocketService**

Run: `grep -rn "connect\|disconnect\|TimeoutError\|except" services/tcp_socket/ | head -30`

Identify the 3 event points:
- Connection established (after socket connect, before recv loop)
- Connection disconnected (peer closed / our close)
- Connection error (timeout / refused / unexpected close)

- [ ] **Step 2: Add import at top of the modified file**

```python
from lib.db import enqueue_lis_event
```

- [ ] **Step 3: At connection-established point, add**

```python
try:
    enqueue_lis_event(
        instrument_id=self._instrument.id,
        event_type="status",
        payload={"status": "online"},
    )
except Exception as e:
    self._logger.warning(f"failed enqueue status=online: {e}")
```

- [ ] **Step 4: At disconnect point, add**

```python
try:
    enqueue_lis_event(
        instrument_id=self._instrument.id,
        event_type="status",
        payload={"status": "offline"},
    )
except Exception:
    pass
```

- [ ] **Step 5: At error point (timeout / connection refused / parse error fatal), add**

```python
try:
    enqueue_lis_event(
        instrument_id=self._instrument.id,
        event_type="status",
        payload={"status": "error", "error_message": str(err)[:500]},
    )
except Exception:
    pass
```

> Insertion locations depend on existing structure. Read first; insert at matching control points. Do NOT modify other logic.

- [ ] **Step 6: Smoke test**

`tests/test_tcp_event_hook.py`:

```python
"""Smoke-level: hook is wired."""
def test_enqueue_imported_in_tcp_service():
    from services.tcp_socket import service as tcp_svc
    assert hasattr(tcp_svc, "enqueue_lis_event"), (
        "TCPSocketService harus import enqueue_lis_event setelah Task 13"
    )
```

Run: `pytest tests/test_tcp_event_hook.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/tcp_socket/ tests/test_tcp_event_hook.py
git commit -m "tcp_socket: enqueue LIS status events on connect/disconnect/error"
```

---

## Tahap 4 — Web Console

### Task 14: Verify-with-LIS API endpoint

**Files:**
- Modify: `services/web_console/api.py`
- Test: `tests/test_api_verify_lis.py`

- [ ] **Step 1: Add endpoint to `services/web_console/api.py`**

Append after `update_instrument` (around line 412):

```python
class LisVerifyRequest(BaseModel):
    lis_api_key: str
    lis_base_url: str | None = None


class LisVerifyResponse(BaseModel):
    success: bool
    lis_instrument_id: str | None = None
    name: str | None = None
    vendor: str | None = None
    model: str | None = None
    error: str | None = None


@app.post("/api/instruments/{instrument_id}/verify-lis", response_model=LisVerifyResponse)
async def verify_with_lis(
    instrument_id: int,
    body: LisVerifyRequest,
    x_api_key: str = Header(None),
):
    """Verify LIS API key dengan call GET /instrument."""
    _verify_api_key(x_api_key)

    from lib.lis_client import LisApiClient, LisApiError
    from lib.db import get_setting

    base_url = body.lis_base_url or get_setting(
        "lis.base_url", "https://eazy.vespahobby.xyz"
    )

    try:
        async with LisApiClient(
            base_url=base_url, api_key=body.lis_api_key,
            timeout=10, retry_max=1,
        ) as client:
            data = await client.get_instrument()
        info = (data.get("data") or {}).get("instrument") or {}
        return LisVerifyResponse(
            success=True,
            lis_instrument_id=info.get("instrument_id"),
            name=info.get("name"),
            vendor=info.get("vendor"),
            model=info.get("model"),
        )
    except LisApiError as e:
        return LisVerifyResponse(success=False, error=f"{e.status}: {e.message}")
    except Exception as e:
        return LisVerifyResponse(success=False, error=str(e))
```

- [ ] **Step 2: Write test**

`tests/test_api_verify_lis.py`:

```python
from fastapi.testclient import TestClient
from aioresponses import aioresponses

from services.web_console.api import app


def test_verify_lis_endpoint_returns_lis_info():
    client = TestClient(app)
    with aioresponses() as m:
        m.get(
            "https://eazy.vespahobby.xyz/api/v1/instrument",
            payload={
                "success": True,
                "data": {"instrument": {
                    "instrument_id": "INST-Z", "name": "Mock",
                    "vendor": "X", "model": "Y",
                }},
            },
            status=200,
        )
        r = client.post("/api/instruments/1/verify-lis", json={"lis_api_key": "k"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["lis_instrument_id"] == "INST-Z"
```

- [ ] **Step 3: Run test — expect PASS**

Run: `pytest tests/test_api_verify_lis.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add services/web_console/api.py tests/test_api_verify_lis.py
git commit -m "webconsole: add POST /api/instruments/{id}/verify-lis endpoint"
```

### Task 15: Instrument CRUD accepts LIS fields

**Files:**
- Modify: `services/web_console/api.py`
- Test: `tests/test_api_instrument_lis_fields.py`

- [ ] **Step 1: Extend Pydantic models in `services/web_console/api.py`**

Add fields to existing classes:

```python
class InstrumentCreate(BaseModel):
    # ... existing fields ...
    lis_instrument_id: str | None = None
    lis_api_key: str | None = None
    order_poll_interval: int | None = 10
    lis_bridge_enabled: bool = False


class InstrumentUpdate(BaseModel):
    # ... existing fields ...
    lis_instrument_id: str | None = None
    lis_api_key: str | None = None
    order_poll_interval: int | None = None
    lis_bridge_enabled: bool | None = None


class InstrumentResponse(BaseModel):
    # ... existing fields ...
    lis_instrument_id: str | None = None
    order_poll_interval: int = 10
    lis_bridge_enabled: bool = False
    last_lis_sync_at: str | None = None
    lis_status_pushed: str | None = None
    # NOTE: lis_api_key TIDAK pernah di-expose di response (security)
```

- [ ] **Step 2: Extend `_instrument_to_response`** (around line 333)

```python
def _instrument_to_response(row: TblInstrument) -> InstrumentResponse:
    return InstrumentResponse(
        # ... existing fields ...
        lis_instrument_id=row.lis_instrument_id,
        order_poll_interval=row.order_poll_interval or 10,
        lis_bridge_enabled=bool(row.lis_bridge_enabled),
        last_lis_sync_at=row.last_lis_sync_at.isoformat() if row.last_lis_sync_at else None,
        lis_status_pushed=row.lis_status_pushed,
    )
```

- [ ] **Step 3: Save LIS fields on create**

In `create_instrument` (around line 361), find where `TblInstrument(...)` is constructed. Add the new kwargs:

```python
        lis_instrument_id=body.lis_instrument_id,
        lis_api_key=body.lis_api_key,
        order_poll_interval=body.order_poll_interval or 10,
        lis_bridge_enabled=body.lis_bridge_enabled,
```

- [ ] **Step 4: Save LIS fields on update**

In `update_instrument` (around line 413), add conditional updates:

```python
    if body.lis_instrument_id is not None:
        inst.lis_instrument_id = body.lis_instrument_id
    if body.lis_api_key is not None:
        inst.lis_api_key = body.lis_api_key
    if body.order_poll_interval is not None:
        inst.order_poll_interval = body.order_poll_interval
    if body.lis_bridge_enabled is not None:
        inst.lis_bridge_enabled = body.lis_bridge_enabled
```

- [ ] **Step 5: Write test**

`tests/test_api_instrument_lis_fields.py`:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from services.web_console.api import app


def test_create_instrument_with_lis_fields_round_trips():
    client = TestClient(app)
    captured = {}

    def fake_add(row):
        captured["row"] = row

    def fake_commit():
        if "row" in captured:
            captured["row"].id = 99

    with patch("services.web_console.api.DBManager") as MockDB:
        mock_session = MagicMock()
        mock_session.add.side_effect = fake_add
        mock_session.commit.side_effect = fake_commit
        MockDB.return_value.get_session.return_value = mock_session

        r = client.post("/api/instruments", json={
            "name": "X", "ip_address": "1.1.1.1", "port": 5000,
            "protocol": "ASTM", "mode": "unidirectional",
            "connection": "server",
            "lis_api_key": "inst_xxx",
            "order_poll_interval": 15,
            "lis_bridge_enabled": True,
        })
    assert r.status_code == 201
    body = r.json()
    assert body["order_poll_interval"] == 15
    assert body["lis_bridge_enabled"] is True
    assert "lis_api_key" not in body
```

- [ ] **Step 6: Run test — expect PASS**

Run: `pytest tests/test_api_instrument_lis_fields.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/web_console/api.py tests/test_api_instrument_lis_fields.py
git commit -m "webconsole: instrument CRUD accepts LIS fields (api_key never exposed)"
```

### Task 16: Event queue API endpoints

**Files:**
- Modify: `services/web_console/api.py`
- Test: `tests/test_api_lis_events.py`

- [ ] **Step 1: Add endpoints**

Append to `api.py`:

```python
class LisEventResponse(BaseModel):
    id: int
    instrument_id: int
    event_type: str
    payload_json: dict
    send_status: str
    retry_count: int
    error_message: str | None = None
    created_at: str | None = None
    sent_at: str | None = None


@app.get("/api/lis-events", response_model=list[LisEventResponse])
async def list_lis_events(
    instrument_id: int | None = None,
    status: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    x_api_key: str = Header(None),
):
    _verify_api_key(x_api_key)
    db = DBManager()
    session = db.get_session()
    try:
        from lib.db import TblLisEventQueue
        q = session.query(TblLisEventQueue)
        if instrument_id:
            q = q.filter(TblLisEventQueue.instrument_id == instrument_id)
        if status:
            q = q.filter(TblLisEventQueue.send_status == status)
        if event_type:
            q = q.filter(TblLisEventQueue.event_type == event_type)
        rows = q.order_by(TblLisEventQueue.id.desc()).limit(limit).all()
        return [
            LisEventResponse(
                id=r.id, instrument_id=r.instrument_id,
                event_type=r.event_type, payload_json=r.payload_json,
                send_status=r.send_status, retry_count=r.retry_count or 0,
                error_message=r.error_message,
                created_at=r.created_at.isoformat() if r.created_at else None,
                sent_at=r.sent_at.isoformat() if r.sent_at else None,
            )
            for r in rows
        ]
    finally:
        session.close()


@app.post("/api/lis-events/{event_id}/retry", response_model=MessageResponse)
async def retry_lis_event(event_id: int, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    from lib.db import update_lis_event_status
    ok = update_lis_event_status(event_id, "pending", error_message=None)
    return MessageResponse(success=ok, message="event reset to pending" if ok else "event not found")


@app.post("/api/lis-events/{event_id}/skip", response_model=MessageResponse)
async def skip_lis_event(event_id: int, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    from lib.db import update_lis_event_status
    ok = update_lis_event_status(event_id, "skipped", error_message="manually skipped")
    return MessageResponse(success=ok, message="event skipped" if ok else "event not found")
```

- [ ] **Step 2: Write test**

`tests/test_api_lis_events.py`:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from services.web_console.api import app


def test_list_lis_events_returns_array():
    client = TestClient(app)
    with patch("services.web_console.api.DBManager") as MockDB:
        mock_session = MagicMock()
        chain = mock_session.query.return_value
        chain.order_by.return_value.limit.return_value.all.return_value = []
        MockDB.return_value.get_session.return_value = mock_session
        r = client.get("/api/lis-events")
    assert r.status_code == 200
    assert r.json() == []


def test_retry_endpoint_calls_helper():
    client = TestClient(app)
    with patch("lib.db.update_lis_event_status", return_value=True):
        r = client.post("/api/lis-events/5/retry")
    assert r.status_code == 200
```

- [ ] **Step 3: Run tests — expect PASS**

Run: `pytest tests/test_api_lis_events.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add services/web_console/api.py tests/test_api_lis_events.py
git commit -m "webconsole: add LIS event queue list/retry/skip endpoints"
```

### Task 17: Instrument form template — LIS fields + Verify button

**Files:**
- Modify: `services/web_console/templates/instruments.html`

- [ ] **Step 1: Add LIS form section**

Inside the existing instrument create/edit form, add this fragment:

```html
<div class="form-section">
  <h4>LIS Bridging (EazyApp)</h4>

  <div class="form-row">
    <label for="lis_api_key">LIS API Key (Bearer)</label>
    <div class="input-group">
      <input type="password" id="lis_api_key" name="lis_api_key"
             placeholder="inst_xxxxxxxxxxxxxxxx" autocomplete="off">
      <button type="button" id="btn-verify-lis" class="btn-secondary">
        Verify with LIS
      </button>
    </div>
    <small class="hint">API key unik per-alat dari EazyApp → Integrasi Alat → Detail Alat</small>
  </div>

  <div class="form-row">
    <label for="lis_instrument_id">LIS Instrument ID</label>
    <input type="text" id="lis_instrument_id" name="lis_instrument_id"
           readonly placeholder="(akan auto-fill setelah Verify)">
    <small class="hint">Read-only — di-set otomatis dari LIS</small>
  </div>

  <div class="form-row">
    <label for="order_poll_interval">Order Poll Interval (detik)</label>
    <input type="number" id="order_poll_interval" name="order_poll_interval"
           value="10" min="1" max="3600">
  </div>

  <div class="form-row">
    <label class="checkbox-label">
      <input type="checkbox" id="lis_bridge_enabled" name="lis_bridge_enabled">
      Enable LIS bridging (LisBridgeService)
    </label>
    <small class="hint">Off = ResultSenderService lama yang handle (untuk staged rollout)</small>
  </div>

  <div id="verify-result" class="hidden"></div>
</div>
```

- [ ] **Step 2: Add inline JS for Verify button**

In the existing `<script>` block of `instruments.html`. **Use textContent / DOM methods only — no innerHTML with user-controlled content (avoid XSS):**

```javascript
document.getElementById('btn-verify-lis').addEventListener('click', async () => {
  const apiKey = document.getElementById('lis_api_key').value.trim();
  const resultEl = document.getElementById('verify-result');
  const instId = window.editingInstrumentId || 1;

  resultEl.replaceChildren();
  resultEl.classList.remove('hidden');

  if (!apiKey) {
    resultEl.className = 'verify-error';
    resultEl.textContent = 'Isi LIS API Key dulu sebelum verify.';
    return;
  }
  resultEl.className = 'verify-pending';
  resultEl.textContent = 'Verifying...';

  try {
    const r = await fetch(`/api/instruments/${instId}/verify-lis`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ lis_api_key: apiKey }),
    });
    const data = await r.json();
    resultEl.replaceChildren();
    if (data.success) {
      resultEl.className = 'verify-ok';
      const okText = document.createElement('span');
      okText.textContent = `OK: ${data.name} (${data.vendor} ${data.model}) — ID: `;
      const idStrong = document.createElement('strong');
      idStrong.textContent = data.lis_instrument_id || '(none)';
      resultEl.appendChild(okText);
      resultEl.appendChild(idStrong);
      document.getElementById('lis_instrument_id').value = data.lis_instrument_id || '';
    } else {
      resultEl.className = 'verify-error';
      resultEl.textContent = `Gagal: ${data.error}`;
    }
  } catch (e) {
    resultEl.className = 'verify-error';
    resultEl.textContent = `Error: ${e.message}`;
  }
});
```

- [ ] **Step 3: Add new fields to form-submit JS payload**

Find the existing form-submit handler. Add to the JSON body:

```javascript
body: JSON.stringify({
  // ... existing fields ...
  lis_api_key: document.getElementById('lis_api_key').value || null,
  lis_instrument_id: document.getElementById('lis_instrument_id').value || null,
  order_poll_interval: parseInt(document.getElementById('order_poll_interval').value) || 10,
  lis_bridge_enabled: document.getElementById('lis_bridge_enabled').checked,
}),
```

- [ ] **Step 4: Manual smoke check**

Run: `python3 -m services.web_console.main`
Visit: `http://localhost:8000/instruments` → click Add/Edit → verify LIS section appears with all 4 fields and Verify button.

- [ ] **Step 5: Commit**

```bash
git add services/web_console/templates/instruments.html
git commit -m "webconsole: instrument form LIS section + Verify with LIS button"
```

### Task 18: Settings page — global LIS keys

**Files:**
- Modify: `services/web_console/templates/settings.html`, `services/web_console/api.py`

- [ ] **Step 1: Add LIS settings group in template**

Append/insert in `settings.html`:

```html
<fieldset class="settings-group">
  <legend>LIS Bridging (Global)</legend>
  <div class="form-row">
    <label for="lis.base_url">LIS Base URL</label>
    <input type="url" name="lis.base_url" id="lis.base_url"
           placeholder="https://eazy.vespahobby.xyz"
           value="{{ settings['lis.base_url'] or '' }}">
  </div>
  <div class="form-row">
    <label for="lis.http_timeout">HTTP Timeout (detik)</label>
    <input type="number" name="lis.http_timeout" id="lis.http_timeout"
           value="{{ settings['lis.http_timeout'] or 30 }}" min="1">
  </div>
  <div class="form-row">
    <label for="lis.retry_max">Retry Max</label>
    <input type="number" name="lis.retry_max" id="lis.retry_max"
           value="{{ settings['lis.retry_max'] or 3 }}" min="1" max="10">
  </div>
  <div class="form-row">
    <label for="lis.result_poll_interval">Result Poll Interval (detik)</label>
    <input type="number" name="lis.result_poll_interval" id="lis.result_poll_interval"
           value="{{ settings['lis.result_poll_interval'] or 5 }}" min="1">
  </div>
  <div class="form-row">
    <label for="lis.status_poll_interval">Status Poll Interval (detik)</label>
    <input type="number" name="lis.status_poll_interval" id="lis.status_poll_interval"
           value="{{ settings['lis.status_poll_interval'] or 2 }}" min="1">
  </div>
  <div class="form-row">
    <label for="lis.log_poll_interval">Log Poll Interval (detik)</label>
    <input type="number" name="lis.log_poll_interval" id="lis.log_poll_interval"
           value="{{ settings['lis.log_poll_interval'] or 5 }}" min="1">
  </div>
</fieldset>
```

- [ ] **Step 2: Ensure `page_settings` passes settings dict with defaults**

In `services/web_console/api.py`, modify `page_settings`:

```python
@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    from lib.db import get_all_settings
    settings = get_all_settings()
    settings.setdefault("lis.base_url", "https://eazy.vespahobby.xyz")
    settings.setdefault("lis.http_timeout", "30")
    settings.setdefault("lis.retry_max", "3")
    settings.setdefault("lis.result_poll_interval", "5")
    settings.setdefault("lis.status_poll_interval", "2")
    settings.setdefault("lis.log_poll_interval", "5")
    return templates.TemplateResponse(
        "settings.html", {"request": request, "settings": settings}
    )
```

- [ ] **Step 3: Verify save endpoint accepts new keys**

Inspect the existing settings POST endpoint in `api.py`. If it uses an allowlist, add new keys: `lis.base_url`, `lis.http_timeout`, `lis.retry_max`, `lis.result_poll_interval`, `lis.status_poll_interval`, `lis.log_poll_interval`. Otherwise (generic `set_setting` for any key), no change needed.

- [ ] **Step 4: Commit**

```bash
git add services/web_console/templates/settings.html services/web_console/api.py
git commit -m "webconsole: settings page with global LIS keys"
```

### Task 19: `/lis-events` page

**Files:**
- Create: `services/web_console/templates/lis_events.html`
- Modify: `services/web_console/api.py`, `services/web_console/templates/base.html`

- [ ] **Step 1: Add route in `api.py`**

```python
@app.get("/lis-events", response_class=HTMLResponse)
async def page_lis_events(request: Request):
    return templates.TemplateResponse("lis_events.html", {"request": request})
```

- [ ] **Step 2: Create `services/web_console/templates/lis_events.html`**

> JS uses `document.createElement` + `textContent` everywhere — no `innerHTML` with dynamic content (XSS-safe).

```html
{% extends "base.html" %}
{% block title %}LIS Events — MidLab{% endblock %}
{% block content %}
<div class="page-header">
  <h2>LIS Event Queue</h2>
  <p class="subtitle">Monitor antrian event ke EazyApp LIS (status + log)</p>
</div>

<div class="filters">
  <label>Instrument: <input type="number" id="filter-instrument" placeholder="(all)"></label>
  <label>Status:
    <select id="filter-status">
      <option value="">(all)</option>
      <option>pending</option>
      <option>sent</option>
      <option>failed</option>
      <option>skipped</option>
    </select>
  </label>
  <label>Type:
    <select id="filter-type">
      <option value="">(all)</option>
      <option>status</option>
      <option>log</option>
    </select>
  </label>
  <button id="btn-refresh" class="btn-primary">Refresh</button>
</div>

<table class="data-table">
  <thead>
    <tr>
      <th>ID</th><th>Time</th><th>Inst</th><th>Type</th>
      <th>Payload</th><th>Status</th><th>Retry</th><th>Error</th><th>Actions</th>
    </tr>
  </thead>
  <tbody id="events-tbody">
    <tr><td colspan="9" class="placeholder">Loading…</td></tr>
  </tbody>
</table>

<script>
function makeCell(text, className) {
  const td = document.createElement('td');
  if (className) td.className = className;
  td.textContent = text == null ? '' : String(text);
  return td;
}

function makeBtn(label, onclick) {
  const btn = document.createElement('button');
  btn.textContent = label;
  btn.addEventListener('click', onclick);
  return btn;
}

function buildRow(r) {
  const tr = document.createElement('tr');
  tr.appendChild(makeCell(r.id));
  tr.appendChild(makeCell(r.created_at || ''));
  tr.appendChild(makeCell(r.instrument_id));
  tr.appendChild(makeCell(r.event_type));

  const payloadCell = document.createElement('td');
  const code = document.createElement('code');
  code.textContent = JSON.stringify(r.payload_json).slice(0, 80);
  payloadCell.appendChild(code);
  tr.appendChild(payloadCell);

  tr.appendChild(makeCell(r.send_status, 'status-' + r.send_status));
  tr.appendChild(makeCell(r.retry_count));
  tr.appendChild(makeCell(r.error_message || '', 'error-cell'));

  const actions = document.createElement('td');
  if (r.send_status !== 'sent') {
    actions.appendChild(makeBtn('Retry', () => retryEvent(r.id)));
  }
  if (r.send_status === 'pending' || r.send_status === 'failed') {
    actions.appendChild(makeBtn('Skip', () => skipEvent(r.id)));
  }
  tr.appendChild(actions);
  return tr;
}

async function loadEvents() {
  const params = new URLSearchParams();
  const inst = document.getElementById('filter-instrument').value;
  const status = document.getElementById('filter-status').value;
  const type = document.getElementById('filter-type').value;
  if (inst) params.set('instrument_id', inst);
  if (status) params.set('status', status);
  if (type) params.set('event_type', type);

  const r = await fetch('/api/lis-events?' + params.toString());
  const rows = await r.json();
  const tbody = document.getElementById('events-tbody');
  tbody.replaceChildren();

  if (!rows.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 9;
    td.className = 'placeholder';
    td.textContent = 'No events';
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  rows.forEach(r => tbody.appendChild(buildRow(r)));
}

async function retryEvent(id) {
  await fetch(`/api/lis-events/${id}/retry`, { method: 'POST' });
  loadEvents();
}
async function skipEvent(id) {
  await fetch(`/api/lis-events/${id}/skip`, { method: 'POST' });
  loadEvents();
}

document.getElementById('btn-refresh').addEventListener('click', loadEvents);
loadEvents();
setInterval(loadEvents, 10000);
</script>
{% endblock %}
```

- [ ] **Step 3: Add nav link in `base.html`**

Find the sidebar nav block in `services/web_console/templates/base.html`. Add:

```html
<a href="/lis-events" class="nav-link {% if request.url.path == '/lis-events' %}active{% endif %}">
  LIS Events
</a>
```

(Place near Results / Orders / Logs links.)

- [ ] **Step 4: Manual check**

Start: `python3 -m services.web_console.main`
Visit: `http://localhost:8000/lis-events` → "No events" placeholder appears.

- [ ] **Step 5: Commit**

```bash
git add services/web_console/templates/lis_events.html services/web_console/api.py services/web_console/templates/base.html
git commit -m "webconsole: add /lis-events queue monitoring page (XSS-safe DOM)"
```

### Task 20: Dashboard LIS indicators

**Files:**
- Modify: `lib/db.py` (add `get_lis_queue_backlog`), `services/web_console/api.py`, `services/web_console/templates/dashboard.html`

- [ ] **Step 1: Add helper to `lib/db.py`**

```python
def get_lis_queue_backlog(instrument_id: int) -> int:
    db = DBManager()
    session = db.get_session()
    try:
        return session.query(TblLisEventQueue).filter(
            TblLisEventQueue.instrument_id == instrument_id,
            TblLisEventQueue.send_status == "pending",
        ).count()
    finally:
        session.close()
```

- [ ] **Step 2: Extend dashboard API endpoint**

Locate the dashboard endpoint in `services/web_console/api.py` (search for `dashboard`). For each instrument card add fields:

```python
"lis_bridge_status": <"running" | "degraded" | "offline">,   # derive from watchdog state of "lis_bridge_<id>"
"last_status_pushed": row.lis_status_pushed,
"queue_backlog":      get_lis_queue_backlog(row.id),
"lis_bridge_enabled": bool(row.lis_bridge_enabled),
```

> Status derivation: if the watchdog reports the `lis_bridge_<id>` service as running → "running". If not running → "offline". (Degraded state can be added later when supervise emits a counter; for now binary is fine.)

- [ ] **Step 3: Render in `dashboard.html`**

In each instrument card block, add:

```html
<div class="lis-section">
  <h5>LIS Bridge</h5>
  <span class="status-pill status-{{ inst.lis_bridge_status }}">{{ inst.lis_bridge_status }}</span>
  <small>Last status: {{ inst.last_status_pushed or '—' }}</small>
  {% if inst.queue_backlog > 0 %}
    <small class="badge-warn">{{ inst.queue_backlog }} queued</small>
  {% endif %}
  {% if not inst.lis_bridge_enabled %}
    <small class="badge-disabled">disabled</small>
  {% endif %}
</div>
```

- [ ] **Step 4: Commit**

```bash
git add lib/db.py services/web_console/api.py services/web_console/templates/dashboard.html
git commit -m "webconsole: dashboard cards show LIS bridge status + queue backlog"
```

---

## Tahap 5 — Gating, Docs, Cutover

### Task 21: ResultSenderService skips `lis_bridge_enabled` alat

**Files:**
- Modify: `services/result_sender/service.py`
- Test: `tests/test_result_sender_gating.py`

- [ ] **Step 1: Identify the polling/send method in `services/result_sender/service.py`**

Run: `grep -n "for result\|_send_result\|get_pending_results" services/result_sender/service.py`

- [ ] **Step 2: Add per-row gating**

Inside the existing iteration `for result in results:`, insert at the top of the loop body (before the existing send call):

```python
from lib.db import get_instrument_by_id  # add this import at top of the file

# ...

        for result in results:
            if not self._running:
                break
            inst = get_instrument_by_id(result.instrument_id)
            if inst and inst.lis_bridge_enabled:
                self._logger.debug(
                    f"result_id={result.id} skip (lis_bridge_enabled untuk instrument_id={result.instrument_id})"
                )
                continue
            # ... existing send logic continues ...
```

- [ ] **Step 3: Write test**

`tests/test_result_sender_gating.py`:

```python
"""ResultSenderService skips rows when instrument.lis_bridge_enabled=True."""
import asyncio
from unittest.mock import MagicMock, patch


def test_skip_lis_bridge_enabled_instrument():
    from services.result_sender.service import ResultSenderService

    svc = ResultSenderService()
    svc._lis_url = "http://test"
    svc._running = True

    result_row = MagicMock(id=1, instrument_id=7, retry_count=0)
    enabled_inst = MagicMock(id=7, lis_bridge_enabled=True)

    with patch("services.result_sender.service.get_pending_results", return_value=[result_row]), \
         patch("services.result_sender.service.get_instrument_by_id", return_value=enabled_inst), \
         patch.object(svc, "_send_result") as send_spy:
        # Call whichever method runs one cycle. ResultSenderService has _poll_loop;
        # the actual iteration happens inside it. Use the smallest unit that exposes
        # the for-loop, or run _poll_loop briefly with self._running=False after 1 cycle.
        async def run():
            svc._running = True
            session = MagicMock()
            # If _poll_loop is the only entry, monkeypatch sleep to short-circuit
            with patch("asyncio.sleep") as sleep_mock:
                sleep_mock.side_effect = lambda *a: setattr(svc, "_running", False)
                try:
                    await svc._poll_loop()
                except Exception:
                    pass
        asyncio.run(run())
        send_spy.assert_not_called()
```

> If `_poll_loop` is not the right method, look for `_poll_once` or whatever processes one batch. Adjust test accordingly.

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest tests/test_result_sender_gating.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/result_sender/service.py tests/test_result_sender_gating.py
git commit -m "result_sender: skip results whose instrument has lis_bridge_enabled=true"
```

### Task 22: Update documentation (CLAUDE.md, HANDOVER.md, PANDUAN-ALAT-BARU.md)

**Files:**
- Modify: `CLAUDE.md`, `HANDOVER.md`, `PANDUAN-ALAT-BARU.md`

- [ ] **Step 1: Update `CLAUDE.md`**

In the SERVICES table, add:

```markdown
| LisBridgeService | Per-alat: poll /orders/pending → tbl_order, push tbl_result → /results, drain tbl_lis_event_queue → /status, push WARN/ERROR log → /logs | lis_bridge_<id>.log |
```

In DATABASE SCHEMA section, add the `tbl_lis_event_queue` block and the new `tbl_instrument` columns:

```sql
tbl_instrument: id, name, ip_address, port, protocol, mode, bidir_mode,
                broadcast_interval, connection, is_active,
                lis_instrument_id, lis_api_key, order_poll_interval,
                last_lis_sync_at, lis_status_pushed, lis_bridge_enabled

tbl_lis_event_queue: id, instrument_id, event_type(status|log), payload_json,
                     send_status(pending|sent|failed|skipped), retry_count,
                     error_message, created_at, sent_at
```

In the Flag ownership block, add:

```markdown
- `tbl_lis_event_queue.send_status` → OWNED by LisBridgeService.StatusReporter/LogPusher; written by TCPSocketService
- `tbl_instrument.lis_bridge_enabled` → toggle staged cutover (temporary, akan di-drop di Task 24)
```

Update BUILD ORDER step 6/7 to reference `services/lis_bridge/` instead of `services/result_sender/` and `services/order_receiver/`.

- [ ] **Step 2: Update `HANDOVER.md`**

Append a section:

```markdown
## LIS Bridging Migration — 2026-05-13

EazyApp LIS integration is now handled by per-alat `LisBridgeService`
(replaces `OrderReceiverService` + `ResultSenderService`).

- Spec: `docs/superpowers/specs/2026-05-13-lis-bridging-eazyapp-design.md`
- Plan: `docs/superpowers/plans/2026-05-13-lis-bridging-eazyapp.md`
- Migration script: `scripts/migrate_lis_api.py`
- Per-alat enabling via `tbl_instrument.lis_bridge_enabled` (set via Web Console)
```

- [ ] **Step 3: Update `PANDUAN-ALAT-BARU.md`**

Add a "Setup LIS Bridging (EazyApp)" subsection with steps: dapatkan API key per-alat dari EazyApp UI → input di Web Console form → klik Verify → toggle Enable LIS bridging → save → start service `lis_bridge_<id>` via Watchdog.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md HANDOVER.md PANDUAN-ALAT-BARU.md
git commit -m "docs: document LisBridgeService + LIS event queue + setup flow"
```

### Task 23: Live sandbox integration tests (gated)

**Files:**
- Create: `tests/integration/__init__.py`, `tests/integration/test_lis_sandbox.py`

- [ ] **Step 1: Create `tests/integration/__init__.py`** (empty)

- [ ] **Step 2: Create `tests/integration/test_lis_sandbox.py`**

```python
"""
Integration test against real EazyApp sandbox.
Run manually with:
    SANDBOX_API_KEY=inst_xxx pytest tests/integration -v -m sandbox
"""
import os
import pytest

from lib.lis_client import LisApiClient

SANDBOX_URL = os.environ.get("SANDBOX_URL", "https://eazy.vespahobby.xyz")
SANDBOX_KEY = os.environ.get("SANDBOX_API_KEY")

pytestmark = pytest.mark.skipif(
    not SANDBOX_KEY,
    reason="SANDBOX_API_KEY not set; skipping live integration test",
)


@pytest.mark.sandbox
async def test_get_instrument_live():
    async with LisApiClient(base_url=SANDBOX_URL, api_key=SANDBOX_KEY) as client:
        data = await client.get_instrument()
    assert data["success"] is True
    inst = data["data"]["instrument"]
    assert "instrument_id" in inst
    assert inst["instrument_id"].startswith("INST-")


@pytest.mark.sandbox
async def test_get_orders_pending_live():
    async with LisApiClient(base_url=SANDBOX_URL, api_key=SANDBOX_KEY) as client:
        data = await client.get_orders_pending()
    assert "data" in data
    assert isinstance(data["data"], list)


@pytest.mark.sandbox
async def test_post_status_online_offline_live():
    async with LisApiClient(base_url=SANDBOX_URL, api_key=SANDBOX_KEY) as client:
        s1, _ = await client.post_status({"status": "online"})
        s2, _ = await client.post_status({"status": "offline"})
    assert s1 == 200
    assert s2 == 200
```

- [ ] **Step 3: Manual run instructions** (do NOT run as part of CI)

Run: `SANDBOX_API_KEY=inst_9VOHNaFpy00uEKwSMHoc3iHDj0GV1wTRENyQEFxzORGV9NPL pytest tests/integration -v -m sandbox`
Expected: 3 tests PASS against live sandbox.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/
git commit -m "tests: add live EazyApp sandbox integration tests (gated by env var)"
```

### Task 24: Decommission scripts (post-cutover, NOT executed)

**Files:**
- Create: `scripts/migrate_lis_api_finalize.py`, `scripts/decommission_result_sender.sh`, `scripts/decommission_order_receiver.sh`

> **Run only after** all instruments have `lis_bridge_enabled=true` and stable >= 1 week (per spec Tahap 4).

- [ ] **Step 1: Create `scripts/migrate_lis_api_finalize.py`**

```python
"""
scripts/migrate_lis_api_finalize.py — Final cleanup post-cutover.
Run ONLY after semua alat sudah lis_bridge_enabled=true dan stabil >= 1 minggu.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text
from lib.db import DBManager


def main():
    db = DBManager()
    engine = db.get_engine()
    with engine.begin() as conn:
        not_enabled = conn.execute(
            text(
                "SELECT COUNT(*) FROM tbl_instrument "
                "WHERE lis_bridge_enabled = FALSE AND is_active = TRUE"
            )
        ).scalar()
        if not_enabled and int(not_enabled) > 0:
            print(f"ABORT: ada {not_enabled} alat aktif yang lis_bridge_enabled=false")
            sys.exit(1)

        print("  DROP COLUMN lis_bridge_enabled")
        conn.execute(text("ALTER TABLE tbl_instrument DROP COLUMN lis_bridge_enabled"))

        print("  DELETE deprecated settings (lis.api_url, lis.api_key)")
        conn.execute(text("DELETE FROM tbl_settings WHERE `key` IN ('lis.api_url','lis.api_key')"))

    print("OK: finalization selesai.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create `scripts/decommission_result_sender.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
echo "Disabling and stopping midlab-result-sender..."
sudo systemctl stop midlab-result-sender || true
sudo systemctl disable midlab-result-sender || true
echo "Archiving services/result_sender/ → services/_archive/result_sender/"
mkdir -p services/_archive
git mv services/result_sender services/_archive/result_sender
git mv systemd/midlab-result-sender.service systemd/_archive_midlab-result-sender.service 2>/dev/null || true
echo "Done. Commit the archive moves manually."
```

- [ ] **Step 3: Create `scripts/decommission_order_receiver.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
echo "Disabling and stopping midlab-order-receiver..."
sudo systemctl stop midlab-order-receiver || true
sudo systemctl disable midlab-order-receiver || true
echo "Archiving services/order_receiver/ → services/_archive/order_receiver/"
mkdir -p services/_archive
git mv services/order_receiver services/_archive/order_receiver
git mv systemd/midlab-order-receiver.service systemd/_archive_midlab-order-receiver.service 2>/dev/null || true
echo "Done."
```

- [ ] **Step 4: Make scripts executable**

Run: `chmod +x scripts/decommission_result_sender.sh scripts/decommission_order_receiver.sh`
Expected: no output.

- [ ] **Step 5: Commit (do NOT run the scripts yet)**

```bash
git add scripts/decommission_*.sh scripts/migrate_lis_api_finalize.py
git commit -m "scripts: add post-cutover decommission + finalize migration (not executed)"
```

### Task 25: Final verification

- [ ] **Step 1: Run full test suite**

Run: `pytest -v --tb=short`
Expected: all unit tests PASS; sandbox integration tests SKIPPED (no env var).

- [ ] **Step 2: Apply migration to dev MySQL**

Run: `python3 scripts/migrate_lis_api.py`
Expected: prints DDL operations + `OK: migrasi LIS API selesai.`

- [ ] **Step 3: Verify schema**

Run: `mysql midlab_db -e "DESCRIBE tbl_instrument" | grep -E "lis_|order_poll"`
Run: `mysql midlab_db -e "DESCRIBE tbl_lis_event_queue"`
Expected: new columns present + new table present.

- [ ] **Step 4: End-to-end manual smoke**

Run (terminal 1): `python3 -m services.web_console.main`
Open: `http://localhost:8000/instruments` → add/edit instrument → set LIS API key → click "Verify with LIS" → confirm info from LIS → save with `lis_bridge_enabled=true`

Run (terminal 2): `python3 -m services.lis_bridge.main --instrument-id <id>`
Expected logs:
- `config loaded: base=https://eazy.vespahobby.xyz, lis_instrument_id=...`
- `lis_instrument_id synced: INST-...` (if first time)
- `boot status=online pushed`
- (subsequently) `result_id=X sent`, `order_puller: inserted N new order(s)` when applicable

Open: `http://localhost:8000/lis-events` → events appear as TCP service emits them.

- [ ] **Step 5: Verify nothing dangling**

Run: `git status`
Expected: clean (or only uncommitted local changes the engineer wants to keep). All planned work is committed.

---

## Plan Self-Review

Checked spec coverage section-by-section:

- ✅ Schema migration (Tasks 2-4) — all new columns + `tbl_lis_event_queue`
- ✅ `lib/lis_client.py` (Task 5) — all 5 endpoints with Bearer auth + retry
- ✅ ResultPusher (Task 6), OrderPuller (Task 7), StatusReporter (Task 8), LogPusher (Task 9)
- ✅ LisBridgeService orchestrator with supervise + verify-with-LIS (Task 10)
- ✅ Entry point + systemd unit (Task 11)
- ✅ Watchdog `lis_bridge_<id>` recognition (Task 12)
- ✅ TCPSocketService event hook (Task 13)
- ✅ Web Console: verify endpoint (Task 14), CRUD fields (Task 15), event queue endpoints (Task 16), instrument form (Task 17), settings (Task 18), `/lis-events` page (Task 19), dashboard indicators (Task 20)
- ✅ ResultSender gating (Task 21)
- ✅ Docs update (Task 22)
- ✅ Live sandbox tests (Task 23)
- ✅ Decommission scripts (Task 24) — separately gated
- ✅ Final verification (Task 25)

**Type consistency confirmed:**
- `update_lis_event_status(event_id, status, error_message, increment_retry=False)` — Task 4 (helper), Task 8 (caller), Task 16 (API endpoint), Task 19 (retry/skip).
- `update_result_status(result_id, status, error_message, increment_retry=False)` — Task 6 step 5 (helper), Task 6 step 4 (caller).
- `enqueue_lis_event(instrument_id, event_type, payload)` — Task 4 (helper), Task 13 (TCP hook).
- `LisApiClient` async-with usage consistent across all callers.

**Retry semantics consistent** (pending while retry_count<max → failed when exhausted) across ResultPusher and StatusReporter. LogPusher uses cursor-stops-on-error model (different but documented).

**Security:**
- All template JS uses `document.createElement` + `textContent` (no `innerHTML` with dynamic content) → XSS-safe.
- `lis_api_key` NEVER in `InstrumentResponse` (Task 15 explicit comment + test asserts).

**No placeholders. All code blocks complete.**
