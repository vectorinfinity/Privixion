import base64
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .config import settings


def cipher():
    key = settings.key_file.read_bytes()
    if len(key) != 32:
        raise RuntimeError('Artifact key must contain exactly 32 bytes')
    return AESGCM(key)


def encrypt(value: bytes, context: str):
    nonce = os.urandom(12)
    return nonce + cipher().encrypt(nonce, value, context.encode())


def decrypt(value: bytes, context: str):
    return cipher().decrypt(value[:12], value[12:], context.encode())


def seal_text(value: str):
    return base64.b64encode(encrypt(value.encode(), 'database')).decode()


def open_text(value: str):
    return decrypt(base64.b64decode(value), 'database').decode()


def artifact_path(document_id: str, name: str):
    if not re.fullmatch(r'[a-f0-9-]{36}', document_id) or not re.fullmatch(r'[a-zA-Z0-9_.-]+', name):
        raise ValueError('Invalid artifact identifier')
    return settings.data_dir / document_id / (name + '.enc')


def put(document_id: str, name: str, value: bytes):
    path = artifact_path(document_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = encrypt(value, f'{document_id}/{name}')
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        f.write(encrypted)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def get(document_id: str, name: str):
    return decrypt(artifact_path(document_id, name).read_bytes(), f'{document_id}/{name}')


@contextmanager
def workspace():
    # Docker mounts /tmp as tmpfs; decrypted inputs never enter persistent storage.
    with tempfile.TemporaryDirectory(prefix='redaction-') as directory:
        yield Path(directory)
