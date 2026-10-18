"""Small standards-based OOXML fixtures, with no optional Office SDK dependency."""
import io
import zipfile


def office_bytes(kind):
    text='Email: redaction@example.com'
    relns='http://schemas.openxmlformats.org/package/2006/relationships'
    offns='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    if kind=='docx':
        target='word/document.xml'; mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
        main=f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:sectPr/></w:body></w:document>'
        extras={}
    elif kind=='xlsx':
        target='xl/workbook.xml'; mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'
        main=f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="{offns}"><sheets><sheet name="Sample" sheetId="1" r:id="rId1"/></sheets></workbook>'
        extras={'xl/_rels/workbook.xml.rels':f'<Relationships xmlns="{relns}"><Relationship Id="rId1" Type="{offns}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
                'xl/worksheets/sheet1.xml':f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cols><col min="1" max="1" width="50" customWidth="1"/></cols><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{text}</t></is></c></row></sheetData></worksheet>'}
    else:
        target='ppt/presentation.xml'; mime='application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml'
        main=f'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="{offns}"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
        extras={'ppt/_rels/presentation.xml.rels':f'<Relationships xmlns="{relns}"><Relationship Id="rId1" Type="{offns}/slide" Target="slides/slide1.xml"/></Relationships>',
                'ppt/slides/slide1.xml':f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Text"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="500000" y="500000"/><a:ext cx="8000000" cy="1000000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="2200"/><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'}
    overrides=f'<Override PartName="/{target}" ContentType="{mime}"/>'
    if kind=='xlsx':
        overrides+='<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    if kind=='pptx':
        overrides+='<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml',f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>{overrides}</Types>')
        archive.writestr('_rels/.rels',f'<Relationships xmlns="{relns}"><Relationship Id="rId1" Type="{offns}/officeDocument" Target="{target}"/></Relationships>')
        archive.writestr(target,main)
        for name,content in extras.items(): archive.writestr(name,content)
    return output.getvalue()
