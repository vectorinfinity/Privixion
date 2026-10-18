import io
import json
from app.db import SessionLocal, Job, Export, Audit, Document
from app.tasks import finalize_document, process_document
from app import storage
from sqlalchemy import select
from .test_redaction import make_pdf


def prepared(client):
    response = client.post('/api/documents',files=[('files',('fixture.pdf',make_pdf(),'application/pdf'))])
    assert response.status_code == 200
    document_id = response.json()['documents'][0]['id']
    from app.processing import analyze, render_page
    data = analyze(make_pdf(),use_nlp=False,use_ocr=False)
    storage.put(document_id,'converted',make_pdf())
    storage.put(document_id,'analysis',json.dumps(data).encode())
    storage.put(document_id,'page-0',render_page(make_pdf(),0))
    with SessionLocal() as db:
        db.get(Document,document_id).status='ready'
        job = db.scalar(select(Job).where(Job.document_id==document_id)); job.state='completed'; db.commit()
    return document_id, client.get(f'/api/documents/{document_id}').json()


def test_auth_csrf_and_login_limits(client):
    assert client.get('/api/documents').status_code == 401
    assert client.post('/api/auth/login',headers={'Origin':'https://evil.invalid'},json={'password':'test-password-12345'}).status_code == 403
    login = client.post('/api/auth/login',json={'password':'test-password-12345'})
    assert login.status_code == 200
    assert 'HttpOnly' in login.headers['set-cookie'] and 'SameSite=strict' in login.headers['set-cookie']
    assert client.post('/api/auth/logout').status_code == 403
    client.headers['X-CSRF-Token']=login.json()['csrf']
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/documents').status_code == 401
    for _ in range(10):
        assert client.post('/api/auth/login',json={'password':'wrong'}).status_code==401
    assert client.post('/api/auth/login',json={'password':'wrong'}).status_code==429


def test_version_review_preview_confirmation_and_audit(signed_in):
    client=signed_in
    document_id, detail=prepared(client)
    assert client.post(f'/api/documents/{document_id}/finalize',json={'version':1}).status_code==409
    draft=detail['draft']
    for f in detail['findings']:
        draft['decisions'][f['id']]='accepted'
        draft['selections'].append({k:f[k] for k in ['id','page','rects','category']} | {'source':'automatic'})
    saved=client.put(f'/api/documents/{document_id}/draft',json=draft)
    assert saved.status_code==200
    assert client.put(f'/api/documents/{document_id}/draft',json=draft).status_code==409
    version=saved.json()['version']
    result=client.post(f'/api/documents/{document_id}/finalize',json={'version':version})
    assert result.status_code==200
    export_id=result.json()['id']
    with SessionLocal() as db:
        job=db.scalar(select(Job).where(Job.export_id==export_id))
        finalize_document.run(job.id)
    assert client.get(f'/api/exports/{export_id}/download').status_code==409
    assert client.get(f'/api/exports/{export_id}/pages/0').headers['content-type']=='image/png'
    assert client.get(f'/api/exports/{export_id}/pages/1').status_code==404
    # Editing the current draft must never mutate the frozen export snapshot.
    draft['version']=version; draft['selections']=[]; draft['decisions']={f['id']:'rejected' for f in detail['findings']}
    assert client.put(f'/api/documents/{document_id}/draft',json=draft).status_code==200
    with SessionLocal() as db:
        assert len(db.get(Export,export_id).selections)>0
    assert client.post(f'/api/exports/{export_id}/confirm').status_code==200
    download=client.get(f'/api/exports/{export_id}/download')
    assert download.status_code==200
    assert b'redaction@example.com' not in download.content
    report=client.get(f'/api/exports/{export_id}/report/json').json()
    assert report['selection_count']>0 and len(report['checksum_sha256'])==64
    assert 'redaction@example.com' not in json.dumps(report)
    assert client.get(f'/api/exports/{export_id}/report/pdf').content.startswith(b'%PDF')
    batch=client.post('/api/batch/download',json={'export_ids':[export_id]})
    assert batch.status_code==200 and batch.content.startswith(b'PK')
    events=client.get(f'/api/audit?document_id={document_id}').json()
    assert {'uploaded','draft_saved','finalized','export_confirmed','downloaded'} <= {e['action'] for e in events}
    assert 'redaction@example.com' not in json.dumps(events)
    original=storage.get(document_id,'original')
    assert b'%PDF' in original
    assert client.delete(f'/api/documents/{document_id}').status_code==200
    assert not storage.artifact_path(document_id,'original').exists()
    assert client.get(f'/api/documents/{document_id}').status_code==404
    assert any(e['action']=='deleted' for e in client.get('/api/audit').json())


def test_search_templates_invalid_coordinates_and_batch_limits(signed_in):
    client=signed_in; document_id, detail=prepared(client)
    results=client.post(f'/api/documents/{document_id}/search',json={'term':'redaction@example.com'}).json()
    assert len(results)==1 and results[0]['rects']
    assert client.post(f'/api/documents/{document_id}/search',json={'term':'.*'}).json()==[]
    bad=detail['draft'] | {'selections':[{'id':'bad','page':0,'rects':[[0,0,2,1]],'category':'MANUAL','source':'manual'}]}
    assert client.put(f'/api/documents/{document_id}/draft',json=bad).status_code==422
    template=client.post('/api/templates',json={'name':'Reference','rules':[{'category':'CUSTOM_EMAIL','pattern':r'redaction@example\.com'}]})
    assert template.status_code==200
    apply=client.post(f'/api/documents/{document_id}/templates/apply',json={'template_id':template.json()['id'],'version':detail['draft']['version']})
    assert apply.status_code==200 and apply.json()['count']==1
    assert client.put(f"/api/templates/{template.json()['id']}",json={'name':'Updated','rules':[{'category':'CUSTOM_EMAIL','pattern':r'other@example\.com'}]}).status_code==200
    # Updating the reusable template cannot rewrite prior detections.
    assert any(f['category']=='CUSTOM_EMAIL' for f in client.get(f'/api/documents/{document_id}').json()['findings'])
    assert client.post('/api/templates',json={'name':'Bad','rules':[{'category':'CUSTOM_BAD','pattern':'(a+)+$'}]}).status_code==422
    assert client.post('/api/documents',files=[('files',(f'{i}.txt',b'x','text/plain')) for i in range(21)]).status_code==400
    unknown=client.post('/api/documents',files=[('files',('x.exe',b'x','application/octet-stream'))]).json()
    assert len(unknown['failures'])==1


def test_processing_failure_retry_and_original(signed_in):
    client=signed_in
    result=client.post('/api/documents',files=[('files',('bad.pdf',b'broken','application/pdf'))])
    document_id=result.json()['documents'][0]['id']
    with SessionLocal() as db:
        job=db.scalar(select(Job).where(Job.document_id==document_id))
        process_document.run(job.id)
    detail=client.get(f'/api/documents/{document_id}').json()
    assert detail['status']=='failed'
    jobs=client.get(f'/api/documents/{document_id}/jobs').json()
    assert client.post(f"/api/jobs/{jobs[0]['id']}/retry").status_code==200
    assert storage.get(document_id,'original')==b'broken'
    assert client.delete(f'/api/documents/{document_id}').status_code==409
