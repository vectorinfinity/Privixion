import hashlib
import io
import json
import re
import secrets
import shutil
import zipfile
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import Response as BytesResponse
from pydantic import BaseModel, Field, field_validator
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from sqlalchemy import select, delete, func
from .config import settings
from .db import Base, engine, SessionLocal, Document, Draft, Export, Job, Audit, LoginSession, RuleTemplate, audit, now, uid
from . import storage
from .detection import validate_pattern, findings
from .processing import render_page
from .tasks import process_document, finalize_document


@asynccontextmanager
async def lifespan(app):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    storage.cipher()
    if not settings.password_file.exists():
        raise RuntimeError('Run scripts/bootstrap.py to create administrator credentials')
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title='Privixion Redaction', lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def database():
    with SessionLocal() as db:
        yield db


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def authenticated(request: Request, db=Depends(database)):
    token = request.cookies.get('privixion_session', '')
    session = db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest()) if token else None
    if not session or utc(session.expires) <= now():
        raise HTTPException(401, 'Sign in to continue')
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session.csrf):
            raise HTTPException(403, 'Invalid request token. Reload the application.')
    return session


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=512)


@app.get('/api/health')
def health(db=Depends(database)):
    db.execute(select(1))
    return {'status': 'ok'}


@app.post('/api/auth/login')
def login(body: LoginBody, response: Response, request: Request, db=Depends(database)):
    origin = request.headers.get('origin')
    expected_origin = settings.public_origin or str(request.base_url).rstrip('/')
    if origin and origin.rstrip('/') != expected_origin:
        raise HTTPException(403, 'Cross-origin login is unsupported')
    # Global throttling is intentional for a single administrator installation.
    attempts = db.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'login_failed', Audit.timestamp > now()-timedelta(minutes=5)))
    if attempts >= 10:
        raise HTTPException(429, 'Too many sign-in attempts. Try again in five minutes.')
    try:
        PasswordHasher().verify(settings.password_file.read_text().strip(), body.password)
    except (VerifyMismatchError, VerificationError):
        audit(db, 'login_failed')
        db.commit()
        raise HTTPException(401, 'Incorrect password') from None
    token, csrf = secrets.token_urlsafe(32), secrets.token_hex(32)
    db.execute(delete(LoginSession).where(LoginSession.expires < now()))
    db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), csrf=csrf, expires=now()+timedelta(hours=settings.session_hours)))
    audit(db, 'login')
    db.commit()
    response.set_cookie('privixion_session', token, httponly=True, secure=settings.cookie_secure, samesite='strict', max_age=settings.session_hours*3600, path='/')
    return {'csrf': csrf, 'user': 'admin'}


@app.get('/api/auth/me')
def me(session=Depends(authenticated)):
    return {'csrf': session.csrf, 'user': 'admin'}


@app.post('/api/auth/logout')
def logout(response: Response, session=Depends(authenticated), db=Depends(database)):
    db.delete(session)
    audit(db, 'logout')
    db.commit()
    response.delete_cookie('privixion_session', path='/')
    return {'ok': True}


def document_or_404(db, document_id, lock=False):
    query = select(Document).where(Document.id == document_id)
    if lock:
        query = query.with_for_update()
    document = db.scalar(query)
    if not document:
        raise HTTPException(404, 'Document not found')
    return document


def ready_document(db, document_id, lock=False):
    document = document_or_404(db, document_id, lock)
    if document.status != 'ready':
        raise HTTPException(409, 'Wait for document processing to complete')
    return document


def analysis(document_id):
    return json.loads(storage.get(document_id, 'analysis'))


def doc_json(doc):
    return {'id': doc.id, 'name': storage.open_text(doc.name), 'kind': doc.kind, 'status': doc.status,
            'error': doc.error, 'created_at': doc.created_at.isoformat()}


def export_json(export):
    return {'id': export.id, 'version': export.version, 'status': export.status,
            'checksum': export.checksum, 'confirmed': export.confirmed_at is not None, 'created_at': export.created_at.isoformat()}


def dispatch(job, db):
    try:
        (finalize_document if job.export_id else process_document).delay(job.id)
    except Exception:
        job.state, job.error = 'failed', 'The job queue is unavailable. Retry when services are running.'
        if job.export_id:
            db.get(Export, job.export_id).status = 'failed'
        else:
            doc = db.get(Document, job.document_id)
            doc.status, doc.error = 'failed', job.error
        db.commit()


@app.get('/api/documents', dependencies=[Depends(authenticated)])
def documents(db=Depends(database)):
    return [doc_json(doc) for doc in db.scalars(select(Document).order_by(Document.created_at.desc()))]


@app.get('/api/config', dependencies=[Depends(authenticated)])
def configuration():
    return {'max_file_mb': settings.max_bytes//(1024*1024), 'max_pages': settings.max_pages, 'max_batch': settings.max_batch}


@app.post('/api/documents', dependencies=[Depends(authenticated)])
async def upload(files: list[UploadFile] = File(...), db=Depends(database)):
    if not 1 <= len(files) <= settings.max_batch:
        raise HTTPException(400, f'Upload between 1 and {settings.max_batch} files per batch')
    accepted, failures = [], []
    for file in files:
        filename = (file.filename or 'document').replace('\\', '/').rsplit('/', 1)[-1][:240]
        kind = filename.rsplit('.', 1)[-1].lower()
        kind = {'jpg': 'jpeg', 'tif': 'tiff'}.get(kind, kind)
        if kind not in {'pdf','docx','xlsx','pptx','txt','tiff','jpeg','png'}:
            failures.append({'name': filename, 'error': 'Unsupported file type'})
            await file.close()
            continue
        # Bound memory even if a client bypasses the proxy upload limit.
        source = await file.read(settings.max_bytes+1)
        await file.close()
        if not source or len(source) > settings.max_bytes:
            failures.append({'name': filename, 'error': 'Empty file or file exceeds the size limit'})
            continue
        document_id = uid()
        storage.put(document_id, 'original', source)
        document = Document(id=document_id, name=storage.seal_text(filename), kind=kind)
        db.add(document)
        db.flush()
        db.add(Draft(document_id=document_id, selections=[], decisions={}))
        job = Job(document_id=document_id)
        db.add(job)
        audit(db, 'uploaded', document_id, format=kind, bytes=len(source))
        db.commit()
        dispatch(job, db)
        accepted.append(doc_json(document))
    return {'documents': accepted, 'failures': failures}


@app.get('/api/documents/{document_id}', dependencies=[Depends(authenticated)])
def detail(document_id: str, db=Depends(database)):
    doc = document_or_404(db, document_id)
    data = analysis(document_id) if doc.status == 'ready' else {'pages': [], 'findings': []}
    draft = db.get(Draft, document_id)
    return {**doc_json(doc), **data, 'draft': {'version': draft.version, 'selections': draft.selections, 'decisions': draft.decisions},
            'exports': [export_json(e) for e in db.scalars(select(Export).where(Export.document_id == document_id).order_by(Export.created_at.desc()))]}


@app.get('/api/documents/{document_id}/jobs', dependencies=[Depends(authenticated)])
def jobs(document_id: str, db=Depends(database)):
    document_or_404(db, document_id)
    return [{'id': j.id, 'state': j.state, 'progress': j.progress, 'error': j.error, 'timings': j.timings,
             'export_id': j.export_id, 'stalled': utc(j.updated_at) < now()-timedelta(seconds=settings.processing_timeout+60)}
            for j in db.scalars(select(Job).where(Job.document_id == document_id).order_by(Job.updated_at.desc()))]


@app.post('/api/jobs/{job_id}/retry', dependencies=[Depends(authenticated)])
def retry(job_id: str, db=Depends(database)):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404, 'Job not found')
    if job.state != 'failed' and not (job.state != 'completed' and utc(job.updated_at) < now()-timedelta(seconds=settings.processing_timeout+60)):
        raise HTTPException(409, 'Only failed or stalled jobs can be retried')
    job.state, job.error, job.progress, job.updated_at = 'queued', None, 0, now()
    if job.export_id:
        db.get(Export, job.export_id).status = 'queued'
    else:
        doc = db.get(Document, job.document_id)
        doc.status, doc.error = 'queued', None
    audit(db, 'job_retried', job.document_id, job_id=job.id)
    db.commit()
    dispatch(job, db)
    return {'ok': True}


@app.get('/api/documents/{document_id}/pages/{page}', dependencies=[Depends(authenticated)])
def page_image(document_id: str, page: int, db=Depends(database)):
    ready_document(db, document_id)
    if not 0 <= page < len(analysis(document_id)['pages']):
        raise HTTPException(404, 'Page not found')
    return BytesResponse(storage.get(document_id, f'page-{page}'), media_type='image/png')


class Selection(BaseModel):
    id: str = Field(pattern=r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
    page: int = Field(ge=0)
    rects: list[tuple[float, float, float, float]] = Field(min_length=1, max_length=2000)
    category: str = Field(pattern=r'^[A-Z_0-9]{1,50}$')
    source: Literal['manual', 'automatic', 'search']

    @field_validator('rects')
    @classmethod
    def valid_rects(cls, rects):
        if any(not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1) for x0,y0,x1,y1 in rects):
            raise ValueError('Rectangles must be normalized, finite, and nonempty')
        return rects


class DraftBody(BaseModel):
    version: int = Field(ge=1)
    selections: list[Selection] = Field(max_length=10000)
    decisions: dict[str, Literal['accepted','rejected']] = Field(default_factory=dict, max_length=10000)


@app.put('/api/documents/{document_id}/draft', dependencies=[Depends(authenticated)])
def save_draft(document_id: str, body: DraftBody, db=Depends(database)):
    ready_document(db, document_id, lock=True)
    draft = db.get(Draft, document_id)
    if draft.version != body.version:
        raise HTTPException(409, 'The draft changed. Reload the document before saving.')
    data = analysis(document_id)
    if any(s.page >= len(data['pages']) for s in body.selections):
        raise HTTPException(400, 'Selection page does not exist')
    if len({s.id for s in body.selections}) != len(body.selections):
        raise HTTPException(400, 'Selection identifiers must be unique')
    if not set(body.decisions).issubset({f['id'] for f in data['findings']}):
        raise HTTPException(400, 'Unknown detection decision')
    draft.selections = [s.model_dump(mode='json') for s in body.selections]
    draft.decisions = body.decisions
    draft.version += 1
    audit(db, 'draft_saved', document_id, version=draft.version, regions=draft.selections, decisions=body.decisions)
    db.commit()
    return {'version': draft.version}


class SearchBody(BaseModel):
    term: str = Field(min_length=1, max_length=200)


@app.post('/api/documents/{document_id}/search', dependencies=[Depends(authenticated)])
def search(document_id: str, body: SearchBody, db=Depends(database)):
    ready_document(db, document_id)
    hits = []
    for page in analysis(document_id)['pages']:
        words, offsets, cursor = page['words'], [], 0
        for word in words:
            offsets.append((cursor, cursor+len(word['text']), word['rect']))
            cursor += len(word['text'])+1
        text = ' '.join(w['text'] for w in words)
        for match in re.finditer(re.escape(body.term), text, re.I):
            hits.append({'id': uid(), 'page': page['index'], 'rects': [r for a,b,r in offsets if a < match.end() and b > match.start()], 'category': 'SEARCH', 'source': 'search'})
    audit(db, 'searched', document_id, matches=len(hits))
    db.commit()
    return hits


class FinalizeBody(BaseModel):
    version: int


@app.post('/api/documents/{document_id}/finalize', dependencies=[Depends(authenticated)])
def finalize(document_id: str, body: FinalizeBody, db=Depends(database)):
    ready_document(db, document_id, lock=True)
    draft = db.get(Draft, document_id)
    if draft.version != body.version:
        raise HTTPException(409, 'Save the current draft before finalizing')
    data = analysis(document_id)
    for finding in data['findings']:
        decision = draft.decisions.get(finding['id'])
        if decision not in {'accepted', 'rejected'}:
            raise HTTPException(409, 'Review every detection before finalizing')
        if decision == 'accepted' and not any(s['id'] == finding['id'] and s['page'] == finding['page'] and s['rects'] == finding['rects'] for s in draft.selections):
            raise HTTPException(409, 'Accepted detections must have matching redaction selections')
    existing = db.scalar(select(Export).where(Export.document_id == document_id, Export.version == body.version, Export.status != 'failed'))
    if existing:
        return export_json(existing)
    export = Export(document_id=document_id, version=draft.version, selections=draft.selections)
    db.add(export)
    db.flush()
    job = Job(document_id=document_id, export_id=export.id)
    db.add(job)
    audit(db, 'finalization_requested', document_id, version=draft.version, export_id=export.id)
    db.commit()
    dispatch(job, db)
    return export_json(export)


def export_or_404(db, export_id):
    export = db.get(Export, export_id)
    if not export:
        raise HTTPException(404, 'Export not found')
    if export.status != 'ready':
        raise HTTPException(409, 'The export is not ready')
    return export


@app.get('/api/exports/{export_id}/pages/{page}', dependencies=[Depends(authenticated)])
def export_preview(export_id: str, page: int, db=Depends(database)):
    export = export_or_404(db, export_id)
    try:
        image = render_page(storage.get(export.document_id, f'export-{export.id}'), page)
    except IndexError:
        raise HTTPException(404, 'Page not found') from None
    return BytesResponse(image, media_type='image/png')


@app.post('/api/exports/{export_id}/confirm', dependencies=[Depends(authenticated)])
def confirm_export(export_id: str, db=Depends(database)):
    export = export_or_404(db, export_id)
    export.confirmed_at = now()
    audit(db, 'export_confirmed', export.document_id, export_id=export.id, version=export.version)
    db.commit()
    return export_json(export)


def download_response(data, filename, mime):
    return BytesResponse(data, media_type=mime, headers={'Content-Disposition': f'attachment; filename="{filename}"'})


@app.get('/api/exports/{export_id}/download', dependencies=[Depends(authenticated)])
def download_export(export_id: str, db=Depends(database)):
    export = export_or_404(db, export_id)
    if not export.confirmed_at:
        raise HTTPException(409, 'Confirm the final preview before downloading')
    audit(db, 'downloaded', export.document_id, export_id=export.id)
    db.commit()
    return download_response(storage.get(export.document_id, f'export-{export.id}'), f'redacted-{export.id}.pdf', 'application/pdf')


@app.get('/api/exports/{export_id}/report/{kind}', dependencies=[Depends(authenticated)])
def download_report(export_id: str, kind: Literal['pdf','json'], db=Depends(database)):
    export = export_or_404(db, export_id)
    audit(db, 'report_downloaded', export.document_id, export_id=export.id, format=kind)
    db.commit()
    return download_response(storage.get(export.document_id, f'report-{export.id}.{kind}'), f'report-{export.id}.{kind}', 'application/pdf' if kind == 'pdf' else 'application/json')


class BatchBody(BaseModel):
    export_ids: list[str] = Field(min_length=1)


@app.post('/api/batch/download', dependencies=[Depends(authenticated)])
def batch_download(body: BatchBody, db=Depends(database)):
    if len(body.export_ids) > settings.max_batch:
        raise HTTPException(400, 'Batch exceeds the configured document limit')
    output = io.BytesIO()
    exports = [export_or_404(db, i) for i in dict.fromkeys(body.export_ids)]
    if any(not export.confirmed_at for export in exports):
        raise HTTPException(409, 'Confirm every export before downloading the batch')
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for export in exports:
            archive.writestr(f'redacted-{export.id}.pdf', storage.get(export.document_id, f'export-{export.id}'))
            archive.writestr(f'report-{export.id}.json', storage.get(export.document_id, f'report-{export.id}.json'))
            audit(db, 'batch_downloaded', export.document_id, export_id=export.id)
    db.commit()
    return download_response(output.getvalue(), 'redacted-batch.zip', 'application/zip')


@app.get('/api/audit', dependencies=[Depends(authenticated)])
def audit_history(document_id: str | None = None, db=Depends(database)):
    query = select(Audit).order_by(Audit.timestamp.desc()).limit(1000)
    if document_id:
        query = query.where(Audit.document_id == document_id)
    return [{'id': event.id, 'actor': event.actor, 'action': event.action, 'document_id': event.document_id,
             'details': event.details, 'timestamp': event.timestamp.isoformat()} for event in db.scalars(query)]


@app.delete('/api/documents/{document_id}', dependencies=[Depends(authenticated)])
def delete_document(document_id: str, db=Depends(database)):
    document = document_or_404(db, document_id, lock=True)
    active = db.scalar(select(Job).where(Job.document_id == document_id, Job.state.not_in(['completed','failed'])))
    if active:
        raise HTTPException(409, 'Wait for active jobs to finish, or retry stalled jobs before deleting')
    for model in (Job, Export, Draft):
        db.execute(delete(model).where(model.document_id == document_id))
    db.delete(document)
    audit(db, 'deleted', document_id)
    # UUID path already validated through storage.artifact_path.
    directory = storage.artifact_path(document_id, 'original').parent
    try:
        directory.resolve().relative_to(settings.data_dir.resolve())
    except ValueError:
        raise HTTPException(409, 'Artifact directory is outside the configured storage root') from None
    if directory.is_symlink():
        raise HTTPException(409, 'Artifact directory cannot be a symbolic link')
    if directory.exists():
        shutil.rmtree(directory)
    db.commit()
    return {'ok': True}


class Rule(BaseModel):
    category: str = Field(pattern=r'^CUSTOM_[A-Z_0-9]{1,40}$')
    pattern: str = Field(min_length=1, max_length=200)

    @field_validator('pattern')
    @classmethod
    def validate(cls, value):
        validate_pattern(value)
        return value


class TemplateBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rules: list[Rule] = Field(min_length=1, max_length=20)


@app.get('/api/templates', dependencies=[Depends(authenticated)])
def templates(db=Depends(database)):
    return [{'id': t.id, 'name': t.name, 'rules': json.loads(storage.open_text(t.rules))} for t in db.scalars(select(RuleTemplate))]


@app.post('/api/templates', dependencies=[Depends(authenticated)])
def create_template(body: TemplateBody, db=Depends(database)):
    template = RuleTemplate(name=body.name, rules=storage.seal_text(json.dumps([r.model_dump() for r in body.rules])))
    db.add(template)
    audit(db, 'template_created')
    db.commit()
    return {'id': template.id}


@app.delete('/api/templates/{template_id}', dependencies=[Depends(authenticated)])
def delete_template(template_id: str, db=Depends(database)):
    template = db.get(RuleTemplate, template_id)
    if not template:
        raise HTTPException(404, 'Template not found')
    db.delete(template)
    audit(db, 'template_deleted', template_id=template_id)
    db.commit()
    return {'ok': True}


@app.put('/api/templates/{template_id}', dependencies=[Depends(authenticated)])
def update_template(template_id: str, body: TemplateBody, db=Depends(database)):
    template = db.get(RuleTemplate, template_id)
    if not template:
        raise HTTPException(404, 'Template not found')
    template.name = body.name
    template.rules = storage.seal_text(json.dumps([r.model_dump() for r in body.rules]))
    audit(db, 'template_updated', template_id=template_id)
    db.commit()
    return {'id': template.id}


class ApplyBody(BaseModel):
    template_id: str
    version: int


@app.post('/api/documents/{document_id}/templates/apply', dependencies=[Depends(authenticated)])
def apply_template(document_id: str, body: ApplyBody, db=Depends(database)):
    ready_document(db, document_id, lock=True)
    draft = db.get(Draft, document_id)
    if draft.version != body.version:
        raise HTTPException(409, 'Save or reload the draft before applying a template')
    template = db.get(RuleTemplate, body.template_id)
    if not template:
        raise HTTPException(404, 'Template not found')
    rules = json.loads(storage.open_text(template.rules))
    data = analysis(document_id)
    try:
        new = [f for page in data['pages'] for f in findings(page, rules, use_nlp=False) if f['category'].startswith('CUSTOM_')]
    except TimeoutError:
        raise HTTPException(422, 'A pattern exceeded its time budget. Use a simpler pattern.') from None
    existing = {(f['page'], f['category'], json.dumps(f['rects'])) for f in data['findings']}
    new = [f for f in new if (f['page'], f['category'], json.dumps(f['rects'])) not in existing]
    data['findings'].extend(new)
    storage.put(document_id, 'analysis', json.dumps(data).encode())
    draft.version += 1
    audit(db, 'template_applied', document_id, template_id=template.id, count=len(new), version=draft.version)
    db.commit()
    return {'count': len(new), 'version': draft.version}
