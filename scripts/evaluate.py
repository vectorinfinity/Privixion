"""Detection evaluation and reproducible 100-page benchmarks; run inside worker image."""
import argparse
import io
import json
import platform
import sys
import time
import os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
sys.path.insert(0,str(Path.cwd()))
from app.detection import matches
from app.processing import analyze,flatten,convert
from app.storage import workspace
from reportlab.pdfgen import canvas

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--dataset',type=Path)
parser.add_argument('--benchmark',action='store_true')
parser.add_argument('--no-nlp',action='store_true')
parser.add_argument('--no-ocr',action='store_true')
args=parser.parse_args()


def hardware_details():
    details={'platform':platform.platform(),'processor':platform.processor() or platform.machine(),
             'logical_cpus':os.cpu_count(),'python':platform.python_version()}
    try:
        quota,period=Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota!='max': details['cpu_quota_cores']=int(quota)/int(period)
    except (OSError,ValueError):
        pass
    try:
        limit=Path('/sys/fs/cgroup/memory.max').read_text().strip()
        if limit!='max': details['memory_limit_bytes']=int(limit)
    except (OSError,ValueError):
        pass
    return details
if args.dataset:
    counts={}
    for sample in json.loads(args.dataset.read_text()):
        expected={(s['start'],s['end'],s['category']) for s in sample['entities']}
        actual={(a,b,c) for a,b,c,_ in matches(sample['text'],use_nlp=not args.no_nlp)}
        for category in {e[2] for e in expected|actual}:
            record=counts.setdefault(category,{'true_positive':0,'false_positive':0,'false_negative':0})
            record['true_positive']+=sum(e[2]==category for e in expected&actual)
            record['false_positive']+=sum(e[2]==category for e in actual-expected)
            record['false_negative']+=sum(e[2]==category for e in expected-actual)
    for record in counts.values():
        tp,fp,fn=(record[k] for k in ('true_positive','false_positive','false_negative'))
        record['precision']=tp/(tp+fp) if tp+fp else None
        record['recall']=tp/(tp+fn) if tp+fn else None
    print(json.dumps({'evaluation':counts,'exact_span_matching':True,'nlp_enabled':not args.no_nlp},indent=2))
if args.benchmark:
    out=io.BytesIO(); c=canvas.Canvas(out,pagesize=(595,842))
    for i in range(100):
        c.drawString(40,780,'Email: redaction@example.com')
        c.drawString(40,750,'Public information. Synthetic benchmark page '+str(i+1)); c.showPage()
    c.save()
    start=time.perf_counter()
    with workspace() as directory: pdf=convert(out.getvalue(),'pdf',directory)
    conversion=time.perf_counter()-start
    start=time.perf_counter(); data=analyze(pdf,use_nlp=not args.no_nlp,use_ocr=not args.no_ocr)
    analysis=time.perf_counter()-start
    assert len({f['page'] for f in data['findings'] if f['category']=='EMAIL'})==100, 'Benchmark email detection failed'
    start=time.perf_counter(); output=flatten(pdf,[f for f in data['findings'] if f['category']=='EMAIL']); export=time.perf_counter()-start
    print(json.dumps({'hardware':hardware_details(),'pages':100,'ocr':not args.no_ocr,'nlp':not args.no_nlp,'conversion_seconds':conversion,'analysis_seconds':analysis,**data['timings'],'export_seconds':export,'output_bytes':len(output),'total_seconds':conversion+analysis+export},indent=2))
