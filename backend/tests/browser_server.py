"""Synthetic browser QA harness. Never deploy this server."""
import os
import sys
import tempfile
import threading
from pathlib import Path

if os.environ.get('PRIVIXION_BROWSER_TEST')!='1':
    raise SystemExit('This test server requires PRIVIXION_BROWSER_TEST=1')
directory=tempfile.TemporaryDirectory(prefix='privixion-browser-')
root=Path(directory.name)
os.environ['DATABASE_URL']='sqlite:///'+str(root/'browser.db').replace('\\','/')
os.environ['DATA_DIR']=str(root/'artifacts')
os.environ['ARTIFACT_KEY_FILE']=str(root/'key')
os.environ['ADMIN_HASH_FILE']=str(root/'password')
os.environ['PUBLIC_ORIGIN']='http://127.0.0.1:5173'
(root/'key').write_bytes(os.urandom(32))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from argon2 import PasswordHasher
(root/'password').write_text(PasswordHasher().hash('test-password-12345'))
from app.main import app
from app import tasks
from app.processing import analyze as real_analyze
from app.db import engine
import atexit
atexit.register(engine.dispose)

tasks.analyze=lambda pdf,progress: real_analyze(pdf,progress,use_nlp=False,use_ocr=False)
tasks.process_document.delay=lambda job_id: threading.Thread(target=tasks.process_document.run,args=(job_id,),daemon=True).start()
tasks.finalize_document.delay=lambda job_id: threading.Thread(target=tasks.finalize_document.run,args=(job_id,),daemon=True).start()

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8000,log_level='warning')
