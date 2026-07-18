"""Regression: get_latest_status_per_instrument picks the newest status
event per instrument, ignores non-status event types, returns empty dict
when there are no events.
"""
from lib.db import (
    TblLisEventQueue,
    get_latest_status_per_instrument,
)


class _SessionProxy:
    """Wrap a Session as DBManager-compatible facade."""
    def __init__(self, session):
        self._session = session

    def get_session(self):
        return self._session


def _patch_dbmanager(monkeypatch, db_session):
    import lib.db as dbmod
    monkeypatch.setattr(dbmod, "DBManager", lambda: _SessionProxy(db_session))


def test_empty_queue_returns_empty_dict(db_session, monkeypatch):
    _patch_dbmanager(monkeypatch, db_session)
    assert get_latest_status_per_instrument() == {}


def test_picks_latest_per_instrument(db_session, monkeypatch):
    _patch_dbmanager(monkeypatch, db_session)

    # instrument 1: online → offline → error  (newest = error)
    # instrument 2: online                     (newest = online)
    rows = [
        TblLisEventQueue(instrument_id=1, event_type="status",
                         payload_json={"status": "online"}, send_status="sent"),
        TblLisEventQueue(instrument_id=1, event_type="status",
                         payload_json={"status": "offline"}, send_status="sent"),
        TblLisEventQueue(instrument_id=1, event_type="status",
                         payload_json={"status": "error",
                                       "error_message": "EHOSTUNREACH"},
                         send_status="pending"),
        TblLisEventQueue(instrument_id=2, event_type="status",
                         payload_json={"status": "online"}, send_status="sent"),
    ]
    for r in rows:
        db_session.add(r)
    db_session.commit()

    result = get_latest_status_per_instrument()
    assert set(result.keys()) == {1, 2}
    assert result[1]["status"] == "error"
    assert result[1]["error_message"] == "EHOSTUNREACH"
    assert result[2]["status"] == "online"
    assert result[2]["error_message"] is None


def test_ignores_non_status_event_types(db_session, monkeypatch):
    _patch_dbmanager(monkeypatch, db_session)

    # instrument 5 only has 'log' event → bukan status, harus diabaikan.
    db_session.add(TblLisEventQueue(
        instrument_id=5, event_type="log",
        payload_json={"message": "noise"}, send_status="pending",
    ))
    # instrument 6 has both; status event harus dipakai.
    db_session.add(TblLisEventQueue(
        instrument_id=6, event_type="log",
        payload_json={"message": "noise"}, send_status="pending",
    ))
    db_session.add(TblLisEventQueue(
        instrument_id=6, event_type="status",
        payload_json={"status": "offline"}, send_status="sent",
    ))
    db_session.commit()

    result = get_latest_status_per_instrument()
    assert 5 not in result
    assert result[6]["status"] == "offline"
