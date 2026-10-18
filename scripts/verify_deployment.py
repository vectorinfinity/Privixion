"""Exercise the live stack and restore its backup into an isolated Compose project.

Uses synthetic documents only. Credentials are read locally and never printed.
The primary deployment is left running; only the drill's temporary volumes are removed.
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from tests.fixtures import office_bytes
from tests.test_redaction import make_pdf
import backup


def checked(response):
    if not response.ok:
        try:
            detail = response.json().get('detail', 'Request failed')
        except ValueError:
            detail = 'Request failed'
        raise RuntimeError(f'HTTP {response.status_code}: {detail}')
    return response


def sign_in(base):
    client = requests.Session()
    password_file = ROOT / 'secrets' / 'initial-password.txt'
    if not password_file.exists():
        import getpass
        password = getpass.getpass('Administrator password for the verification drill: ')
    else:
        password = password_file.read_text().strip()
    response = checked(client.post(base + '/api/auth/login', json={'password': password},
                                   headers={'Origin': base}, timeout=30))
    client.headers['X-CSRF-Token'] = response.json()['csrf']
    return client


def wait_ready(client, base, path, limit=900):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        value = checked(client.get(base + path, timeout=30)).json()
        if value['status'] == 'ready':
            return value
        if value['status'] == 'failed':
            raise RuntimeError(value.get('error') or 'Document processing failed')
        time.sleep(2)
    raise TimeoutError('Processing did not finish within the verification time limit')


def fixtures():
    output = [('verification.pdf', make_pdf(rotation=90, crop=True, image=True), 'application/pdf'),
              ('verification.txt', b'Email: redaction@example.com\nPUBLIC INFORMATION', 'text/plain')]
    for kind in ('docx', 'xlsx', 'pptx'):
        output.append(('verification.' + kind, office_bytes(kind), 'application/octet-stream'))
    image = Image.new('RGB', (1600, 500), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=44)
    draw.text((70, 90), 'Email: redaction@example.com', fill='black', font=font)
    draw.text((70, 240), 'PUBLIC INFORMATION', fill='black', font=font)
    for extension, kind, mime in [('png', 'PNG', 'image/png'), ('jpeg', 'JPEG', 'image/jpeg'), ('tiff', 'TIFF', 'image/tiff')]:
        source = io.BytesIO()
        if kind == 'TIFF':
            image.save(source, format=kind, save_all=True, append_images=[image.copy()])
        else:
            image.save(source, format=kind)
        output.append(('verification.' + extension, source.getvalue(), mime))
    return output


def verify_originals(records):
    code = "import sys,json,hashlib; from app.storage import get; records=json.load(sys.stdin); assert all(hashlib.sha256(get(r['document_id'],'original')).hexdigest()==r['original_sha256'] for r in records); print('Original artifact checksums verified')"
    subprocess.run(['docker', 'compose', 'exec', '-T', 'api', 'python', '-c', code], cwd=ROOT,
                   input=json.dumps(records).encode(), check=True)


def verify_exports(client, base, records):
    for record in records:
        detail = checked(client.get(base + '/api/documents/' + record['document_id'], timeout=30)).json()
        assert detail['status'] == 'ready'
        export = next(e for e in detail['exports'] if e['id'] == record['export_id'])
        assert export['confirmed'] and export['checksum'] == record['output_sha256']
        pdf = checked(client.get(base + '/api/exports/' + export['id'] + '/download', timeout=60)).content
        assert hashlib.sha256(pdf).hexdigest() == record['output_sha256']
        reader = PdfReader(io.BytesIO(pdf))
        assert len(reader.pages) == record['pages']
        assert all(not page.extract_text().strip() and '/Annots' not in page for page in reader.pages)
        assert '/Names' not in reader.trailer['/Root'] and '/AcroForm' not in reader.trailer['/Root']
        report = checked(client.get(base + '/api/exports/' + export['id'] + '/report/json', timeout=30)).json()
        assert report['checksum_sha256'] == record['output_sha256']
        assert 'redaction@example.com' not in json.dumps(report)
        events = checked(client.get(base + '/api/audit', params={'document_id': record['document_id']}, timeout=30)).json()
        assert {'uploaded', 'detection_completed', 'draft_saved', 'finalized', 'export_confirmed'} <= {event['action'] for event in events}
        assert 'redaction@example.com' not in json.dumps(events)
    verify_originals(records)


def main():
    base = 'http://localhost:8080'
    checked(requests.get(base + '/api/health', timeout=30))
    assert requests.get(base + '/api/documents', timeout=30).status_code == 401
    client = sign_in(base)
    samples = fixtures()
    result = checked(client.post(base + '/api/documents', files=[('files', fixture) for fixture in samples], timeout=90)).json()
    assert not result['failures'] and len(result['documents']) == 8
    originals = {name: hashlib.sha256(value).hexdigest() for name, value, _ in samples}
    records = []
    for document in result['documents']:
        detail = wait_ready(client, base, '/api/documents/' + document['id'])
        email = [f for f in detail['findings'] if f['category'] == 'EMAIL']
        assert email, f"No sensitive email found for {document['kind']}"
        draft = detail['draft']
        draft['decisions'] = {f['id']: ('accepted' if f['category'] == 'EMAIL' else 'rejected') for f in detail['findings']}
        draft['selections'] = [{key: f[key] for key in ('id', 'page', 'rects', 'category')} | {'source': 'automatic'} for f in email]
        saved = checked(client.put(base + '/api/documents/' + document['id'] + '/draft', json=draft, timeout=30)).json()
        export = checked(client.post(base + '/api/documents/' + document['id'] + '/finalize', json={'version': saved['version']}, timeout=30)).json()
        export_deadline = time.monotonic() + 900
        while time.monotonic() < export_deadline:
            detail = wait_ready(client, base, '/api/documents/' + document['id'])
            current = next(e for e in detail['exports'] if e['id'] == export['id'])
            if current['status'] == 'ready':
                break
            if current['status'] == 'failed':
                raise RuntimeError('Final export failed')
            time.sleep(2)
        else:
            raise TimeoutError('Export did not finish within the verification time limit')
        assert client.get(base + '/api/exports/' + export['id'] + '/download', timeout=30).status_code == 409
        for page in range(len(detail['pages'])):
            image = checked(client.get(base + f"/api/exports/{export['id']}/pages/{page}", timeout=60)).content
            assert image.startswith(b'\x89PNG')
        checked(client.post(base + '/api/exports/' + export['id'] + '/confirm', timeout=30))
        records.append({'document_id': document['id'], 'export_id': export['id'], 'format': document['kind'],
                        'pages': len(detail['pages']), 'original_sha256': originals[document['name']], 'output_sha256': current['checksum']})
        print(f"Live {document['kind'].upper()} upload, detection, review, export and confirmation passed", flush=True)
    verify_exports(client, base, records)
    batch = checked(client.post(base + '/api/batch/download', json={'export_ids': [r['export_id'] for r in records]}, timeout=90)).content
    with zipfile.ZipFile(io.BytesIO(batch)) as archive:
        assert len(archive.namelist()) == 16 and archive.testzip() is None
    print('Live batch, reports, audit and preservation checks passed', flush=True)
    identifier = uuid.uuid4().hex[:12]
    directory = ROOT / 'data' / ('deployment-drill-' + identifier)
    key_directory = ROOT / 'secrets' / ('deployment-drill-' + identifier)
    backup.backup(directory, key_directory)
    (directory / 'fixture-manifest.json').write_text(json.dumps(records, indent=2))
    # The namespace is unique to this run. Never change or remove the primary volumes.
    drill = 'privixion-drill-' + identifier
    override = directory / 'restore-compose.yaml'
    override.write_text('services:\n  web:\n    image: privixion-web:latest\n    ports: !override\n      - "127.0.0.1:8081:80"\n')
    old_environment = {name: os.environ.get(name) for name in ('COMPOSE_PROJECT_NAME', 'COMPOSE_FILE')}
    try:
        os.environ['COMPOSE_PROJECT_NAME'] = drill
        os.environ['COMPOSE_FILE'] = os.pathsep.join([str(ROOT / 'compose.yaml'), str(override)])
        assert not backup.compose('ps', '-a', '-q', capture_output=True).stdout.strip()
        backup.restore(directory, key_directory)
        backup.compose('up', '-d', '--wait', 'api', 'worker', 'web')
        restored = sign_in('http://localhost:8081')
        verify_exports(restored, 'http://localhost:8081', records)
        print('Isolated PostgreSQL/artifact restore, export checksums and audit history passed', flush=True)
    finally:
        if not re.fullmatch(r'privixion-drill-[a-f0-9]{12}', drill):
            raise RuntimeError('Unsafe drill namespace')
        backup.compose('-p', drill, 'down', '--volumes')
        for name, value in old_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    # Remove only fixtures uploaded by this run. Their non-sensitive audit entries remain.
    client = sign_in(base)
    for record in records:
        checked(client.delete(base + '/api/documents/' + record['document_id'], timeout=30))
    checked(client.post(base + '/api/auth/logout', timeout=30))
    checked(requests.get(base + '/api/health', timeout=30))
    print('Deployment verification passed; primary application is running on http://localhost:8080', flush=True)


if __name__ == '__main__':
    main()
