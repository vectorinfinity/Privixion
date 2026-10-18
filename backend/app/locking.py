"""PostgreSQL session advisory locks survive progress commits on another session."""
import hashlib
from contextlib import contextmanager
from sqlalchemy import text
from .db import engine


@contextmanager
def job_lock(document_id):
    if engine.dialect.name == 'sqlite':
        # SQLite is only used by the isolated, single-process test harness.
        yield True
        return
    key = int.from_bytes(hashlib.sha256(document_id.encode()).digest()[:8], 'big', signed=True)
    with engine.connect() as connection:
        acquired = connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': key})
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
                connection.commit()
