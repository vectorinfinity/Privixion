"""Real local engines: automatically enabled in the Docker verification image."""
import io
import shutil
import importlib.util
import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
from app.processing import convert, analyze, flatten, render_page
from .test_redaction import make_pdf


@pytest.mark.skipif(not shutil.which('tesseract'), reason='Tesseract is installed in the Docker image')
def test_real_ocr_scanned_and_mixed_pdf():
    import pytesseract
    image=Image.new('RGB',(1600,500),'white')
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',44)
    draw=ImageDraw.Draw(image)
    draw.text((80,100),'Email: redaction@example.com',font=font,fill='black')
    draw.text((80,250),'PUBLIC INFORMATION',font=font,fill='black')
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    stream=io.BytesIO(); c=canvas.Canvas(stream,pagesize=(800,450))
    c.drawString(40,415,'PAN ABCPD1234E')
    c.drawImage(ImageReader(image),0,100,800,250); c.save()
    data=analyze(stream.getvalue(),use_nlp=False)
    email=next(f for f in data['findings'] if f['category']=='EMAIL')
    assert any(f['category']=='IN_PAN' for f in data['findings'])
    result=flatten(stream.getvalue(),[email])
    text=pytesseract.image_to_string(Image.open(io.BytesIO(render_page(result,0))),lang='eng')
    assert 'redaction@example.com' not in text
    assert 'PUBLIC INFORMATION' in text
    assert 'ABCPD1234E' in text


@pytest.mark.skipif(not shutil.which('soffice'), reason='LibreOffice is installed in the Docker image')
@pytest.mark.parametrize('kind',['docx','xlsx','pptx'])
def test_real_office_conversion(kind,tmp_path):
    from .fixtures import office_bytes
    result=convert(office_bytes(kind),'{}'.format(kind),tmp_path)
    assert len(PdfReader(io.BytesIO(result)).pages)>0
    data=analyze(result,use_nlp=False,use_ocr=False)
    assert any(f['category']=='EMAIL' for f in data['findings'])


@pytest.mark.skipif(importlib.util.find_spec('presidio_analyzer') is None, reason='Presidio is installed in the Docker image')
def test_local_nlp_loads_without_network(monkeypatch):
    import spacy
    if not spacy.util.is_package('en_core_web_sm'):
        pytest.skip('English model is baked into the Docker image')
    def forbidden_network(*args,**kwargs):
        raise AssertionError('Detection attempted outbound HTTP')
    monkeypatch.setattr('requests.sessions.Session.request',forbidden_network)
    from app.detection import matches
    output=matches('John Smith lives in New Delhi. Email john@example.com.')
    assert any(item[2]=='NAME' for item in output)
    assert any(item[2]=='EMAIL' for item in output)
