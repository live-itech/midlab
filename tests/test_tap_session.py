"""Test TapSession + model tbl_tap_session."""

import pytest

from lib.db import TblTapSession


class TestTblTapSession:
    def test_bisa_disimpan_dan_dibaca(self, db_session):
        s = TblTapSession(
            name="AR580 commissioning",
            transport="tcp_server",
            target="0.0.0.0:2600",
            protocol_basis="HL7",
            response_mode="uni",
            status="running",
        )
        db_session.add(s)
        db_session.commit()

        row = db_session.query(TblTapSession).one()
        assert row.name == "AR580 commissioning"
        assert row.target == "0.0.0.0:2600"

    def test_counter_default_nol(self, db_session):
        s = TblTapSession(
            name="x", transport="serial", target="/dev/ttyUSB0@9600-8N1",
            protocol_basis="RAW", response_mode="uni", status="running",
        )
        db_session.add(s)
        db_session.commit()
        row = db_session.query(TblTapSession).one()
        assert (row.bytes_rx, row.bytes_tx, row.message_count) == (0, 0, 0)

    def test_detected_protocol_boleh_kosong(self, db_session):
        s = TblTapSession(
            name="x", transport="tcp_client", target="10.0.0.5:9100",
            protocol_basis="AUTO", response_mode="uni", status="running",
        )
        db_session.add(s)
        db_session.commit()
        assert db_session.query(TblTapSession).one().detected_protocol is None
