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
