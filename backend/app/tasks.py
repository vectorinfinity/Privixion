import hashlib
import io
import json
import time
from collections import Counter
from celery import Celery
from reportlab.pdfgen import canvas
from .config import settings
from .db import SessionLocal, Document, Export, Job, audit, now
from . import storage
from .processing import convert, analyze, flatten, ProcessingError
from .locking import job_lock

celery = Celery('redaction', broker=settings.broker_url)
celery.conf.update(task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
                   task_time_limit=settings.processing_timeout, broker_connection_retry_on_startup=True,
                   task_ignore_result=True)


def report(export):
    categories = Counter(s['category'] for s in export.selections)
    return {'export_id': export.id, 'document_id': export.document_id, 'draft_version': export.version,
            'created_at': export.created_at.isoformat(), 'checksum_sha256': export.checksum,
            'selection_count': len(export.selections), 'categories': dict(categories),
            'pages': sorted(set(s['page']+1 for s in export.selections)),
            'sources': dict(Counter(s['source'] for s in export.selections)),
            'regions': [{'page': s['page']+1, 'category': s['category'], 'rects': s['rects'], 'source': s['source']} for s in export.selections]}


def report_pdf(data):
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(595,842))
    lines = ['Redaction summary', f"Document: {data['document_id']}", f"Export: {data['export_id']}",
             f"Draft version: {data['draft_version']}", f"Created: {data['created_at']}",
             f"Selections: {data['selection_count']}", f"SHA-256: {data['checksum_sha256']}"]
    lines += [f'{category}: {count}' for category, count in data['categories'].items()]
    lines += [f'{source}: {count}' for source, count in data['sources'].items()]
    lines += ['Pages: ' + ', '.join(map(str, data['pages']))]
    import textwrap
    lines = [part for line in lines for part in (textwrap.wrap(line, 86) or [''])]
    y = 800
    for line in lines:
        if y < 40:
            c.showPage()
            y = 800
        c.setFont('Helvetica', 10)
        c.drawString(36, y, line)
        y -= 18
    c.save()
    return out.getvalue()


@celery.task(name='process_document', bind=True, max_retries=60)
def process_document(task, job_id):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        document_id = job.document_id
    with job_lock(document_id) as acquired:
        if not acquired:
            raise task.retry(countdown=30)
        _process_document(job_id)


def _process_document(job_id):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.state == 'completed':
            return
        document = db.get(Document, job.document_id)
        if not document:
            return
        job.state, job.updated_at = 'converting', now()
        document.status = 'processing'
        db.commit()
        timings = {}
        try:
            with storage.workspace() as directory:
                start = time.perf_counter()
                pdf = convert(storage.get(document.id, 'original'), document.kind, directory)
                storage.put(document.id, 'converted', pdf)
                timings['conversion_seconds'] = round(time.perf_counter()-start, 3)
                job.state = 'analyzing'
                db.commit()
                def progress(index, total, png):
                    storage.put(document.id, f'page-{index}', png)
                    job.progress = int((index+1)/total*100)
                    job.updated_at = now()
                    db.commit()
                start = time.perf_counter()
                analysis = analyze(pdf, progress)
                timings['analysis_seconds'] = round(time.perf_counter()-start, 3)
                timings.update(analysis['timings'])
                storage.put(document.id, 'analysis', json.dumps(analysis).encode())
                document.status, document.error = 'ready', None
                job.state, job.progress, job.timings = 'completed', 100, timings
                audit(db, 'detection_completed', document.id, counts=dict(Counter(f['category'] for f in analysis['findings'])))
                db.commit()
        except Exception as error:
            message = str(error) if isinstance(error, ProcessingError) else 'Processing failed. Check the document or retry the job.'
            document.status, document.error = 'failed', message
            job.state, job.error = 'failed', message
            audit(db, 'processing_failed', document.id)
            db.commit()


@celery.task(name='finalize_document', bind=True, max_retries=60)
def finalize_document(task, job_id):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        document_id = job.document_id
    with job_lock(document_id) as acquired:
        if not acquired:
            raise task.retry(countdown=30)
        _finalize_document(job_id)


def _finalize_document(job_id):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.state == 'completed':
            return
        export = db.get(Export, job.export_id)
        if not export:
            return
        try:
            export.status, job.state = 'processing', 'exporting'
            db.commit()
            start = time.perf_counter()
            def progress(index, total):
                job.progress, job.updated_at = int((index+1)/total*100), now()
                db.commit()
            pdf = flatten(storage.get(export.document_id, 'converted'), export.selections, progress)
            storage.put(export.document_id, f'export-{export.id}', pdf)
            export.checksum = hashlib.sha256(pdf).hexdigest()
            data = report(export)
            storage.put(export.document_id, f'report-{export.id}.json', json.dumps(data).encode())
            storage.put(export.document_id, f'report-{export.id}.pdf', report_pdf(data))
            export.status, job.state, job.progress = 'ready', 'completed', 100
            job.timings = {'export_seconds': round(time.perf_counter()-start, 3)}
            audit(db, 'finalized', export.document_id, export_id=export.id, version=export.version, checksum=export.checksum)
            db.commit()
        except Exception:
            export.status, job.state, job.error = 'failed', 'failed', 'Export failed. Retry the job.'
            audit(db, 'export_failed', export.document_id, export_id=export.id)
            db.commit()
