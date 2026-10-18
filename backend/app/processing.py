import io
import math
import subprocess
import textwrap
import time
from contextlib import closing
from pathlib import Path
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageSequence
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from .config import settings
from .detection import findings

Image.MAX_IMAGE_PIXELS = 40_000_000
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


class ProcessingError(Exception):
    pass


def safe_render(page, scale):
    width, height = page.get_size()
    if width <= 0 or height <= 0 or width*height*scale*scale > Image.MAX_IMAGE_PIXELS:
        raise ProcessingError('Page dimensions exceed safe rendering limits.')
    return page.render(scale=scale, may_draw_forms=True)


def convert(source: bytes, kind: str, directory: Path):
    source_path = directory / ('input.' + kind)
    source_path.write_bytes(source)
    if kind == 'pdf':
        try:
            reader = PdfReader(io.BytesIO(source))
            if reader.is_encrypted:
                raise ProcessingError('Password-protected PDFs are unsupported. Upload an unlocked copy.')
            if not 0 < len(reader.pages) <= settings.max_pages:
                raise ProcessingError('Document exceeds the configured page limit or has no pages.')
        except ProcessingError:
            raise
        except Exception:
            raise ProcessingError('The PDF could not be read.') from None
        return source
    if kind in {'docx', 'xlsx', 'pptx'}:
        try:
            subprocess.run(['soffice', '--headless', '--nologo', '--nodefault', '--nofirststartwizard',
                            f'-env:UserInstallation={directory.joinpath("lo-profile").as_uri()}',
                            '--convert-to', 'pdf', '--outdir', str(directory), str(source_path)],
                           check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = directory / 'input.pdf'
            if not result.exists():
                raise ProcessingError('Office conversion failed. Check that the file is valid and unlocked.')
            return convert(result.read_bytes(), 'pdf', directory)
        except (subprocess.SubprocessError, OSError):
            raise ProcessingError('Office conversion failed or exceeded its time limit.') from None
    output = io.BytesIO()
    c = canvas.Canvas(output, pagesize=(595.28, 841.89), pageCompression=1)
    if kind == 'txt':
        try:
            text = source.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ProcessingError('TXT inputs must use UTF-8 encoding.') from None
        font = 'Courier'
        if Path(FONT).exists():
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            if 'DejaVu' not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont('DejaVu', FONT))
            font = 'DejaVu'
        lines = [line for paragraph in text.splitlines() for line in (textwrap.wrap(paragraph.expandtabs(4), 78) or [''])] or ['']
        if math.ceil(len(lines) / 55) > settings.max_pages:
            raise ProcessingError('Document exceeds the configured page limit.')
        for start in range(0, len(lines), 55):
            c.setFont(font, 10)
            for i, line in enumerate(lines[start:start+55]):
                c.drawString(36, 805-i*14, line)
            c.showPage()
    else:
        try:
            with Image.open(io.BytesIO(source)) as image:
                if image.format not in {'PNG', 'JPEG', 'TIFF'}:
                    raise ProcessingError('Unsupported image contents.')
                for i, frame in enumerate(ImageSequence.Iterator(image)):
                    if i >= settings.max_pages:
                        raise ProcessingError('Document exceeds the configured page limit.')
                    from PIL import ImageOps
                    rgba = ImageOps.exif_transpose(frame).convert('RGBA')
                    rgb = Image.new('RGB', rgba.size, 'white')
                    rgb.paste(rgba, mask=rgba.getchannel('A'))
                    w, h = rgb.size
                    scale = min(595.28/w, 841.89/h)
                    size = (w*scale, h*scale)
                    c.setPageSize(size)
                    c.drawImage(ImageReader(rgb), 0, 0, *size)
                    c.showPage()
        except ProcessingError:
            raise
        except Exception:
            raise ProcessingError('Image could not be decoded or exceeds safe pixel limits.') from None
    c.save()
    return output.getvalue()


def normalized_box(box, converter, width, height):
    left, bottom, right, top = box
    points = [converter.to_bitmap(x, y) for x, y in ((left,bottom), (left,top), (right,bottom), (right,top))]
    xs, ys = zip(*points)
    return [max(0., min(1., min(xs)/width)), max(0., min(1., min(ys)/height)),
            max(0., min(1., max(xs)/width)), max(0., min(1., max(ys)/height))]


def overlap(a, b):
    area = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    return area / max(1e-10, (a[2]-a[0])*(a[3]-a[1]))


def native_words(page, bitmap):
    converter = bitmap.get_posconv(page)
    words, chars, boxes = [], [], []
    with closing(page.get_textpage()) as textpage:
        def flush():
            if chars and boxes:
                words.append({'text': ''.join(chars), 'rect': [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)], 'source': 'native'})
            chars.clear()
            boxes.clear()
        for i in range(textpage.count_chars()):
            char = textpage.get_text_range(i, 1)
            if not char or char.isspace():
                flush()
                continue
            rect = normalized_box(textpage.get_charbox(i), converter, bitmap.width, bitmap.height)
            if rect[2] <= rect[0] or rect[3] <= rect[1]:
                continue
            chars.append(char)
            boxes.append(rect)
        flush()
    return words


def ocr_words(image):
    import pytesseract
    data = pytesseract.image_to_data(image, lang='eng', config='--psm 3', output_type=pytesseract.Output.DICT, timeout=120)
    w, h = image.size
    output = []
    for i, text in enumerate(data['text']):
        if text.strip() and float(data['conf'][i]) >= 25:
            x, y, bw, bh = (data[k][i] for k in ('left', 'top', 'width', 'height'))
            output.append({'text': text.strip(), 'rect': [x/w, y/h, (x+bw)/w, (y+bh)/h], 'source': 'ocr'})
    return output


def analyze(pdf: bytes, progress=lambda *_: None, custom_rules=(), use_nlp=True, use_ocr=True):
    pages, detected = [], []
    timings = {'render_extract_seconds': 0., 'ocr_seconds': 0., 'detection_seconds': 0.}
    with pdfium.PdfDocument(pdf) as doc:
        if not 0 < len(doc) <= settings.max_pages:
            raise ProcessingError('Document exceeds the configured page limit.')
        doc.init_forms()
        for index in range(len(doc)):
            with closing(doc[index]) as page:
                # Render the complete visible page, including form appearances.
                stage_start = time.perf_counter()
                with closing(safe_render(page, 150/72)) as bitmap:
                    image = bitmap.to_pil().convert('RGB')
                    native = native_words(page, bitmap)
                    timings['render_extract_seconds'] += time.perf_counter()-stage_start
                    stage_start = time.perf_counter()
                    scanned = ocr_words(image) if use_ocr else []
                    timings['ocr_seconds'] += time.perf_counter()-stage_start
                    words = native + [word for word in scanned if not any(overlap(word['rect'], nw['rect']) > .35 for nw in native)]
                    # Group by approximate line to reconstruct text across words.
                    line_height = sorted([w['rect'][3]-w['rect'][1] for w in words])[len(words)//2] if words else .015
                    if page.get_rotation() == 0:
                        words.sort(key=lambda word: (round(word['rect'][1]/max(.005, line_height*.7)), word['rect'][0]))
                    metadata = {'index': index, 'width': image.width * 72/150, 'height': image.height * 72/150, 'words': words}
                    pages.append(metadata)
                    stage_start = time.perf_counter()
                    detected.extend(findings(metadata, custom_rules, use_nlp))
                    timings['detection_seconds'] += time.perf_counter()-stage_start
                    png = io.BytesIO()
                    image.save(png, format='PNG')
                    progress(index, len(doc), png.getvalue())
    return {'pages': pages, 'findings': detected, 'timings': {key: round(value, 3) for key,value in timings.items()}}


def flatten(pdf: bytes, selections: list, progress=lambda *_: None):
    output = io.BytesIO()
    c = canvas.Canvas(output, pageCompression=1)
    c.setTitle('Redacted document')
    c.setAuthor('')
    c.setSubject('')
    with pdfium.PdfDocument(pdf) as doc:
        doc.init_forms()
        for index in range(len(doc)):
            with closing(doc[index]) as page:
                with closing(safe_render(page, 300/72)) as bitmap:
                    image = bitmap.to_pil().convert('RGB')
                    draw = ImageDraw.Draw(image)
                    for selection in selections:
                        if selection['page'] == index:
                            for x0, y0, x1, y1 in selection['rects']:
                                # Cover boundary pixels and antialiasing around glyphs.
                                draw.rectangle((max(0, math.floor(x0*image.width)-2), max(0, math.floor(y0*image.height)-2),
                                                min(image.width, math.ceil(x1*image.width)+2), min(image.height, math.ceil(y1*image.height)+2)), fill='black')
                    w, h = image.width*72/300, image.height*72/300
                    c.setPageSize((w, h))
                    c.drawImage(ImageReader(image), 0, 0, w, h)
                    c.showPage()
                    # Preview derives from the finalized pixel image; later re-render exact PDF for UI.
                    progress(index, len(doc))
    c.save()
    return output.getvalue()


def render_page(pdf, index, scale=150/72):
    with pdfium.PdfDocument(pdf) as doc:
        if not 0 <= index < len(doc):
            raise IndexError('Page does not exist')
        doc.init_forms()
        with closing(doc[index]) as page, closing(safe_render(page, scale)) as bitmap:
            output = io.BytesIO()
            bitmap.to_pil().save(output, format='PNG')
            return output.getvalue()
