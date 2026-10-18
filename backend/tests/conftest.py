import os
import tempfile
import atexit
from pathlib import Path

# Configure before importing application modules. No production data or key is used.
_temp = tempfile.TemporaryDirectory(prefix='privixion-tests-')
ROOT = Path(_temp.name)
os.environ['DATABASE_URL'] = 'sqlite:///' + str(ROOT / 'tests.db').replace('\\', '/')
os.environ['DATA_DIR'] = str(ROOT / 'artifacts')
os.environ['ARTIFACT_KEY_FILE'] = str(ROOT / 'key')
os.environ['ADMIN_HASH_FILE'] = str(ROOT / 'password')
(ROOT / 'artifacts').mkdir()
(ROOT / 'key').write_bytes(os.urandom(32))

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from app.main import app
from app.db import Base, engine

atexit.register(engine.dispose)

(ROOT / 'password').write_text(PasswordHasher().hash('test-password-12345'))


@pytest.fixture
def client(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # Prevent external Redis calls. Tests explicitly run queued functions.
    monkeypatch.setattr('app.main.process_document.delay', lambda *_: None)
    monkeypatch.setattr('app.main.finalize_document.delay', lambda *_: None)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def signed_in(client):
    result = client.post('/api/auth/login', json={'password': 'test-password-12345'})
    assert result.status_code == 200
    client.headers['X-CSRF-Token'] = result.json()['csrf']
    return client
