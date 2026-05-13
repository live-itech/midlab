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
