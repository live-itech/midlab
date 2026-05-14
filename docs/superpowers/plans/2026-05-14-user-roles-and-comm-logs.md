# User Roles & Raw Comm Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cookie-session login with 2 roles (admin/viewer) + audit log to Web Console, and per-instrument bidirectional raw frame logging exposed via existing Logs viewer.

**Architecture:** Two phases. Phase A introduces `tbl_user`/`tbl_session`/`tbl_audit_log`, a `lib/auth.py` module with bcrypt + session helpers, and a `require_role(...)` FastAPI dependency wrapping every mutating endpoint. Existing `x-api-key` header continues to work (treated as admin) so scripts don't break. Phase B introduces `lib/comm_logger.py` writing `tcp_<id>.comm.log` via `RotatingFileHandler`; TCP socket service calls `rx()`/`tx()` at each socket I/O point. Web Console enriches service registry with virtual `tcp_<id>__comm` entries that the log resolver maps to the `.comm.log` file.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy, MySQL, Jinja2, `passlib[bcrypt]`, stdlib `logging.handlers.RotatingFileHandler`, `secrets`.

**Spec:** `docs/superpowers/specs/2026-05-14-user-roles-and-comm-logs-design.md`

---

## File Structure

**Phase A — Auth & Roles:**
- Create `lib/auth.py` — hashing, session lifecycle, `require_role` Depends, audit helper, rate-limit dict
- Modify `lib/db.py` — add `User`, `Session`, `AuditLog` models + helper `bootstrap_admin_if_empty`
- Create `scripts/migrate_add_auth.sql` — table DDL
- Modify `services/web_console/api.py` — login/logout/users/audit/whoami endpoints + wrap mutating endpoints with `require_role('admin')` and `audit(...)`
- Modify `services/web_console/api.py` startup hook — call `bootstrap_admin_if_empty`
- Create `services/web_console/templates/login.html`, `change_password.html`, `users.html`, `audit.html`
- Modify existing templates (`services.html`, `logs.html`, `protocols.html`, `results.html`, `orders.html`, dashboard) — header bar with username/role/logout + role-aware admin-only hiding
- Modify `scripts/install.sh` — prompt admin creds + apply auth migration
- Add `passlib[bcrypt]>=1.7.4` to `requirements.txt`
- Create `tests/test_auth.py`, `tests/test_auth_models.py`, `tests/test_auth_depends.py`, `tests/test_bootstrap_admin.py`, `tests/test_login_flow.py`, `tests/test_role_enforcement.py`, `tests/test_users_api.py`

**Phase B — Comm Logs:**
- Create `lib/comm_logger.py` — `CommLogger` singleton + decoder
- Modify `services/tcp_socket/receiver.py`, `broadcast_worker.py`, `query_handler.py`, `service.py` — call `comm_logger.rx/tx` at I/O sites
- Modify `services/web_console/api.py` — `list_services` enrichment for `__comm` virtual entries + log resolver mapping in `get_logs` and `stream_logs`
- Create `tests/test_comm_logger.py`, `tests/test_logs_comm_resolver.py`

**Security note for template JS:** all dynamic DOM rendering in `users.html`/`audit.html` uses `textContent` + `createElement` (never `innerHTML` with concatenated values) to avoid XSS from a malicious username or audit metadata.

---

## Phase A — User Roles & Auth

### Task 1: Add `passlib` dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add line**

Append to `requirements.txt`:
```
passlib[bcrypt]>=1.7.4
```

- [ ] **Step 2: Install**

Run: `pip install -r requirements.txt`
Expected: `passlib` and `bcrypt` installed.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add passlib[bcrypt] for password hashing"
```

---

### Task 2: SQL migration for auth tables

**Files:**
- Create: `scripts/migrate_add_auth.sql`

- [ ] **Step 1: Write the migration**

Create `scripts/migrate_add_auth.sql`:
```sql
-- Migration: add user roles, sessions, audit log
-- Date: 2026-05-14

CREATE TABLE IF NOT EXISTS tbl_user (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('admin','viewer') NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  force_password_change BOOLEAN NOT NULL DEFAULT FALSE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tbl_session (
  token VARCHAR(64) PRIMARY KEY,
  user_id INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ip_address VARCHAR(45) NULL,
  CONSTRAINT fk_session_user FOREIGN KEY (user_id) REFERENCES tbl_user(id) ON DELETE CASCADE,
  INDEX idx_session_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tbl_audit_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  username VARCHAR(64) NOT NULL,
  action VARCHAR(64) NOT NULL,
  target VARCHAR(255) NULL,
  metadata JSON NULL,
  ip_address VARCHAR(45) NULL,
  logged_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_audit_logged_at (logged_at),
  INDEX idx_audit_user (user_id),
  INDEX idx_audit_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: Commit**

```bash
git add scripts/migrate_add_auth.sql
git commit -m "db: migration for user/session/audit tables"
```

---

### Task 3: Add SQLAlchemy models

**Files:**
- Modify: `lib/db.py` (append models near other model classes)
- Test: `tests/test_auth_models.py` (NEW)

- [ ] **Step 1: Write failing test**

Create `tests/test_auth_models.py`:
```python
from datetime import datetime, timedelta
from lib.db import User, Session as DbSession, AuditLog

def test_user_model_fields():
    u = User(username="alice", password_hash="x", role="admin")
    assert u.username == "alice"
    assert u.role == "admin"

def test_session_model_fields():
    s = DbSession(token="t", user_id=1, expires_at=datetime.utcnow() + timedelta(hours=8))
    assert s.token == "t"
    assert s.user_id == 1

def test_audit_log_fields():
    a = AuditLog(username="alice", action="login.success", target=None)
    assert a.action == "login.success"
```

- [ ] **Step 2: Run test, expect ImportError**

Run: `pytest tests/test_auth_models.py -v`
Expected: ImportError for `User`/`Session`/`AuditLog`.

- [ ] **Step 3: Add models to `lib/db.py`**

In `lib/db.py`, after the existing models (e.g. after `Instrument`/`Result`/`Order`), append:

```python
from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.sql import func


class User(Base):
    __tablename__ = "tbl_user"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(16), nullable=False)  # 'admin' | 'viewer'
    is_active = Column(Boolean, nullable=False, default=True)
    force_password_change = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    last_login_at = Column(DateTime, nullable=True)


class Session(Base):
    __tablename__ = "tbl_session"
    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("tbl_user.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False, server_default=func.now())
    ip_address = Column(String(45), nullable=True)


class AuditLog(Base):
    __tablename__ = "tbl_audit_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("tbl_user.id", ondelete="SET NULL"), nullable=True)
    username = Column(String(64), nullable=False)
    action = Column(String(64), nullable=False)
    target = Column(String(255), nullable=True)
    extra_metadata = Column("metadata", JSON, nullable=True)  # 'metadata' reserved on Base
    ip_address = Column(String(45), nullable=True)
    logged_at = Column(DateTime, nullable=False, server_default=func.now())
```

Only add imports that aren't already present in `lib/db.py`. The `extra_metadata` Python attribute maps to the DB column `metadata` because `Base.metadata` is reserved.

- [ ] **Step 4: Run test, expect PASS**

Run: `pytest tests/test_auth_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/db.py tests/test_auth_models.py
git commit -m "db: User, Session, AuditLog models"
```

---

### Task 4: `lib/auth.py` — hashing & token primitives

**Files:**
- Create: `lib/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth.py`:
```python
from lib.auth import hash_password, verify_password, generate_session_token

def test_hash_and_verify_roundtrip():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False

def test_hash_is_salted_unique():
    assert hash_password("same") != hash_password("same")

def test_token_entropy_and_length():
    t1 = generate_session_token()
    t2 = generate_session_token()
    assert t1 != t2
    assert len(t1) >= 40
```

- [ ] **Step 2: Run, expect ImportError**

Run: `pytest tests/test_auth.py -v`
Expected: ImportError.

- [ ] **Step 3: Create `lib/auth.py`**

```python
"""Auth helpers: password hashing, session tokens, FastAPI Depends, audit."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Optional

from passlib.hash import bcrypt

SESSION_TTL_HOURS = 8
SESSION_COOKIE_NAME = "midlab_session"


def hash_password(plain: str) -> str:
    return bcrypt.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.verify(plain, hashed)
    except Exception:
        return False


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def session_expiry() -> datetime:
    return datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)
```

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/auth.py tests/test_auth.py
git commit -m "auth: password hashing and session token primitives"
```

---

### Task 5: Session persistence helpers + `tmp_db` fixture

**Files:**
- Modify: `lib/auth.py`
- Modify: `tests/conftest.py` (add fixture if missing)
- Modify: `tests/test_auth.py` (append tests)

- [ ] **Step 1: Add `tmp_db` fixture**

Check `tests/conftest.py`. If no `tmp_db` fixture exists, append:
```python
import pytest
from lib.db import Base, engine

@pytest.fixture
def tmp_db():
    Base.metadata.create_all(bind=engine)
    yield
```

- [ ] **Step 2: Append failing tests to `tests/test_auth.py`**

```python
from datetime import datetime, timedelta
from lib.db import SessionLocal, User, Session as DbSession
from lib.auth import (
    hash_password, create_session, load_session_user, revoke_session,
)

def _make_user(db, username="alice", role="admin"):
    db.query(User).filter(User.username == username).delete()
    u = User(username=username, password_hash=hash_password("pw"), role=role, is_active=True)
    db.add(u); db.commit(); db.refresh(u)
    return u

def test_create_and_load_session(tmp_db):
    db = SessionLocal()
    try:
        u = _make_user(db)
        token = create_session(db, u.id, ip="127.0.0.1")
        assert token
        loaded = load_session_user(db, token)
        assert loaded is not None
        assert loaded.username == "alice"
    finally:
        db.close()

def test_load_session_expired(tmp_db):
    db = SessionLocal()
    try:
        u = _make_user(db, username="bob")
        token = "expired-token-xxxxxxxxxxxxxxxxxxxx"
        db.add(DbSession(token=token, user_id=u.id,
                         expires_at=datetime.utcnow() - timedelta(minutes=1)))
        db.commit()
        assert load_session_user(db, token) is None
    finally:
        db.close()

def test_revoke_session(tmp_db):
    db = SessionLocal()
    try:
        u = _make_user(db, username="carol")
        token = create_session(db, u.id, ip=None)
        assert load_session_user(db, token) is not None
        revoke_session(db, token)
        assert load_session_user(db, token) is None
    finally:
        db.close()
```

- [ ] **Step 3: Run, expect ImportError**

Run: `pytest tests/test_auth.py -v`
Expected: ImportError for `create_session` / `load_session_user` / `revoke_session`.

- [ ] **Step 4: Implement helpers in `lib/auth.py`**

Append:
```python
from sqlalchemy.orm import Session as OrmSession
from lib.db import Session as DbSession, User


def create_session(db: OrmSession, user_id: int, ip: Optional[str]) -> str:
    token = generate_session_token()
    db.add(DbSession(
        token=token,
        user_id=user_id,
        expires_at=session_expiry(),
        ip_address=ip,
    ))
    db.commit()
    return token


def load_session_user(db: OrmSession, token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    row = db.query(DbSession).filter(DbSession.token == token).first()
    if row is None or row.expires_at <= datetime.utcnow():
        return None
    user = db.query(User).filter(User.id == row.user_id, User.is_active == True).first()  # noqa: E712
    if user is None:
        return None
    row.last_seen_at = datetime.utcnow()
    db.commit()
    return user


def revoke_session(db: OrmSession, token: str) -> None:
    db.query(DbSession).filter(DbSession.token == token).delete()
    db.commit()


def purge_expired_sessions(db: OrmSession) -> int:
    n = db.query(DbSession).filter(DbSession.expires_at <= datetime.utcnow()).delete()
    db.commit()
    return n
```

- [ ] **Step 5: Run, expect PASS**

Run: `pytest tests/test_auth.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add lib/auth.py tests/test_auth.py tests/conftest.py
git commit -m "auth: session create/load/revoke/purge"
```

---

### Task 6: `require_role` FastAPI dependency + audit helper

**Files:**
- Modify: `lib/auth.py`
- Test: `tests/test_auth_depends.py` (NEW)

- [ ] **Step 1: Write failing test**

Create `tests/test_auth_depends.py`:
```python
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from lib.auth import require_role, hash_password, create_session, SESSION_COOKIE_NAME
from lib.db import SessionLocal, User

def _make_app():
    app = FastAPI()

    @app.get("/admin-only")
    def admin_route(user=Depends(require_role("admin"))):
        return {"user": user.username}

    @app.get("/any-role")
    def any_route(user=Depends(require_role("admin", "viewer"))):
        return {"user": user.username, "role": user.role}

    return app

def _seed_user(role):
    db = SessionLocal()
    db.query(User).filter(User.username == f"u_{role}").delete()
    u = User(username=f"u_{role}", password_hash=hash_password("pw"), role=role, is_active=True)
    db.add(u); db.commit(); db.refresh(u)
    token = create_session(db, u.id, ip=None)
    db.close()
    return token

def test_no_cookie_returns_401(tmp_db):
    client = TestClient(_make_app())
    r = client.get("/admin-only")
    assert r.status_code == 401

def test_viewer_blocked_from_admin(tmp_db):
    token = _seed_user("viewer")
    client = TestClient(_make_app())
    r = client.get("/admin-only", cookies={SESSION_COOKIE_NAME: token})
    assert r.status_code == 401

def test_admin_allowed(tmp_db):
    token = _seed_user("admin")
    client = TestClient(_make_app())
    r = client.get("/admin-only", cookies={SESSION_COOKIE_NAME: token})
    assert r.status_code == 200
    assert r.json()["user"] == "u_admin"

def test_any_role_viewer_allowed(tmp_db):
    token = _seed_user("viewer")
    client = TestClient(_make_app())
    r = client.get("/any-role", cookies={SESSION_COOKIE_NAME: token})
    assert r.status_code == 200
```

- [ ] **Step 2: Run, expect ImportError**

Run: `pytest tests/test_auth_depends.py -v`
Expected: ImportError for `require_role`.

- [ ] **Step 3: Implement `require_role` in `lib/auth.py`**

Append:
```python
import os
from fastapi import Request, HTTPException
from lib.db import SessionLocal


def _get_api_key(request: Request) -> Optional[str]:
    return request.headers.get("x-api-key") or request.headers.get("X-API-Key")


def _api_key_is_valid(provided: Optional[str]) -> bool:
    if not provided:
        return False
    expected = os.environ.get("MIDLAB_API_KEY") or _read_config_api_key()
    return bool(expected) and provided == expected


def _read_config_api_key() -> str:
    try:
        from lib.config import config
        return config.get("web_console.api_key", "") or ""
    except Exception:
        return ""


def require_role(*roles: str):
    """Accept cookie session OR valid x-api-key (treated as admin)."""
    allowed = set(roles)

    def _dep(request: Request) -> User:
        api_key = _get_api_key(request)
        if api_key and _api_key_is_valid(api_key):
            if "admin" in allowed:
                return User(id=0, username="api-key", role="admin",
                            password_hash="", is_active=True,
                            force_password_change=False)
            raise HTTPException(status_code=403, detail="API key does not satisfy role")

        token = request.cookies.get(SESSION_COOKIE_NAME)
        db = SessionLocal()
        try:
            user = load_session_user(db, token)
        finally:
            db.close()

        if user is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if user.role not in allowed:
            raise HTTPException(status_code=401, detail="Insufficient role")
        return user

    return _dep
```

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_auth_depends.py -v`
Expected: all PASS.

- [ ] **Step 5: Implement audit helper**

Append to `lib/auth.py`:
```python
from lib.db import AuditLog


def audit(db: OrmSession, *, user: Optional[User], action: str,
          target: Optional[str] = None, metadata: Optional[dict] = None,
          request: Optional[Request] = None) -> None:
    ip = None
    if request is not None and request.client:
        ip = request.client.host
    db.add(AuditLog(
        user_id=(user.id if user and user.id else None),
        username=(user.username if user else "anonymous"),
        action=action,
        target=target,
        extra_metadata=metadata,
        ip_address=ip,
    ))
    db.commit()
```

- [ ] **Step 6: Add audit test**

Append to `tests/test_auth_depends.py`:
```python
from lib.auth import audit
from lib.db import AuditLog

def test_audit_writes_row(tmp_db):
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "z").delete()
        u = User(username="z", password_hash="x", role="admin", is_active=True)
        db.add(u); db.commit(); db.refresh(u)
        audit(db, user=u, action="service.stop", target="tcp_3", metadata={"pid": 1234})
        rows = db.query(AuditLog).filter(AuditLog.action == "service.stop").all()
        assert any(r.target == "tcp_3" and r.extra_metadata == {"pid": 1234} for r in rows)
    finally:
        db.close()
```

Run: `pytest tests/test_auth_depends.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add lib/auth.py tests/test_auth_depends.py
git commit -m "auth: require_role Depends + audit helper, x-api-key fallback"
```

---

### Task 7: Bootstrap admin on startup + DB helper

**Files:**
- Modify: `lib/db.py`
- Modify: `services/web_console/api.py` (startup hook)
- Test: `tests/test_bootstrap_admin.py` (NEW)

- [ ] **Step 1: Write failing test**

Create `tests/test_bootstrap_admin.py`:
```python
from lib.db import SessionLocal, User, bootstrap_admin_if_empty

def test_bootstrap_when_empty(tmp_db, monkeypatch):
    monkeypatch.setenv("MIDLAB_ADMIN_USER", "bootadmin")
    monkeypatch.setenv("MIDLAB_ADMIN_PASS", "bootpass!1")
    db = SessionLocal()
    db.query(User).delete()
    db.commit(); db.close()

    bootstrap_admin_if_empty()

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == "bootadmin").first()
        assert u is not None
        assert u.role == "admin"
    finally:
        db.close()

def test_bootstrap_skips_when_user_exists(tmp_db, monkeypatch):
    monkeypatch.setenv("MIDLAB_ADMIN_USER", "second")
    monkeypatch.setenv("MIDLAB_ADMIN_PASS", "x")
    bootstrap_admin_if_empty()
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.username == "second").first() is None
    finally:
        db.close()
```

- [ ] **Step 2: Run, expect ImportError**

Run: `pytest tests/test_bootstrap_admin.py -v`
Expected: ImportError.

- [ ] **Step 3: Add helper to `lib/db.py`**

Append to `lib/db.py`:
```python
def bootstrap_admin_if_empty() -> None:
    """Create initial admin from MIDLAB_ADMIN_USER/PASS env if tbl_user empty."""
    import os
    import logging
    from lib.auth import hash_password
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return
        username = os.environ.get("MIDLAB_ADMIN_USER", "admin")
        password = os.environ.get("MIDLAB_ADMIN_PASS", "admin")
        is_default = (username == "admin" and password == "admin")
        db.add(User(
            username=username,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
            force_password_change=is_default,
        ))
        db.commit()
        logging.getLogger("web_console").warning(
            "Bootstrap admin created: username=%s force_password_change=%s",
            username, is_default,
        )
    finally:
        db.close()
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_bootstrap_admin.py -v`
Expected: PASS.

- [ ] **Step 5: Wire into startup**

In `services/web_console/api.py` around line 128 (`async def _startup`), append inside the function body:
```python
    from lib.db import bootstrap_admin_if_empty
    bootstrap_admin_if_empty()
```

- [ ] **Step 6: Commit**

```bash
git add lib/db.py services/web_console/api.py tests/test_bootstrap_admin.py
git commit -m "auth: bootstrap initial admin from env on startup"
```

---

### Task 8: Login / logout / change-password endpoints + templates

**Files:**
- Modify: `services/web_console/api.py`
- Create: `services/web_console/templates/login.html`
- Create: `services/web_console/templates/change_password.html`
- Test: `tests/test_login_flow.py` (NEW)

- [ ] **Step 1: Write failing test**

Create `tests/test_login_flow.py`:
```python
from fastapi.testclient import TestClient
from services.web_console.api import app
from lib.db import SessionLocal, User
from lib.auth import hash_password, SESSION_COOKIE_NAME

def _make_user(username="alice", role="admin", password="pw1234"):
    db = SessionLocal()
    db.query(User).filter(User.username == username).delete()
    db.add(User(username=username, password_hash=hash_password(password),
                role=role, is_active=True))
    db.commit(); db.close()

def test_login_success_sets_cookie(tmp_db):
    _make_user()
    c = TestClient(app)
    r = c.post("/login", data={"username": "alice", "password": "pw1234"},
               follow_redirects=False)
    assert r.status_code in (302, 303)
    assert SESSION_COOKIE_NAME in r.cookies

def test_login_wrong_password(tmp_db):
    _make_user()
    c = TestClient(app)
    r = c.post("/login", data={"username": "alice", "password": "bad"},
               follow_redirects=False)
    assert r.status_code == 401

def test_logout_clears_cookie(tmp_db):
    _make_user(username="bob")
    c = TestClient(app)
    r = c.post("/login", data={"username": "bob", "password": "pw1234"},
               follow_redirects=False)
    token = r.cookies[SESSION_COOKIE_NAME]
    r2 = c.post("/logout", cookies={SESSION_COOKIE_NAME: token},
                follow_redirects=False)
    assert r2.status_code in (302, 303)
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest tests/test_login_flow.py -v`
Expected: 404 / failures.

- [ ] **Step 3: Create login template**

Create `services/web_console/templates/login.html`:
```html
<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <title>MidLab — Login</title>
  <link rel="stylesheet" href="/static/style.css">
  <style>
    body { display:flex; align-items:center; justify-content:center; min-height:100vh;
           background:#f5f5f7; font-family:-apple-system,BlinkMacSystemFont,sans-serif; }
    .card { background:#fff; padding:32px; border-radius:14px;
            box-shadow:0 2px 20px rgba(0,0,0,.06); width:340px; }
    h1 { margin:0 0 18px; font-size:20px; font-weight:600; }
    label { display:block; font-size:13px; color:#444; margin-top:10px; }
    input { width:100%; padding:10px; border:1px solid #d2d2d7; border-radius:8px;
            font-size:14px; margin-top:4px; box-sizing:border-box; }
    button { width:100%; margin-top:18px; padding:11px; border:0; border-radius:9px;
             background:#0071e3; color:#fff; font-weight:600; cursor:pointer; }
    .err { color:#b00020; margin-top:12px; font-size:13px; }
  </style>
</head>
<body>
  <form class="card" method="POST" action="/login">
    <h1>MidLab Console</h1>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <label>Username<input name="username" autofocus required></label>
    <label>Password<input name="password" type="password" required></label>
    <input type="hidden" name="next" value="{{ next or '/' }}">
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
```

- [ ] **Step 4: Create change_password template**

Create `services/web_console/templates/change_password.html` (same chrome as login):
```html
<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <title>MidLab — Change Password</title>
  <link rel="stylesheet" href="/static/style.css">
  <style>
    body { display:flex; align-items:center; justify-content:center; min-height:100vh;
           background:#f5f5f7; font-family:-apple-system,BlinkMacSystemFont,sans-serif; }
    .card { background:#fff; padding:32px; border-radius:14px;
            box-shadow:0 2px 20px rgba(0,0,0,.06); width:380px; }
    h1 { margin:0 0 18px; font-size:20px; font-weight:600; }
    label { display:block; font-size:13px; color:#444; margin-top:10px; }
    input { width:100%; padding:10px; border:1px solid #d2d2d7; border-radius:8px;
            font-size:14px; margin-top:4px; box-sizing:border-box; }
    button { width:100%; margin-top:18px; padding:11px; border:0; border-radius:9px;
             background:#0071e3; color:#fff; font-weight:600; cursor:pointer; }
    .err { color:#b00020; margin-top:12px; font-size:13px; }
  </style>
</head>
<body>
  <form class="card" method="POST" action="/change-password">
    <h1>Change Password</h1>
    <p style="font-size:13px;color:#666">Default password must be changed before continuing.</p>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <label>Current password<input name="current" type="password" required></label>
    <label>New password<input name="new" type="password" minlength="8" required></label>
    <label>Confirm new password<input name="confirm" type="password" minlength="8" required></label>
    <button type="submit">Update</button>
  </form>
</body>
</html>
```

- [ ] **Step 5: Add imports + endpoints to `services/web_console/api.py`**

Add near top imports:
```python
from fastapi import Form, Depends
from fastapi.responses import RedirectResponse
from datetime import datetime
from lib.auth import (
    verify_password, hash_password, create_session, revoke_session,
    load_session_user, audit, SESSION_COOKIE_NAME, SESSION_TTL_HOURS,
    require_role,
)
from lib.db import User
```

Add helper + endpoints (place after the existing `_verify_api_key` helper):
```python
_LOGIN_FAIL_COUNTER: dict[str, list] = {}

def _rate_limit_login(ip: str) -> bool:
    import time
    now = time.time()
    bucket = _LOGIN_FAIL_COUNTER.setdefault(ip, [])
    bucket[:] = [t for t in bucket if now - t < 60.0]
    return len(bucket) >= 5

def _record_login_fail(ip: str) -> None:
    import time
    _LOGIN_FAIL_COUNTER.setdefault(ip, []).append(time.time())


@app.get("/login", response_class=HTMLResponse)
async def page_login(request: Request, next: str = "/"):
    return _templates.TemplateResponse(request, "login.html",
                                       {"next": next, "error": None})


@app.post("/login")
async def do_login(request: Request,
                   username: str = Form(...),
                   password: str = Form(...),
                   next: str = Form("/")):
    from lib.db import SessionLocal
    ip = request.client.host if request.client else None

    if ip and _rate_limit_login(ip):
        return _templates.TemplateResponse(request, "login.html",
            {"next": next, "error": "Too many attempts; try again in a minute."},
            status_code=429)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username,
                                     User.is_active == True).first()  # noqa: E712
        if user is None or not verify_password(password, user.password_hash):
            if ip:
                _record_login_fail(ip)
            audit(db, user=None, action="login.fail", target=username, request=request)
            return _templates.TemplateResponse(request, "login.html",
                {"next": next, "error": "Invalid credentials."},
                status_code=401)
        token = create_session(db, user.id, ip=ip)
        user.last_login_at = datetime.utcnow()
        db.commit()
        audit(db, user=user, action="login.success", request=request)
        redirect_to = "/change-password" if user.force_password_change else (next or "/")
        resp = RedirectResponse(url=redirect_to, status_code=303)
        resp.set_cookie(SESSION_COOKIE_NAME, token,
                        max_age=SESSION_TTL_HOURS * 3600,
                        httponly=True, samesite="lax")
        return resp
    finally:
        db.close()


@app.post("/logout")
async def do_logout(request: Request):
    from lib.db import SessionLocal
    token = request.cookies.get(SESSION_COOKIE_NAME)
    db = SessionLocal()
    try:
        user = load_session_user(db, token)
        if token:
            revoke_session(db, token)
        audit(db, user=user, action="logout", request=request)
    finally:
        db.close()
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


@app.get("/change-password", response_class=HTMLResponse)
async def page_change_password(request: Request):
    return _templates.TemplateResponse(request, "change_password.html", {"error": None})


@app.post("/change-password")
async def do_change_password(request: Request,
                             current: str = Form(...),
                             new: str = Form(...),
                             confirm: str = Form(...)):
    from lib.db import SessionLocal
    token = request.cookies.get(SESSION_COOKIE_NAME)
    db = SessionLocal()
    try:
        user = load_session_user(db, token)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if new != confirm:
            return _templates.TemplateResponse(request, "change_password.html",
                {"error": "New passwords do not match."}, status_code=400)
        if not verify_password(current, user.password_hash):
            return _templates.TemplateResponse(request, "change_password.html",
                {"error": "Current password incorrect."}, status_code=401)
        if len(new) < 8:
            return _templates.TemplateResponse(request, "change_password.html",
                {"error": "Password must be at least 8 characters."}, status_code=400)
        u = db.query(User).filter(User.id == user.id).first()
        u.password_hash = hash_password(new)
        u.force_password_change = False
        db.commit()
        audit(db, user=u, action="password.change", request=request)
    finally:
        db.close()
    return RedirectResponse(url="/", status_code=303)
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_login_flow.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add services/web_console/api.py services/web_console/templates/login.html services/web_console/templates/change_password.html tests/test_login_flow.py
git commit -m "auth: login/logout/change-password endpoints + templates"
```

---

### Task 9: Wrap existing mutating endpoints with `require_role('admin')` + audit

**Files:**
- Modify: `services/web_console/api.py`
- Test: `tests/test_role_enforcement.py` (NEW)

- [ ] **Step 1: Write failing test**

Create `tests/test_role_enforcement.py`:
```python
from fastapi.testclient import TestClient
from services.web_console.api import app
from lib.db import SessionLocal, User, AuditLog
from lib.auth import hash_password, create_session, SESSION_COOKIE_NAME

def _seed(role):
    db = SessionLocal()
    db.query(User).filter(User.username == f"u_{role}").delete()
    u = User(username=f"u_{role}", password_hash=hash_password("pw"),
             role=role, is_active=True)
    db.add(u); db.commit(); db.refresh(u)
    tok = create_session(db, u.id, ip=None)
    db.close()
    return tok

def test_viewer_cannot_stop_service(tmp_db):
    tok = _seed("viewer")
    c = TestClient(app)
    r = c.post("/api/services/tcp_1/stop", cookies={SESSION_COOKIE_NAME: tok})
    assert r.status_code == 401

def test_admin_can_call_stop_audited(tmp_db, monkeypatch):
    from services.web_console import api as api_mod
    class FakeWD:
        def stop_service(self, name): return True
        def status(self, name): return {"state": "stopped"}
    monkeypatch.setattr(api_mod, "watchdog", FakeWD(), raising=False)
    tok = _seed("admin")
    c = TestClient(app)
    r = c.post("/api/services/tcp_1/stop", cookies={SESSION_COOKIE_NAME: tok})
    assert r.status_code == 200
    db = SessionLocal()
    try:
        rows = db.query(AuditLog).filter(AuditLog.action == "service.stop").all()
        assert any(r.target == "tcp_1" for r in rows)
    finally:
        db.close()
```

- [ ] **Step 2: Run, expect failure on admin case**

Run: `pytest tests/test_role_enforcement.py -v`
Expected: tests fail because endpoints still use `_verify_api_key`.

- [ ] **Step 3: Replace `_verify_api_key` checks with `require_role`**

For each mutating endpoint in `services/web_console/api.py` (search for every line `_verify_api_key(x_api_key)`), transform the signature.

Before:
```python
@app.post("/api/services/{name}/stop", response_model=MessageResponse)
async def stop_service(name: str, x_api_key: str = Header(None)):
    _verify_api_key(x_api_key)
    ...
    return MessageResponse(message="Service stopped", ok=True)
```

After:
```python
@app.post("/api/services/{name}/stop", response_model=MessageResponse)
async def stop_service(name: str, request: Request,
                       user: User = Depends(require_role("admin"))):
    ...
    from lib.db import SessionLocal
    db = SessionLocal()
    try:
        audit(db, user=user, action="service.stop", target=name, request=request)
    finally:
        db.close()
    return MessageResponse(message="Service stopped", ok=True)
```

Audit-action mapping to apply:
| Endpoint | action | target |
|---|---|---|
| `start_service` | `service.start` | name |
| `stop_service` | `service.stop` | name |
| `restart_service` | `service.restart` | name |
| `toggle_auto_restart` | `service.auto_restart_toggle` | name |
| `create_instrument` | `instrument.create` | new instrument name or id |
| `update_instrument` | `instrument.update` | instrument id |
| `delete_instrument` | `instrument.delete` | instrument id |
| `verify_with_lis` | `instrument.verify_lis` | instrument id |
| `test_connection` | `instrument.test_connection` | instrument id |
| `force_broadcast` | `instrument.force_broadcast` | instrument id |
| `hot_swap_protocol` | `instrument.protocol_swap` | instrument id |
| `update_settings` | `settings.update` | None |
| `test_lis_connection` | `settings.test_lis` | None |
| any results retry endpoint | `result.retry` | result id |
| any orders retry / cancel endpoints | `order.retry` / `order.cancel` | order id |

For read endpoints (`list_services`, `list_instruments`, `get_instrument`, `list_protocols`, `get_settings`, `get_logs`, `stream_logs`, results list, orders list), replace the `x_api_key` parameter + `_verify_api_key(...)` call with `user: User = Depends(require_role("admin", "viewer"))`.

**Keep** `_verify_api_key` defined — `require_role` consults `x-api-key` internally so existing clients with a header still work. Don't delete the helper to avoid breaking any callers that import it.

- [ ] **Step 4: Run tests**

Run: `pytest tests/ -v`
Expected: green (modulo unrelated tests that may need cookie injection — fix them by either adding session cookie or setting `MIDLAB_API_KEY` env in `tests/conftest.py`).

- [ ] **Step 5: Commit**

```bash
git add services/web_console/api.py tests/test_role_enforcement.py
git commit -m "auth: enforce role on all endpoints + audit mutating actions"
```

---

### Task 10: User CRUD endpoints + page

**Files:**
- Modify: `services/web_console/api.py`
- Create: `services/web_console/templates/users.html`
- Test: `tests/test_users_api.py` (NEW)

- [ ] **Step 1: Write failing test**

Create `tests/test_users_api.py`:
```python
from fastapi.testclient import TestClient
from services.web_console.api import app
from lib.db import SessionLocal, User
from lib.auth import hash_password, create_session, SESSION_COOKIE_NAME

def _seed_admin():
    db = SessionLocal()
    db.query(User).filter(User.username == "admin1").delete()
    u = User(username="admin1", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(u); db.commit(); db.refresh(u)
    tok = create_session(db, u.id, ip=None)
    db.close()
    return u.id, tok

def test_create_user(tmp_db):
    _, tok = _seed_admin()
    c = TestClient(app)
    r = c.post("/api/users",
               json={"username": "new_viewer", "password": "pw12345!", "role": "viewer"},
               cookies={SESSION_COOKIE_NAME: tok})
    assert r.status_code == 201
    assert r.json()["username"] == "new_viewer"

def test_delete_self_blocked(tmp_db):
    uid, tok = _seed_admin()
    c = TestClient(app)
    r = c.delete(f"/api/users/{uid}", cookies={SESSION_COOKIE_NAME: tok})
    assert r.status_code == 400
```

- [ ] **Step 2: Run, expect 404**

Run: `pytest tests/test_users_api.py -v`
Expected: failures.

- [ ] **Step 3: Add Pydantic schemas + endpoints**

In `services/web_console/api.py`, near existing Pydantic models:
```python
class UserCreate(BaseModel):
    username: str
    password: str
    role: str

class UserUpdate(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None

class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    last_login_at: Optional[datetime] = None
```

Add endpoints:
```python
@app.get("/users", response_class=HTMLResponse)
async def page_users(request: Request, user: User = Depends(require_role("admin"))):
    return _templates.TemplateResponse(request, "users.html",
                                       {"active_page": "users", "current_user": user})


@app.get("/api/users", response_model=list[UserResponse])
async def list_users(request: Request, user: User = Depends(require_role("admin"))):
    from lib.db import SessionLocal
    db = SessionLocal()
    try:
        return [UserResponse(id=r.id, username=r.username, role=r.role,
                             is_active=r.is_active, last_login_at=r.last_login_at)
                for r in db.query(User).order_by(User.id).all()]
    finally:
        db.close()


@app.post("/api/users", response_model=UserResponse, status_code=201)
async def create_user(body: UserCreate, request: Request,
                      user: User = Depends(require_role("admin"))):
    from lib.db import SessionLocal
    if body.role not in ("admin", "viewer"):
        raise HTTPException(400, "Invalid role")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == body.username).first():
            raise HTTPException(409, "Username already exists")
        u = User(username=body.username, password_hash=hash_password(body.password),
                 role=body.role, is_active=True)
        db.add(u); db.commit(); db.refresh(u)
        audit(db, user=user, action="user.create", target=body.username,
              metadata={"role": body.role}, request=request)
        return UserResponse(id=u.id, username=u.username, role=u.role,
                            is_active=u.is_active, last_login_at=None)
    finally:
        db.close()


@app.put("/api/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, body: UserUpdate, request: Request,
                      user: User = Depends(require_role("admin"))):
    from lib.db import SessionLocal, Session as DbSession
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if u is None:
            raise HTTPException(404, "User not found")
        changes = {}
        if body.role is not None:
            if body.role not in ("admin", "viewer"):
                raise HTTPException(400, "Invalid role")
            u.role = body.role; changes["role"] = body.role
        if body.password is not None:
            if len(body.password) < 8:
                raise HTTPException(400, "Password must be at least 8 characters")
            u.password_hash = hash_password(body.password); changes["password"] = "***"
        if body.is_active is not None:
            u.is_active = body.is_active; changes["is_active"] = body.is_active
            if body.is_active is False:
                db.query(DbSession).filter(DbSession.user_id == u.id).delete()
        db.commit()
        audit(db, user=user, action="user.update", target=u.username,
              metadata=changes, request=request)
        return UserResponse(id=u.id, username=u.username, role=u.role,
                            is_active=u.is_active, last_login_at=u.last_login_at)
    finally:
        db.close()


@app.delete("/api/users/{user_id}", response_model=MessageResponse)
async def delete_user(user_id: int, request: Request,
                      user: User = Depends(require_role("admin"))):
    if user.id == user_id:
        raise HTTPException(400, "Cannot delete yourself")
    from lib.db import SessionLocal
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if u is None:
            raise HTTPException(404, "User not found")
        username = u.username
        db.delete(u); db.commit()
        audit(db, user=user, action="user.delete", target=username, request=request)
        return MessageResponse(message="User deleted", ok=True)
    finally:
        db.close()
```

- [ ] **Step 4: Create `users.html`**

Open `services/web_console/templates/services.html` and copy the page chrome (head, nav, container styles) verbatim. Save as `users.html` and replace the main content with the block below. **All dynamic text uses `textContent` and DOM construction — no `innerHTML` with concatenated user data.**

```html
<!-- begin users page body -->
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <h2 style="margin:0">Users</h2>
    <button id="btnNewUser" class="btn-primary">Add user</button>
  </div>
  <table class="data-table" id="usersTable" style="margin-top:14px;width:100%">
    <thead><tr><th>Username</th><th>Role</th><th>Active</th><th>Last login</th><th></th></tr></thead>
    <tbody></tbody>
  </table>
</div>

<dialog id="userDialog">
  <form method="dialog" style="display:flex;flex-direction:column;gap:10px;min-width:320px;padding:18px">
    <h3 id="dlgTitle">New user</h3>
    <label>Username <input id="dlgUsername" required></label>
    <label>Role
      <select id="dlgRole"><option value="viewer">viewer</option><option value="admin">admin</option></select>
    </label>
    <label>Password <input id="dlgPassword" type="password" minlength="8" placeholder="leave blank to keep on edit"></label>
    <label><input type="checkbox" id="dlgActive" checked> Active</label>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button value="cancel">Cancel</button>
      <button id="dlgSave" value="save" class="btn-primary">Save</button>
    </div>
  </form>
</dialog>

<script>
function cell(text) {
  const td = document.createElement('td');
  td.textContent = text == null ? '' : String(text);
  return td;
}

function actionsCell(u) {
  const td = document.createElement('td');
  const editBtn = document.createElement('button');
  editBtn.textContent = 'Edit';
  editBtn.addEventListener('click', () => openEdit(u));
  const delBtn = document.createElement('button');
  delBtn.textContent = 'Delete';
  delBtn.style.marginLeft = '6px';
  delBtn.addEventListener('click', () => del(u.id, u.username));
  td.appendChild(editBtn);
  td.appendChild(delBtn);
  return td;
}

async function load() {
  const r = await fetch('/api/users');
  if (!r.ok) { alert('Failed to load users'); return; }
  const rows = await r.json();
  const tbody = document.querySelector('#usersTable tbody');
  tbody.replaceChildren();
  for (const u of rows) {
    const tr = document.createElement('tr');
    tr.appendChild(cell(u.username));
    tr.appendChild(cell(u.role));
    tr.appendChild(cell(u.is_active ? 'yes' : 'no'));
    tr.appendChild(cell(u.last_login_at || ''));
    tr.appendChild(actionsCell(u));
    tbody.appendChild(tr);
  }
}

async function del(id, name) {
  if (!confirm(`Delete user ${name}?`)) return;
  const r = await fetch(`/api/users/${id}`, { method: 'DELETE' });
  if (!r.ok) { const j = await r.json().catch(() => ({})); alert(j.detail || 'Failed'); return; }
  load();
}

function openEdit(u) {
  const dlg = document.getElementById('userDialog');
  dlg.dataset.id = u.id;
  document.getElementById('dlgTitle').textContent = 'Edit user';
  const userInput = document.getElementById('dlgUsername');
  userInput.value = u.username;
  userInput.disabled = true;
  document.getElementById('dlgRole').value = u.role;
  document.getElementById('dlgActive').checked = u.is_active;
  document.getElementById('dlgPassword').value = '';
  dlg.showModal();
}

document.getElementById('btnNewUser').addEventListener('click', () => {
  const dlg = document.getElementById('userDialog');
  delete dlg.dataset.id;
  document.getElementById('dlgTitle').textContent = 'New user';
  const userInput = document.getElementById('dlgUsername');
  userInput.value = '';
  userInput.disabled = false;
  document.getElementById('dlgRole').value = 'viewer';
  document.getElementById('dlgPassword').value = '';
  document.getElementById('dlgActive').checked = true;
  dlg.showModal();
});

document.getElementById('dlgSave').addEventListener('click', async (e) => {
  e.preventDefault();
  const dlg = document.getElementById('userDialog');
  const id = dlg.dataset.id;
  const role = document.getElementById('dlgRole').value;
  const password = document.getElementById('dlgPassword').value;
  const is_active = document.getElementById('dlgActive').checked;
  let r;
  if (id) {
    const patch = { role, is_active };
    if (password) patch.password = password;
    r = await fetch(`/api/users/${id}`, { method: 'PUT',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) });
  } else {
    const username = document.getElementById('dlgUsername').value;
    r = await fetch('/api/users', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, role }) });
  }
  if (!r.ok) { const j = await r.json().catch(() => ({})); alert(j.detail || 'Failed'); return; }
  dlg.close();
  load();
});

load();
</script>
<!-- end users page body -->
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_users_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/web_console/api.py services/web_console/templates/users.html tests/test_users_api.py
git commit -m "auth: user CRUD endpoints + users.html admin page"
```

---

### Task 11: Audit log page + API

**Files:**
- Modify: `services/web_console/api.py`
- Create: `services/web_console/templates/audit.html`
- Test: append to `tests/test_role_enforcement.py`

- [ ] **Step 1: Append failing test**

Append to `tests/test_role_enforcement.py`:
```python
def test_audit_endpoint_admin_only(tmp_db):
    tok = _seed("viewer")
    c = TestClient(app)
    r = c.get("/api/audit", cookies={SESSION_COOKIE_NAME: tok})
    assert r.status_code == 401
```

- [ ] **Step 2: Add Pydantic + endpoints**

In `services/web_console/api.py`:
```python
class AuditEntry(BaseModel):
    id: int
    username: str
    action: str
    target: Optional[str] = None
    metadata: Optional[dict] = None
    ip_address: Optional[str] = None
    logged_at: datetime


@app.get("/audit", response_class=HTMLResponse)
async def page_audit(request: Request, user: User = Depends(require_role("admin"))):
    return _templates.TemplateResponse(request, "audit.html",
                                       {"active_page": "audit", "current_user": user})


@app.get("/api/audit", response_model=list[AuditEntry])
async def list_audit(request: Request,
                     user: User = Depends(require_role("admin")),
                     limit: int = 200,
                     action: Optional[str] = None,
                     username: Optional[str] = None):
    from lib.db import SessionLocal, AuditLog
    db = SessionLocal()
    try:
        q = db.query(AuditLog).order_by(AuditLog.id.desc())
        if action: q = q.filter(AuditLog.action == action)
        if username: q = q.filter(AuditLog.username == username)
        q = q.limit(min(limit, 1000))
        return [AuditEntry(id=r.id, username=r.username, action=r.action,
                           target=r.target, metadata=r.extra_metadata,
                           ip_address=r.ip_address, logged_at=r.logged_at)
                for r in q.all()]
    finally:
        db.close()
```

- [ ] **Step 3: Create `audit.html`**

Copy chrome from `services.html`. Replace body with (DOM-safe — no innerHTML):
```html
<!-- begin audit page body -->
<div class="card">
  <h2 style="margin:0 0 12px">Audit Log</h2>
  <div style="display:flex;gap:8px;margin-bottom:10px">
    <input id="fAction" placeholder="action (e.g. service.stop)">
    <input id="fUser" placeholder="username">
    <button id="btnFilter">Filter</button>
  </div>
  <table class="data-table" id="auditTable" style="width:100%">
    <thead><tr><th>Time</th><th>User</th><th>Action</th><th>Target</th><th>IP</th><th>Metadata</th></tr></thead>
    <tbody></tbody>
  </table>
</div>
<script>
function cell(text) {
  const td = document.createElement('td');
  td.textContent = text == null ? '' : String(text);
  return td;
}
function codeCell(obj) {
  const td = document.createElement('td');
  if (obj) {
    const code = document.createElement('code');
    code.textContent = JSON.stringify(obj);
    td.appendChild(code);
  }
  return td;
}
async function load() {
  const params = new URLSearchParams();
  const a = document.getElementById('fAction').value.trim();
  const u = document.getElementById('fUser').value.trim();
  if (a) params.set('action', a);
  if (u) params.set('username', u);
  const r = await fetch('/api/audit?' + params.toString());
  if (!r.ok) { alert('Failed to load audit'); return; }
  const rows = await r.json();
  const tbody = document.querySelector('#auditTable tbody');
  tbody.replaceChildren();
  for (const x of rows) {
    const tr = document.createElement('tr');
    tr.appendChild(cell(x.logged_at));
    tr.appendChild(cell(x.username));
    tr.appendChild(cell(x.action));
    tr.appendChild(cell(x.target));
    tr.appendChild(cell(x.ip_address));
    tr.appendChild(codeCell(x.metadata));
    tbody.appendChild(tr);
  }
}
document.getElementById('btnFilter').addEventListener('click', load);
load();
</script>
<!-- end audit page body -->
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_role_enforcement.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/web_console/api.py services/web_console/templates/audit.html tests/test_role_enforcement.py
git commit -m "auth: audit log page + API endpoint"
```

---

### Task 12: Header widget (whoami) + role-aware admin-only hiding in existing templates

**Files:**
- Modify: `services/web_console/api.py` (add `/api/whoami`)
- Modify: `services/web_console/templates/services.html`, `logs.html`, `protocols.html`, `results.html`, `orders.html`, and the dashboard template (whichever is at `/`)
- Modify: `services/web_console/static/style.css` (or inline style)

- [ ] **Step 1: Add `/api/whoami` endpoint**

In `services/web_console/api.py`:
```python
class WhoAmI(BaseModel):
    username: str
    role: str

@app.get("/api/whoami", response_model=WhoAmI)
async def whoami(user: User = Depends(require_role("admin", "viewer"))):
    return WhoAmI(username=user.username, role=user.role)
```

- [ ] **Step 2: Add header CSS**

In `services/web_console/static/style.css` (or inline in each template if no global file), append:
```css
body.role-viewer .admin-only { display: none !important; }
.user-widget { position: absolute; top: 14px; right: 24px; font-size: 13px;
               display: flex; gap: 12px; align-items: center; }
.btn-link { background: none; border: 0; color: #0071e3; cursor: pointer; padding: 0; }
```

- [ ] **Step 3: Inject user widget into each existing template**

For each of `services.html`, `logs.html`, `protocols.html`, `results.html`, `orders.html`, dashboard:

Locate the top of `<body>` (after any nav/header markup) and insert:
```html
<div class="user-widget">
  <span id="userBadge"></span>
  <form method="post" action="/logout" style="margin:0">
    <button class="btn-link" type="submit">Logout</button>
  </form>
</div>
<script>
fetch('/api/whoami').then(r => r.ok ? r.json() : null).then(u => {
  if (!u) { location.href = '/login'; return; }
  document.getElementById('userBadge').textContent = u.username + ' (' + u.role + ')';
  document.body.classList.add('role-' + u.role);
});
</script>
```

- [ ] **Step 4: Add `admin-only` class to mutating buttons**

In `services.html`: add `admin-only` to the start/stop/restart/auto-restart toggle buttons.
In `logs.html`: no admin-only buttons typically — verify nothing needs hiding.
In `protocols.html`: add `admin-only` to the hot-swap dropdown action button.
In `instruments` template (the page that lists/edits instruments — find via `page_instruments` route): add `admin-only` to "Add", "Edit", "Delete", "Test Connection", "Force Broadcast" buttons.
In `results.html`: add `admin-only` to any retry buttons.
In `orders.html`: add `admin-only` to retry/cancel buttons.

Example:
Before:
```html
<button onclick="stopService('tcp_1')">Stop</button>
```
After:
```html
<button class="admin-only" onclick="stopService('tcp_1')">Stop</button>
```

- [ ] **Step 5: Manual smoke**

Start web console, login as `admin` — verify buttons present.
Logout, login as a `viewer` user — verify mutating buttons hidden and direct API requests return 401.

- [ ] **Step 6: Commit**

```bash
git add services/web_console/api.py services/web_console/templates/ services/web_console/static/
git commit -m "ui: user widget + role-aware admin-only hiding"
```

---

### Task 13: `install.sh` admin bootstrap + auth migration

**Files:**
- Modify: `scripts/install.sh`

- [ ] **Step 1: Read existing `install.sh`**

Open `scripts/install.sh`. Find:
- The section that prompts for / writes env vars (look for `DB_USER`, `DB_PASS`, similar prompts).
- The section that runs SQL migrations (look for `mysql -u ... < /opt/midlab/scripts/migrate_*.sql` — note the existing migration `migrate_protocol_to_varchar.sql`).

- [ ] **Step 2: Add admin credential prompts**

Insert before env-write section:
```bash
if [ -z "${MIDLAB_ADMIN_USER:-}" ]; then
  read -rp "Initial admin username [admin]: " MIDLAB_ADMIN_USER
  MIDLAB_ADMIN_USER="${MIDLAB_ADMIN_USER:-admin}"
fi
if [ -z "${MIDLAB_ADMIN_PASS:-}" ]; then
  read -rsp "Initial admin password (leave empty to default 'admin', will require change on first login): " MIDLAB_ADMIN_PASS
  echo
  MIDLAB_ADMIN_PASS="${MIDLAB_ADMIN_PASS:-admin}"
fi
export MIDLAB_ADMIN_USER MIDLAB_ADMIN_PASS
```

In the env-write block (systemd unit `Environment=` lines or `/etc/midlab/env`):
```
MIDLAB_ADMIN_USER=${MIDLAB_ADMIN_USER}
MIDLAB_ADMIN_PASS=${MIDLAB_ADMIN_PASS}
```

- [ ] **Step 3: Apply auth migration**

After the existing migration runs, add a sibling line matching the same pattern (mariadb/mysql client). Example, matching the project's current style:
```bash
"$MYSQL_BIN" -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" < /opt/midlab/scripts/migrate_add_auth.sql
```
(Use the same variable names and binary detection logic the existing migration step uses.)

- [ ] **Step 4: Commit**

```bash
git add scripts/install.sh
git commit -m "install: prompt admin credentials + apply auth migration"
```

---

## Phase B — Raw Comm Logs

### Task 14: `lib/comm_logger.py` decoder unit

**Files:**
- Create: `lib/comm_logger.py`
- Test: `tests/test_comm_logger.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_comm_logger.py`:
```python
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
```

- [ ] **Step 2: Run, expect ImportError**

Run: `pytest tests/test_comm_logger.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement decoder**

Create `lib/comm_logger.py`:
```python
"""Per-instrument bidirectional raw byte logger to tcp_<id>.comm.log.

Format: {timestamp}.{ms} [tcp_<id>] {direction} {decoded}
Where decoded maps control chars to <TAG> and non-printable bytes to \\xNN.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Dict

LOG_DIR = "/var/log/midlab"
ROTATE_BYTES = 50 * 1024 * 1024
ROTATE_BACKUPS = 5

_CTRL = {
    0x02: "<STX>", 0x03: "<ETX>", 0x04: "<EOT>", 0x05: "<ENQ>",
    0x06: "<ACK>", 0x15: "<NAK>", 0x17: "<ETB>",
    0x0D: "<CR>", 0x0A: "<LF>",
}


def _decode_for_log(data: bytes) -> str:
    out = []
    for b in data:
        if b in _CTRL:
            out.append(_CTRL[b])
        elif 0x20 <= b <= 0x7E:
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return "".join(out)
```

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_comm_logger.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/comm_logger.py tests/test_comm_logger.py
git commit -m "comm-log: decoder for control chars + hex fallback"
```

---

### Task 15: `CommLogger` class with rotating file handler

**Files:**
- Modify: `lib/comm_logger.py`
- Test: append to `tests/test_comm_logger.py`

- [ ] **Step 1: Append failing test**

Append to `tests/test_comm_logger.py`:
```python
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
```

- [ ] **Step 2: Run, expect AttributeError**

Run: `pytest tests/test_comm_logger.py -v`
Expected: AttributeError on `CommLogger`.

- [ ] **Step 3: Implement class**

Append to `lib/comm_logger.py`:
```python
class CommLogger:
    _cache: Dict[int, "CommLogger"] = {}

    def __init__(self, instrument_id: int):
        self.instrument_id = instrument_id
        self._logger = logging.getLogger(f"midlab.comm.tcp_{instrument_id}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._warned = False
        if not self._logger.handlers:
            try:
                os.makedirs(LOG_DIR, exist_ok=True)
                handler = RotatingFileHandler(
                    os.path.join(LOG_DIR, f"tcp_{instrument_id}.comm.log"),
                    maxBytes=ROTATE_BYTES,
                    backupCount=ROTATE_BACKUPS,
                )
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s.%(msecs)03d [tcp_" + str(instrument_id) + "] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                ))
                self._logger.addHandler(handler)
            except Exception as exc:
                logging.getLogger("web_console").warning(
                    "CommLogger init failed for instrument %s: %s", instrument_id, exc
                )

    @classmethod
    def for_instrument(cls, instrument_id: int) -> "CommLogger":
        if instrument_id not in cls._cache:
            cls._cache[instrument_id] = cls(instrument_id)
        return cls._cache[instrument_id]

    def rx(self, data: bytes) -> None:
        if not data:
            return
        try:
            self._logger.info("← RX %s", _decode_for_log(data))
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logging.getLogger("web_console").warning(
                    "CommLogger rx failed (instrument %s): %s", self.instrument_id, exc
                )

    def tx(self, data: bytes) -> None:
        if not data:
            return
        try:
            self._logger.info("→ TX %s", _decode_for_log(data))
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logging.getLogger("web_console").warning(
                    "CommLogger tx failed (instrument %s): %s", self.instrument_id, exc
                )
```

- [ ] **Step 4: Run, expect PASS**

Run: `pytest tests/test_comm_logger.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/comm_logger.py tests/test_comm_logger.py
git commit -m "comm-log: CommLogger singleton with rotating file handler"
```

---

### Task 16: Instrument `services/tcp_socket/` I/O call sites

**Files:**
- Modify: `services/tcp_socket/receiver.py` (lines 129, 171, 346)
- Modify: `services/tcp_socket/broadcast_worker.py` (lines 261, 277, 295, 316, 345, 369)
- Modify: `services/tcp_socket/query_handler.py` (lines 320, 335, 348, 362, 383)
- Modify: `services/tcp_socket/service.py` (line 308)

(Line numbers from the spec scan. Re-grep before editing: `grep -n "reader.read\|writer.write" services/tcp_socket/*.py`.)

- [ ] **Step 1: Attach `CommLogger` to each class**

For each class with TCP I/O (`Receiver`, `BroadcastWorker`, `QueryHandler`, the connection handler in `service.py`):
- Confirm the class already has `instrument_id` (search class init).
- Add to top of file imports:
  ```python
  from lib.comm_logger import CommLogger
  ```
- In `__init__`, after `self._instrument_id = ...` (or wherever `instrument_id` is set), add:
  ```python
  self._comm = CommLogger.for_instrument(instrument_id)
  ```
  Use `self._instrument_id` if that's the existing attribute name.

If `service.py`'s connection handler does not already have `instrument_id` accessible, pass it from the surrounding scope where the handler is constructed.

- [ ] **Step 2: Wrap receives**

For each `await reader.read(...)` or `await self._reader.read(...)` site:

Before:
```python
data = await reader.read(READ_BUFFER_SIZE)
```
After:
```python
data = await reader.read(READ_BUFFER_SIZE)
if data:
    self._comm.rx(data)
```

For `asyncio.wait_for(self._reader.read(...), ...)` patterns, wrap the awaited result the same way after assignment.

- [ ] **Step 3: Wrap writes**

For each `writer.write(payload)` or `self._writer.write(payload)` site:

Before:
```python
writer.write(bytes([ASTM_ACK]))
```
After:
```python
self._comm.tx(bytes([ASTM_ACK]))
writer.write(bytes([ASTM_ACK]))
```

Apply to ALL sites listed for this task.

- [ ] **Step 4: Manual integration smoke**

Bring up one TCP service for an instrument, run `scripts/cobas_test_sender.py` against it, then:
```bash
tail -f /var/log/midlab/tcp_<id>.comm.log
```
Verify lines like `← RX <ENQ>`, `→ TX <ACK>`, `← RX <STX>...<CR><ETX>...<CR><LF>`, `← RX <EOT>` appear.

- [ ] **Step 5: Commit**

```bash
git add services/tcp_socket/
git commit -m "tcp_socket: log raw bidirectional bytes per instrument via CommLogger"
```

---

### Task 17: Web Console resolver for `__comm` virtual services

**Files:**
- Modify: `services/web_console/api.py`
- Test: `tests/test_logs_comm_resolver.py` (NEW)

- [ ] **Step 1: Write failing test**

Create `tests/test_logs_comm_resolver.py`:
```python
from services.web_console.api import _resolve_log_path

def test_resolve_app_log():
    assert _resolve_log_path("tcp_3").endswith("tcp_3.log")

def test_resolve_comm_log():
    assert _resolve_log_path("tcp_3__comm").endswith("tcp_3.comm.log")

def test_resolve_other_service():
    assert _resolve_log_path("result_sender").endswith("result_sender.log")
```

- [ ] **Step 2: Run, expect ImportError**

Run: `pytest tests/test_logs_comm_resolver.py -v`
Expected: ImportError of `_resolve_log_path`.

- [ ] **Step 3: Add resolver helper**

In `services/web_console/api.py`, near the top-level helpers (e.g. just after `_verify_api_key`):
```python
LOG_DIR_PATH = "/var/log/midlab"

def _resolve_log_path(service: str) -> str:
    """Map service id to log file path. tcp_<id>__comm → tcp_<id>.comm.log."""
    if service.endswith("__comm"):
        base = service[: -len("__comm")]
        return f"{LOG_DIR_PATH}/{base}.comm.log"
    return f"{LOG_DIR_PATH}/{service}.log"
```

- [ ] **Step 4: Use resolver in `get_logs` and `stream_logs`**

In `services/web_console/api.py` at the `get_logs` (~line 985) and `stream_logs` (~line 1033) endpoints, find the spot that builds the log file path (search `/var/log/midlab` or the existing `LOG_DIR` constant in that file) and replace direct construction:

Before (illustrative):
```python
log_file = f"/var/log/midlab/{service}.log"
```
After:
```python
log_file = _resolve_log_path(service)
```

- [ ] **Step 5: Enrich `list_services` with `__comm` virtual entries**

In `list_services` (~line 295), after the existing service status loop builds the response list, append (use the existing response list variable name — e.g. `result` or `services`):

```python
from lib.db import SessionLocal, Instrument
db = SessionLocal()
try:
    for inst in db.query(Instrument).filter(Instrument.is_active == True).all():  # noqa: E712
        result.append(ServiceStatusResponse(
            name=f"tcp_{inst.id}__comm",
            state="virtual",
            pid=None,
            uptime_seconds=None,
            instrument_name=inst.name,
            display_name=f"{inst.name} — Communication",
        ))
finally:
    db.close()
```

If `ServiceStatusResponse` lacks any of `pid`, `uptime_seconds`, `instrument_name`, `display_name` as `Optional`, mark them `Optional[...] = None` in the model. (Per existing observation, `instrument_name`/`display_name` were already added.)

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_logs_comm_resolver.py -v`
Expected: PASS.

- [ ] **Step 7: Manual smoke**

Open `/logs`, verify the service dropdown contains entries `"<instrument> — Communication"`. Select one; verify SSE stream reads from `tcp_<id>.comm.log`.

- [ ] **Step 8: Commit**

```bash
git add services/web_console/api.py tests/test_logs_comm_resolver.py
git commit -m "web_console: surface tcp_<id>.comm.log via __comm virtual service id"
```

---

### Task 18: End-to-end smoke + spec status update

**Files:**
- Modify: `docs/superpowers/specs/2026-05-14-user-roles-and-comm-logs-design.md`

- [ ] **Step 1: Full pytest run**

Run: `pytest tests/ -v`
Expected: all green. Fix any regression in pre-existing tests (some may need cookie injection or `MIDLAB_API_KEY` env in fixtures).

- [ ] **Step 2: Manual end-to-end smoke**

1. Apply migration: `mysql -u ... < scripts/migrate_add_auth.sql`.
2. Start web console with default env or custom `MIDLAB_ADMIN_USER`/`MIDLAB_ADMIN_PASS`.
3. Login as default admin → forced to `/change-password`.
4. Change password; redirect to dashboard.
5. Create viewer user in `/users`; logout; login as viewer; verify mutating buttons hidden and direct API requests return 401.
6. Login as admin; stop `tcp_1`; visit `/audit` and confirm row.
7. Run `scripts/cobas_test_sender.py` (or wait for a real instrument); in `/logs`, pick `<name> — Communication` from the dropdown; verify SSE shows live ENQ/ACK/frame trace.

- [ ] **Step 3: Mark spec as implemented**

Edit `docs/superpowers/specs/2026-05-14-user-roles-and-comm-logs-design.md` and update the front-matter status:
```markdown
**Status:** Implemented 2026-05-14 — see plan docs/superpowers/plans/2026-05-14-user-roles-and-comm-logs.md
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-05-14-user-roles-and-comm-logs-design.md
git commit -m "docs: mark user-roles + comm-logs spec as implemented"
```

---

## Self-Review Summary

**Spec coverage:**
- §4.1 schema → Tasks 2, 3 ✅
- §4.2 auth lib → Tasks 4, 5, 6 ✅
- §4.3 endpoints → Tasks 8, 10, 11, 12 (whoami) ✅
- §4.4 bootstrap admin → Tasks 7, 13 ✅
- §4.5 templates → Tasks 8, 10, 11, 12 ✅
- §4.6 role matrix → Tasks 9, 10, 11 ✅
- §4.7 audited actions → Task 9 (mapping table) ✅
- §5.1–5.3 comm logger + tcp_socket → Tasks 14, 15, 16 ✅
- §5.4 web console integration → Task 17 ✅
- §8 error handling (rate limit, file write errors, session expiry) → Tasks 6, 8, 15 ✅
- §9 testing strategy → unit + integration tests in each task + Task 18 smoke ✅

**Out-of-scope items per spec §3** (not in plan, correctly): password reset email, 2FA, per-instrument permissions, comm log search UI, audit CSV export.

**Type consistency check:**
- `SESSION_COOKIE_NAME` used consistently across Tasks 4, 6, 8, 9, 10.
- `User` field names (`force_password_change`, `is_active`, `last_login_at`) consistent across Tasks 3, 7, 8, 10.
- `audit(...)` signature `(db, *, user, action, target=None, metadata=None, request=None)` consistent across Tasks 6, 8, 9, 10, 11.
- `extra_metadata` (Python attr) ↔ `metadata` (DB column) consistent in Tasks 3, 6, 10, 11.
- `CommLogger.rx`/`tx` consistent across Tasks 15, 16.
- Virtual service id pattern `tcp_<id>__comm` consistent across Tasks 17 (resolver + enrichment).

**Placeholder scan:** none — all code blocks complete, all commands concrete.

Plan complete and ready to execute.
