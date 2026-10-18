import importlib.util
import io
import os
from pathlib import Path
import pytest
from cryptography.exceptions import InvalidTag

path=Path(__file__).resolve().parents[2]/'scripts'/'backup.py'
# Docker image only includes backend tests; the host backup helper is tested locally.
@pytest.mark.skipif(not path.exists(),reason='Host backup helper is outside the backend image')
def test_backup_stream_restore_and_tamper_detection(tmp_path):
    spec=importlib.util.spec_from_file_location('backup_helper',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    key=os.urandom(32); original=b'private restoration fixture'*100000
    encrypted=tmp_path/'backup.enc'
    module.encrypt_stream(io.BytesIO(original),encrypted,key)
    assert b'private restoration fixture' not in encrypted.read_bytes()
    restored=io.BytesIO(); module.decrypt_stream(encrypted,restored,key)
    assert restored.getvalue()==original
    corrupted=bytearray(encrypted.read_bytes()); corrupted[35]^=1; encrypted.write_bytes(corrupted)
    with pytest.raises(InvalidTag): module.decrypt_stream(encrypted,None,key)
