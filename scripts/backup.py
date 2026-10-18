"""Encrypted, consistent Compose backups. Restore only into empty volumes."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROOT = Path(__file__).resolve().parents[1]
MAGIC = b'PRVXBACK1'
CHUNK = 1024*1024


def compose(*args, **kwargs):
    return subprocess.run(['docker','compose',*args],cwd=ROOT,check=True,**kwargs)


def encrypt_stream(source, destination, key):
    nonce=os.urandom(12)
    encryptor=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    with destination.open('xb') as output:
        output.write(MAGIC+nonce)
        while chunk:=source.read(CHUNK):
            output.write(encryptor.update(chunk))
        output.write(encryptor.finalize())
        output.write(encryptor.tag)


def decrypt_stream(source, destination, key):
    with source.open('rb') as stream:
        if stream.read(len(MAGIC))!=MAGIC:
            raise ValueError('Unrecognized backup format')
        nonce=stream.read(12)
        stream.seek(-16,2); tag=stream.read(16)
        size=stream.tell()-16-len(MAGIC)-12
        stream.seek(len(MAGIC)+12)
        decryptor=Cipher(algorithms.AES(key),modes.GCM(nonce,tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        while size>0:
            chunk=stream.read(min(CHUNK,size)); size-=len(chunk)
            if not chunk: raise ValueError('Truncated backup')
            data=decryptor.update(chunk)
            if destination is not None: destination.write(data)
        tail=decryptor.finalize()
        if destination is not None: destination.write(tail)


def capture_encrypted(args, destination, key):
    with subprocess.Popen(['docker','compose',*args],cwd=ROOT,stdout=subprocess.PIPE) as process:
        encrypt_stream(process.stdout,destination,key)
        if process.wait(): raise RuntimeError('Backup command failed')


def restore_encrypted(args, source, key):
    # Authenticate the entire encrypted stream before allowing any restore writes.
    decrypt_stream(source,None,key)
    with subprocess.Popen(['docker','compose',*args],cwd=ROOT,stdin=subprocess.PIPE) as process:
        decrypt_stream(source,process.stdin,key)
        process.stdin.close()
        if process.wait(): raise RuntimeError('Restore command failed')


def backup(destination, keys):
    destination=destination.resolve(); keys=keys.resolve()
    if destination==keys or destination in keys.parents or keys in destination.parents:
        raise ValueError('Backup data and key destinations must be separate directories')
    destination.mkdir(parents=True,exist_ok=False)
    keys.mkdir(parents=True,exist_ok=False)
    key=(ROOT/'secrets/artifact.key').read_bytes()
    compose('stop','--timeout','1900','web','worker','api')
    try:
        capture_encrypted(['exec','-T','db','pg_dump','-U','privixion','-d','privixion','-Fc'],destination/'database.enc',key)
        code="import tarfile,sys; t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|'); t.add('/data',arcname='artifacts'); t.close()"
        capture_encrypted(['run','--rm','--no-deps','-T','worker','python','-c',code],destination/'artifacts.enc',key)
        shutil.copy2(ROOT/'secrets/artifact.key',keys/'artifact.key')
        shutil.copy2(ROOT/'secrets/admin.hash',keys/'admin.hash')
        shutil.copy2(ROOT/'.env',keys/'deployment.env')
        manifest={}
        for name in ('database.enc','artifacts.enc'):
            with (destination/name).open('rb') as stream:
                manifest[name]=hashlib.file_digest(stream,'sha256').hexdigest()
        (destination/'manifest.json').write_text(json.dumps(manifest,indent=2))
        # Verify authentication without extracting plaintext to disk.
        for name in manifest: decrypt_stream(destination/name,None,key)
        print('Backup verified. Store the key directory separately and protect it with filesystem access controls.')
    finally:
        compose('start','api','worker','web')


def restore(destination, keys):
    key=(keys/'artifact.key').read_bytes()
    manifest=json.loads((destination/'manifest.json').read_text())
    for name in ('database.enc','artifacts.enc'):
        with (destination/name).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=manifest[name]:
                raise ValueError('Backup checksum mismatch')
        decrypt_stream(destination/name,None,key)
    # Restore keys only when absent or identical. Never overwrite an existing key.
    (ROOT/'secrets').mkdir(exist_ok=True)
    for source,target in [(keys/'artifact.key',ROOT/'secrets/artifact.key'),(keys/'admin.hash',ROOT/'secrets/admin.hash'),(keys/'deployment.env',ROOT/'.env')]:
        if target.exists() and target.read_bytes()!=source.read_bytes():
            raise ValueError('Destination secrets differ. Use a fresh project checkout for restoration.')
        if not target.exists(): shutil.copy2(source,target)
    compose('up','-d','--wait','db','redis')
    compose('stop','--timeout','1900','web','worker','api')
    count=compose('exec','-T','db','psql','-U','privixion','-d','privixion','-tAc',"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'",capture_output=True).stdout.strip()
    if count!=b'0': raise ValueError('Destination database is not empty. Restoration refuses to overwrite data.')
    empty="from pathlib import Path; import sys; sys.exit(1 if any(Path('/data').iterdir()) else 0)"
    compose('run','--rm','--no-deps','-T','worker','python','-c',empty)
    restore_encrypted(['exec','-T','db','pg_restore','-U','privixion','-d','privixion','--exit-on-error','--no-owner'],destination/'database.enc',key)
    code="import tarfile,sys; t=tarfile.open(fileobj=sys.stdin.buffer,mode='r|');\nfor m in t:\n if not (m.isfile() or m.isdir()) or not (m.name=='artifacts' or m.name.startswith('artifacts/')) or '..' in m.name.split('/'): raise ValueError('Unsafe archive member')\n m.name=m.name.removeprefix('artifacts').lstrip('/');\n if m.name: t.extract(m,path='/data',filter='data')"
    restore_encrypted(['run','--rm','--no-deps','-T','worker','python','-c',code],destination/'artifacts.enc',key)
    # Sessions are invalidated after recovery; jobs interrupted by backup remain retryable.
    compose('exec','-T','db','psql','-U','privixion','-d','privixion','-c',"DELETE FROM sessions; UPDATE jobs SET state='failed', error='Recovered job; retry required' WHERE state NOT IN ('completed','failed'); UPDATE documents SET status='failed', error='Recovered document; retry required' WHERE status IN ('queued','processing'); UPDATE exports SET status='failed' WHERE status IN ('queued','processing');")
    compose('up','-d','api','worker','web')
    print('Restored. Sign in and verify an original, a confirmed export, and the audit trail.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['backup','restore'])
    parser.add_argument('directory',type=Path)
    parser.add_argument('--keys-dir',type=Path,required=True)
    args=parser.parse_args()
    (backup if args.action=='backup' else restore)(args.directory,args.keys_dir)
