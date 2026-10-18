import io
import pytest
from PIL import Image
from reportlab.pdfgen import canvas
from app.config import settings
from app.processing import analyze,convert,ProcessingError


def test_file_limit_and_unknown_pages(signed_in,monkeypatch):
    monkeypatch.setattr(settings,'max_bytes',4)
    response=signed_in.post('/api/documents',files=[('files',('big.txt',b'12345','text/plain'))])
    assert response.status_code==200 and len(response.json()['failures'])==1
    assert signed_in.get('/api/documents/missing/pages/0').status_code==404


def test_page_and_render_limits(tmp_path,monkeypatch):
    out=io.BytesIO(); c=canvas.Canvas(out,pagesize=(200,200))
    for _ in range(2): c.drawString(20,100,'Page'); c.showPage()
    c.save(); monkeypatch.setattr(settings,'max_pages',1)
    with pytest.raises(ProcessingError,match='page limit'):
        convert(out.getvalue(),'pdf',tmp_path)
    monkeypatch.setattr(settings,'max_pages',500)
    huge=io.BytesIO(); c=canvas.Canvas(huge,pagesize=(20000,20000)); c.drawString(20,100,'Huge'); c.save()
    with pytest.raises(ProcessingError,match='safe rendering'):
        analyze(huge.getvalue(),use_nlp=False,use_ocr=False)


def test_transparent_image_uses_white_background(tmp_path):
    source=io.BytesIO(); Image.new('RGBA',(100,100),(0,0,0,0)).save(source,format='PNG')
    result=convert(source.getvalue(),'png',tmp_path)
    from app.processing import render_page
    image=Image.open(io.BytesIO(render_page(result,0))).convert('RGB')
    assert image.getpixel((50,50))==(255,255,255)


def test_stalled_job_retry(signed_in):
    from datetime import timedelta
    from app.db import SessionLocal,Job,now
    result=signed_in.post('/api/documents',files=[('files',('queued.txt',b'synthetic','text/plain'))])
    document_id=result.json()['documents'][0]['id']
    job=signed_in.get(f'/api/documents/{document_id}/jobs').json()[0]
    with SessionLocal() as db:
        record=db.get(Job,job['id']); record.state='analyzing'; record.updated_at=now()-timedelta(hours=2); db.commit()
    assert signed_in.get(f'/api/documents/{document_id}/jobs').json()[0]['stalled']
    assert signed_in.post(f"/api/jobs/{job['id']}/retry").status_code==200
