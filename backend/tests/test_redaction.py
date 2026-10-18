import io
import json
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from app.processing import analyze, flatten, convert, ProcessingError, render_page
from app.detection import aadhaar, luhn, matches, validate_pattern
from app import storage


def make_pdf(rotation=0, crop=False, image=False):
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(400,300))
    c.drawString(50,230,'Email: redaction@example.com')
    c.drawString(50,180,'PUBLIC INFORMATION')
    if image:
        c.drawImage(ImageReader(Image.new('RGB',(30,30),'red')), 200,100,30,30)
    c.save()
    writer = PdfWriter()
    page = PdfReader(io.BytesIO(out.getvalue())).pages[0]
    if crop:
        page.cropbox.lower_left = (20,30)
        page.cropbox.upper_right = (380,280)
    if rotation:
        page.rotate(rotation)
    writer.add_page(page)
    writer.add_metadata({'/Title':'Sensitive metadata'})
    writer.add_attachment('secret.txt', b'redaction@example.com')
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def test_crypto_authentication_and_original_preservation():
    document_id = '01234567-1234-1234-1234-012345678901'
    storage.put(document_id, 'original', b'sensitive-original')
    encrypted = storage.artifact_path(document_id, 'original').read_bytes()
    assert b'sensitive-original' not in encrypted
    assert storage.get(document_id, 'original') == b'sensitive-original'
    from cryptography.exceptions import InvalidTag
    import pytest
    with pytest.raises(InvalidTag):
        storage.decrypt(encrypted, 'wrong/context')
    with pytest.raises(ValueError):
        storage.artifact_path('../escape', 'original')
    storage.put(document_id, 'export-1', b'redacted')
    assert storage.get(document_id, 'original') == b'sensitive-original'


def test_indian_rules_and_checksums():
    assert aadhaar('234567890124')
    assert not aadhaar('234567890123')
    assert not aadhaar('123456789012')
    assert luhn('4111 1111 1111 1111')
    assert not luhn('4111 1111 1111 1112')
    text = 'PAN ABCPD1234E; Aadhaar 2345 6789 0124; phone +91 98765 43210; IFSC HDFC0001234; passport A1234567; bank account 123456789012; card 4111 1111 1111 1111'
    categories = {m[2] for m in matches(text, use_nlp=False)}
    assert {'IN_PAN','IN_AADHAAR','IN_PHONE','IN_IFSC','IN_PASSPORT','BANK_ACCOUNT','CREDIT_CARD'} <= categories


def test_pattern_restrictions():
    import pytest
    validate_pattern(r'REF-[0-9]{6}')
    for pattern in [r'(a+)+$', r'a{1000}', r'foo|bar', r'(a)\1']:
        with pytest.raises(ValueError):
            validate_pattern(pattern)


def test_rotated_cropped_coordinates_and_irrecoverable_export():
    for rotation in (0,90,180,270):
        source = make_pdf(rotation, crop=True, image=True)
        analysis = analyze(source, use_nlp=False, use_ocr=False)
        finding = next(f for f in analysis['findings'] if f['category'] == 'EMAIL')
        assert all(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 for x0,y0,x1,y1 in finding['rects'])
        selection = {'page':0,'rects':finding['rects']}
        output = flatten(source, [selection, selection])
        reader = PdfReader(io.BytesIO(output))
        assert not reader.pages[0].extract_text().strip()
        assert '/Annots' not in reader.pages[0]
        assert '/AcroForm' not in reader.trailer['/Root']
        assert '/Names' not in reader.trailer['/Root']
        assert 'Sensitive metadata' not in str(reader.metadata)
        assert b'redaction@example.com' not in output
        original_image = Image.open(io.BytesIO(render_page(source,0,300/72))).convert('RGB')
        final_image = Image.open(io.BytesIO(render_page(output,0,300/72))).convert('RGB')
        # The mapped detection must actually cover the sensitive glyph area.
        for x0,y0,x1,y1 in finding['rects']:
            x,y = int((x0+x1)/2*final_image.width), int((y0+y1)/2*final_image.height)
            assert max(final_image.getpixel((x,y))) < 20
        # A public area outside the sensitive selection remains unchanged.
        assert final_image.getpixel((5,5)) == original_image.getpixel((5,5))


def test_whole_page_blackout():
    result = flatten(make_pdf(), [{'page':0,'rects':[[0,0,1,1]]}])
    image = Image.open(io.BytesIO(render_page(result,0))).convert('RGB')
    assert image.getextrema() == ((0,0),(0,0),(0,0))


def test_conversions_and_password_rejection(tmp_path):
    txt = convert(b'PAN ABCPD1234E\nPublic text', 'txt', tmp_path)
    assert 'ABCPD1234E' in PdfReader(io.BytesIO(txt)).pages[0].extract_text()
    frames = [Image.new('RGB',(200,100),'white'),Image.new('RGB',(100,200),'green')]
    for kind, format in [('png','PNG'),('jpeg','JPEG'),('tiff','TIFF')]:
        stream = io.BytesIO()
        frames[0].save(stream,format=format,save_all=format=='TIFF',append_images=frames[1:] if format=='TIFF' else [])
        pdf = convert(stream.getvalue(),kind,tmp_path)
        assert len(PdfReader(io.BytesIO(pdf)).pages) == (2 if kind=='tiff' else 1)
    writer = PdfWriter()
    writer.add_blank_page(200,200)
    writer.encrypt('secret')
    encrypted = io.BytesIO(); writer.write(encrypted)
    import pytest
    with pytest.raises(ProcessingError, match='Password-protected'):
        convert(encrypted.getvalue(),'pdf',tmp_path)
    with pytest.raises(ProcessingError):
        convert(b'not a pdf','pdf',tmp_path)


def test_native_multiline_findings():
    page = {'index':0,'words':[{'text':'redaction@example.com','rect':[.1,.1,.4,.15]}, {'text':'redaction@example.com','rect':[.1,.3,.4,.35]}]}
    from app.detection import findings
    detected = findings(page,use_nlp=False)
    assert len([f for f in detected if f['category']=='EMAIL']) == 2
