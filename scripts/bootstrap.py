"""Create local secrets once. Never replace an existing encryption key."""
import getpass
import os
import secrets
import argparse
from pathlib import Path
from argon2 import PasswordHasher

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--generate-password', action='store_true', help='Save a random initial password in secrets/initial-password.txt')
args = parser.parse_args()
directory = root / 'secrets'
directory.mkdir(exist_ok=True)
key = directory / 'artifact.key'
if not key.exists():
    with key.open('xb') as f:
        f.write(os.urandom(32))
    key.chmod(0o600)
password_hash = directory / 'admin.hash'
if not password_hash.exists():
    if args.generate_password:
        password = secrets.token_urlsafe(24)
        initial = directory / 'initial-password.txt'
        with initial.open('x') as f:
            f.write(password)
        initial.chmod(0o600)
        print('Initial password saved in secrets/initial-password.txt. Keep this file private.')
    else:
        password = getpass.getpass('Choose administrator password (minimum 12 characters): ')
        if len(password) < 12 or password != getpass.getpass('Confirm password: '):
            raise SystemExit('Password too short or confirmation does not match. Run again.')
    with password_hash.open('x') as f:
        f.write(PasswordHasher().hash(password))
    password_hash.chmod(0o600)
env = root / '.env'
if not env.exists():
    with env.open('x') as f:
        f.write(f'POSTGRES_PASSWORD={secrets.token_hex(32)}\nCOOKIE_SECURE=false\n')
    env.chmod(0o600)
print('Configuration ready. Start Docker Desktop, then run: docker compose up --build -d')
