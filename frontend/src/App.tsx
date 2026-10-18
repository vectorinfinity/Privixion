import {useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent} from 'react';
import {ShieldCheck, Upload, FileText, ArrowRight, Search, ScanLine, MousePointer2, Square, Undo2, Redo2, ZoomIn, ZoomOut, ChevronLeft, ChevronRight, Check, X, Download, Settings2, History, LockKeyhole, LogOut, Trash2, LoaderCircle, Eye, Save, Layers, Plus, AlertCircle} from 'lucide-react';
import {api, setCsrf, batchDownload} from './api';
import type {Detail, Doc, Draft, Event, Export, Finding, Job, Rect, Selection, Template} from './types';

const label = (value: string) => value.toLowerCase().replaceAll('_', ' ');
const boxStyle = ([x0,y0,x1,y1]: Rect): CSSProperties => ({left: `${x0*100}%`, top: `${y0*100}%`, width: `${(x1-x0)*100}%`, height: `${(y1-y0)*100}%`});
const clone = <T,>(value: T): T => structuredClone(value);

export default function App() {
  const [auth, setAuth] = useState<boolean | null>(null);
  const [password, setPassword] = useState('');
  const [docs, setDocs] = useState<Doc[]>([]);
  const [doc, setDoc] = useState<Detail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [undo, setUndo] = useState<Draft[]>([]);
  const [redo, setRedo] = useState<Draft[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [page, setPage] = useState(0);
  const [zoom, setZoom] = useState(85);
  const [mode, setMode] = useState<'text'|'rectangle'>('text');
  const [drag, setDrag] = useState<Rect | null>(null);
  const start = useRef<[number, number] | null>(null);
  const [search, setSearch] = useState('');
  const [hits, setHits] = useState<Selection[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [panel, setPanel] = useState<'findings'|'selections'>('findings');
  const [filter, setFilter] = useState('pending');
  const [modal, setModal] = useState<'templates'|'audit'|'delete' | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateName, setTemplateName] = useState('');
  const [ruleCategory, setRuleCategory] = useState('CUSTOM_REFERENCE');
  const [rulePattern, setRulePattern] = useState('REF-[0-9]{6}');
  const [events, setEvents] = useState<Event[]>([]);
  const [preview, setPreview] = useState<Export | null>(null);
  const [previewPage, setPreviewPage] = useState(0);
  const [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const [limits, setLimits] = useState({max_file_mb:50,max_pages:500,max_batch:20});
  const uploadRef = useRef<HTMLInputElement>(null);
  const sessionExpired = useRef(false);
  const refreshDocs = useCallback(async () => setDocs(await api<Doc[]>('/documents')), []);
  useEffect(() => { api<{csrf: string}>('/auth/me').then(data => {setCsrf(data.csrf); setAuth(true);}).catch(() => setAuth(false)); }, []);
  useEffect(() => { if (auth) refreshDocs().catch(e => setError(e.message)); }, [auth, refreshDocs]);
  useEffect(() => {if(auth) api<typeof limits>('/config').then(setLimits).catch(()=>{});},[auth]);
  useEffect(() => {
    if (!auth) return;
    let active = true;
    const timer = setInterval(() => {
      refreshDocs().catch(() => { if (!sessionExpired.current) {sessionExpired.current = true; setError('Session expired or server unavailable. Save work if possible, then sign in again.');} });
      if (doc) {
        api<Job[]>(`/documents/${doc.id}/jobs`).then(next => {if(active) setJobs(next);}).catch(() => {});
        if (doc.status !== 'ready' || doc.exports.some(e => ['queued','processing'].includes(e.status))) {
          api<Detail>(`/documents/${doc.id}`).then(next => {
            if (!active) return;
            setDoc(next);
            if (doc.status !== 'ready' && next.status === 'ready') {setDraft(clone(next.draft)); setDirty(false);}
          }).catch(() => {});
        }
      }
    }, 2200);
    return () => {active = false; clearInterval(timer);};
  }, [auth, doc, refreshDocs]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault(); event.returnValue = '';}};
    window.addEventListener('beforeunload', handler); return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);
  async function run(action: () => Promise<void>) {
    setError(''); setBusy(true); try {await action();} catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  async function openDoc(id: string) {
    if (busy) return;
    if (dirty && !window.confirm('Discard unsaved draft changes?')) return;
    await run(async () => {
      const next = await api<Detail>(`/documents/${id}`); setDoc(next); setDraft(clone(next.draft)); setPage(0); setUndo([]); setRedo([]); setHits([]); setDirty(false); setPreview(null);
      setJobs(await api<Job[]>(`/documents/${id}/jobs`));
    });
  }
  function edit(next: Draft) {
    if (!draft) return;
    setUndo(history => [...history.slice(-49), clone(draft)]); setRedo([]); setDraft(next); setDirty(true);
  }
  function addSelections(selections: Selection[]) {
    if (!draft) return;
    const ids = new Set(draft.selections.map(s => s.id));
    edit({...draft, selections: [...draft.selections, ...selections.filter(s => !ids.has(s.id))]});
  }
  function decide(finding: Finding, accept: boolean) {
    if (!draft) return;
    const selections = draft.selections.filter(s => s.id !== finding.id);
    if (accept) selections.push({id: finding.id, page: finding.page, rects: finding.rects, category: finding.category, source: 'automatic'});
    edit({...draft, selections, decisions: {...draft.decisions, [finding.id]: accept ? 'accepted' : 'rejected'}});
  }
  function bulk(accept: boolean) {
    if (!draft || !doc) return;
    const pending = doc.findings.filter(f => !draft.decisions[f.id]);
    edit({...draft, selections: accept ? [...draft.selections, ...pending.map(f => ({id: f.id, page: f.page, rects: f.rects, category: f.category, source: 'automatic' as const}))] : draft.selections,
      decisions: {...draft.decisions, ...Object.fromEntries(pending.map(f => [f.id, accept ? 'accepted' : 'rejected']))}});
  }
  function removeSelection(selection: Selection) {
    if (!draft) return;
    edit({...draft, selections: draft.selections.filter(s => s.id !== selection.id), decisions: selection.source === 'automatic' ? {...draft.decisions, [selection.id]: 'rejected'} : draft.decisions});
  }
  async function save() {
    if (!doc || !draft) throw new Error('No document selected');
    if (!dirty) return draft.version;
    const result = await api<{version: number}>(`/documents/${doc.id}/draft`, {method: 'PUT', body: JSON.stringify(draft)});
    setDraft({...draft, version: result.version}); setDirty(false); setNotice('Draft saved'); return result.version;
  }
  async function upload(files: FileList | File[]) {
    if (uploading) return;
    setUploading(true); setError('');
    try {
      const form = new FormData(); Array.from(files).forEach(file => form.append('files', file));
      const result = await api<{documents: Doc[]; failures: {name: string; error: string}[]}>('/documents', {method: 'POST', body: form});
      await refreshDocs();
      if (result.failures.length) setError(result.failures.map(f => `${f.name}: ${f.error}`).join(' · '));
      if (result.documents.length) {setNotice(`${result.documents.length} document(s) queued`); if (!doc) await openDoc(result.documents[0].id);}
    } catch (e) {setError((e as Error).message);} finally {setUploading(false); if (uploadRef.current) uploadRef.current.value = '';}
  }
  function point(event: PointerEvent<HTMLDivElement>): [number, number] {
    const rect = event.currentTarget.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (event.clientX-rect.left)/rect.width)), Math.max(0, Math.min(1, (event.clientY-rect.top)/rect.height))];
  }
  function pointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    start.current = point(event); event.currentTarget.setPointerCapture(event.pointerId); setDrag([...start.current, ...start.current] as Rect);
  }
  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!start.current) return;
    const [x,y] = point(event), [a,b] = start.current; setDrag([Math.min(x,a), Math.min(y,b), Math.max(x,a), Math.max(y,b)]);
  }
  function pointerUp(event: PointerEvent<HTMLDivElement>) {
    if (!start.current || !doc) return;
    const [x,y] = point(event), [a,b] = start.current;
    const rect: Rect = [Math.min(x,a), Math.min(y,b), Math.max(x,a), Math.max(y,b)];
    const words = doc.pages[page].words.filter(w => {
      const [x0,y0,x1,y1] = w.rect;
      return Math.abs(x-a) < .004 && Math.abs(y-b) < .004 ? x >= x0 && x <= x1 && y >= y0 && y <= y1 : x1 > rect[0] && x0 < rect[2] && y1 > rect[1] && y0 < rect[3];
    });
    const rects = mode === 'text' ? words.map(w => w.rect) : rect[2]-rect[0] > .003 && rect[3]-rect[1] > .003 ? [rect] : [];
    if (rects.length) addSelections([{id: crypto.randomUUID(), page, rects, category: 'MANUAL', source: 'manual'}]);
    start.current = null; setDrag(null);
  }
  async function openTemplates() {await run(async () => {setTemplates(await api<Template[]>('/templates')); setModal('templates');});}
  const pending = doc?.findings.filter(f => !draft?.decisions[f.id]).length || 0;
  const visibleFindings = doc?.findings.filter(f => filter === 'all' || (filter === 'pending' ? !draft?.decisions[f.id] : draft?.decisions[f.id] === filter)) || [];
  const latest = doc?.exports[0];

  if (auth === null) return <div className="loading-screen"><LoaderCircle className="spin"/> Loading your workspace…</div>;
  if (!auth) return <div className="login-shell"><section className="login-story"><div className="brand"><ShieldCheck size={30}/> privixion<span>DOCUMENT SECURITY</span></div><div><span className="eyebrow">CONFIDENTIAL, BY DESIGN</span><h1>Keep the document.<br/>Protect the details.</h1><p>A private workspace to find, review, and permanently redact sensitive information.</p><div className="login-pills"><span><LockKeyhole size={15}/> Local processing</span><span><Eye size={15}/> Review before export</span></div></div><small>Your documents stay on your infrastructure.</small></section><section className="login-form"><div className="login-icon"><ShieldCheck size={30}/></div><span className="eyebrow">YOUR SECURE WORKSPACE</span><h2>Welcome back.</h2><p>Sign in with your administrator password.</p><form onSubmit={event => {event.preventDefault(); run(async () => {const result = await api<{csrf: string}>('/auth/login', {method: 'POST', body: JSON.stringify({password})}); setCsrf(result.csrf); setPassword(''); setAuth(true); sessionExpired.current = false;});}}><label htmlFor="password">Administrator password</label><input id="password" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required/>{error && <div className="error" role="alert">{error}</div>}<button className="primary" disabled={busy}>{busy ? <LoaderCircle className="spin" size={17}/> : <ArrowRight size={17}/>} Enter workspace</button></form><small><LockKeyhole size={13}/> Encrypted storage · Private access</small></section></div>;

  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><ShieldCheck size={26}/> privixion<span>DOCUMENT REDACTION</span></div><div className="workspace-label">WORKSPACE <span>LOCAL</span></div><button className="nav-active"><Layers size={18}/> Documents <span>{docs.length}</span></button><button className="nav-item" onClick={openTemplates}><Settings2 size={18}/> Rule templates</button><button className="nav-item" onClick={() => run(async () => {setEvents(await api<Event[]>('/audit')); setModal('audit');})}><History size={18}/> Audit trail</button><div className="sidebar-bottom"><div className="local-note"><span className="status-dot"/><div>Private processing<small>Documents stay on this server</small></div></div><button className="nav-item" onClick={() => {if (dirty && !window.confirm('Discard unsaved changes and sign out?')) return; run(async () => {await api('/auth/logout', {method:'POST'}); setAuth(false); setDoc(null); setDraft(null); setDirty(false);});}}><LogOut size={17}/> Sign out</button></div></aside>
    <main><header className="topbar"><span>Workspace <ChevronRight size={14}/> <strong>Documents</strong></span><div><span className="private-tag"><LockKeyhole size={13}/> PRIVATE</span><span className="avatar">A</span><span>Administrator</span></div></header>
      <nav className="mobile-nav" aria-label="Workspace actions"><button onClick={openTemplates}><Settings2 size={15}/> Rules</button><button onClick={() => run(async () => {setEvents(await api<Event[]>('/audit')); setModal('audit');})}><History size={15}/> Audit</button><button disabled={!doc} onClick={()=>setModal('delete')}><Trash2 size={15}/> Delete</button><button onClick={() => {if (dirty && !window.confirm('Discard unsaved changes and sign out?')) return; run(async () => {await api('/auth/logout',{method:'POST'});setAuth(false);setDoc(null);setDraft(null);setDirty(false);});}}><LogOut size={15}/> Sign out</button></nav>
      <div className="page-heading"><div><span className="eyebrow">PROTECT WHAT MATTERS</span><h1>Document workspace</h1><p>Find sensitive details. Review your redactions. Share with confidence.</p></div><button className="primary" disabled={uploading} onClick={() => uploadRef.current?.click()}>{uploading ? <LoaderCircle className="spin" size={17}/> : <Upload size={17}/>} Upload documents</button><input hidden ref={uploadRef} type="file" multiple accept=".pdf,.docx,.xlsx,.pptx,.txt,.tiff,.tif,.jpg,.jpeg,.png" onChange={e => e.target.files && upload(e.target.files)}/></div>
      {error && <div className="banner error" role="alert"><AlertCircle size={17}/>{error}<button aria-label="Dismiss error" onClick={() => setError('')}><X size={16}/></button></div>}
      {notice && <div className="banner notice" role="status"><Check size={17}/>{notice}<button aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={16}/></button></div>}
      <div className="workspace-grid"><section className="document-list"><div className="section-heading"><h2>Your documents <span>{docs.length}</span></h2><button title="Download selected finalized documents" aria-label="Download selected finalized documents" disabled={!selectedDocs.length || busy} onClick={() => run(async () => {const details = await Promise.all(selectedDocs.map(id => api<Detail>(`/documents/${id}`))); const ids = details.map(d => d.exports.find(e => e.status === 'ready' && e.confirmed)?.id); if (ids.some(id => !id)) throw new Error('Each selected document needs a confirmed final export.'); await batchDownload(ids as string[]);})}><Download size={17}/></button></div>
        <div className="dropzone" onDragOver={e => e.preventDefault()} onDrop={e => {e.preventDefault(); upload(e.dataTransfer.files);}}><Upload size={21}/><button onClick={() => uploadRef.current?.click()}>Choose files or drop here</button><small>PDF, Office, TXT & images<br/>Up to {limits.max_file_mb} MB each · {limits.max_batch} per batch</small></div>
        <div className="document-rows">{docs.length === 0 && <div className="list-empty">Your documents will appear here.</div>}{docs.map(d => <div className={`document-row ${doc?.id === d.id ? 'selected' : ''}`} key={d.id}><input type="checkbox" aria-label={`Select ${d.name} for batch download`} checked={selectedDocs.includes(d.id)} onChange={e => setSelectedDocs(e.target.checked ? [...selectedDocs,d.id] : selectedDocs.filter(id => id !== d.id))}/><button className="doc-open" onClick={() => openDoc(d.id)}><span className="file-icon"><FileText size={20}/><small>{d.kind.toUpperCase()}</small></span><span><strong>{d.name}</strong><small>{new Date(d.created_at).toLocaleDateString(undefined,{month:'short',day:'numeric'})} <span className={`doc-status ${d.status}`}>{d.status === 'ready' ? 'Ready to review' : label(d.status)}</span></small></span></button></div>)}</div><div className="list-footer"><ShieldCheck size={16}/> Originals are preserved</div></section>
        {!doc ? <section className="empty-workspace"><div className="empty-art"><div className="paper-lines"><span/><span/><span className="redacted-line"/><span/><span className="redacted-line short"/></div><ShieldCheck size={39}/></div><span className="eyebrow">A CLEAN START</span><h2>Your next safe-to-share document<br/>starts here.</h2><p>Upload a document to detect sensitive information,<br/>then review every detail before exporting.</p><button className="primary" onClick={() => uploadRef.current?.click()}><Plus size={17}/> Add your first document</button><div className="steps"><span>01 <strong>Upload</strong></span><span>02 <strong>Review</strong></span><span>03 <strong>Export</strong></span></div></section> : doc.status !== 'ready' ? <section className="processing-view"><div className="large-icon">{doc.status === 'failed' ? <AlertCircle/> : <LoaderCircle className="spin"/>}</div><h2>{doc.status === 'failed' ? 'Document needs attention' : 'Preparing your document'}</h2><p>{doc.name}</p><p>{doc.error || 'Converting, reading pages, and checking for sensitive information.'}</p>{jobs.filter(j => !j.export_id).map(j => <div className="job-card" key={j.id}><span>{label(j.state)} · {j.progress}%</span><progress value={j.progress} max={100}/>{(j.state === 'failed' || j.stalled) && <button className="secondary" disabled={busy} onClick={() => run(async () => {await api(`/jobs/${j.id}/retry`, {method:'POST'}); await openDoc(doc.id);})}>Retry processing</button>}</div>)}<button className="text-button danger" onClick={() => setModal('delete')}><Trash2 size={15}/> Delete document</button></section> : <section className="editor">
          <div className="editor-heading"><div><div className="file-title"><FileText size={18}/><h2 title={doc.name}>{doc.name}</h2></div><small>{doc.pages.length} pages <span>·</span> {dirty ? 'Unsaved changes' : `Draft v${draft?.version}`} <span>·</span> {draft?.selections.length || 0} redactions</small></div><div><button className="icon-button" title="Document audit history" aria-label="Document audit history" onClick={() => run(async () => {setEvents(await api<Event[]>(`/audit?document_id=${doc.id}`)); setModal('audit');})}><History size={17}/></button><button className="icon-button danger" title="Delete document" aria-label="Delete document" onClick={() => setModal('delete')}><Trash2 size={17}/></button><button className="secondary" disabled={busy || !dirty} onClick={() => run(async () => {await save();})}><Save size={15}/> Save draft</button></div></div>
          {['docx','xlsx','pptx'].includes(doc.kind) && <div className="conversion-note"><AlertCircle size={14}/> Review the converted layout. Office fonts, print areas, and pagination may differ.</div>}
          <div className="editor-body" inert={busy}><div className="canvas-column"><div className="toolbar"><div className="tool-group"><button className={mode === 'text' ? 'active' : ''} aria-label="Select text" title="Click a word or drag over text" onClick={() => setMode('text')}><MousePointer2 size={17}/></button><button className={mode === 'rectangle' ? 'active' : ''} aria-label="Draw rectangle" title="Draw redaction rectangle" onClick={() => setMode('rectangle')}><Square size={17}/></button><button title="Black out whole page" aria-label="Black out whole page" onClick={() => addSelections([{id:crypto.randomUUID(),page,rects:[[0,0,1,1]],category:'WHOLE_PAGE',source:'manual'}])}><ScanLine size={17}/></button></div><div className="tool-group"><button disabled={!undo.length} aria-label="Undo" onClick={() => {setRedo([...redo,clone(draft!)]); setDraft({...undo.at(-1)!,version:draft!.version}); setUndo(undo.slice(0,-1)); setDirty(true);}}><Undo2 size={17}/></button><button disabled={!redo.length} aria-label="Redo" onClick={() => {setUndo([...undo,clone(draft!)]); setDraft({...redo.at(-1)!,version:draft!.version}); setRedo(redo.slice(0,-1)); setDirty(true);}}><Redo2 size={17}/></button></div><div className="zoom-controls"><button aria-label="Zoom out" onClick={() => setZoom(Math.max(40,zoom-10))}><ZoomOut size={16}/></button><span>{zoom}%</span><button aria-label="Zoom in" onClick={() => setZoom(Math.min(180,zoom+10))}><ZoomIn size={16}/></button></div></div>
              <div className="page-area"><div className="page-sheet" style={{width:`${zoom}%`,aspectRatio:`${doc.pages[page].width}/${doc.pages[page].height}`}} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={() => {start.current=null; setDrag(null);}}><img draggable={false} src={`/api/documents/${doc.id}/pages/${page}`} alt={`Document page ${page+1}`}/>{doc.findings.filter(f => f.page === page && !draft?.decisions[f.id]).flatMap(f => f.rects.map((r,i) => <span className="finding-box" style={boxStyle(r)} key={`${f.id}-${i}`}/>))}{draft?.selections.filter(s => s.page === page).flatMap(s => s.rects.map((r,i) => <span className="redaction-box" style={boxStyle(r)} key={`${s.id}-${i}`}/>))}{hits.filter(s => s.page === page).flatMap(s => s.rects.map((r,i) => <span className="search-box" style={boxStyle(r)} key={`${s.id}-${i}`}/>))}{drag && <span className="drag-box" style={boxStyle(drag)}/>}</div></div>
              <div className="page-navigation"><button aria-label="Previous page" disabled={page === 0} onClick={() => setPage(page-1)}><ChevronLeft size={17}/></button><span>Page <strong>{page+1}</strong> of {doc.pages.length}</span><button aria-label="Next page" disabled={page === doc.pages.length-1} onClick={() => setPage(page+1)}><ChevronRight size={17}/></button><span className="selection-hint">{mode === 'text' ? 'Click or drag to select text' : 'Drag to mark an area'}</span></div><div className="thumbnails">{doc.pages.map(p => <button key={p.index} className={page === p.index ? 'selected' : ''} aria-label={`Go to page ${p.index+1}`} onClick={() => setPage(p.index)}><img loading="lazy" src={`/api/documents/${doc.id}/pages/${p.index}`} alt=""/><span>{p.index+1}</span></button>)}</div></div>
            <aside className="review-panel"><div className="review-title"><ShieldCheck size={18}/><h3>Redaction review</h3><span>{pending}</span></div><div className="review-tabs"><button className={panel==='findings'?'active':''} onClick={() => setPanel('findings')}>Detections <span>{doc.findings.length}</span></button><button className={panel==='selections'?'active':''} onClick={() => setPanel('selections')}>Selected <span>{draft?.selections.length || 0}</span></button></div>
              <form className="term-search" onSubmit={e => {e.preventDefault(); run(async () => {const found = await api<Selection[]>(`/documents/${doc.id}/search`,{method:'POST',body:JSON.stringify({term:search})}); setHits(found); if(found.length) setPage(found[0].page);});}}><Search size={15}/><input aria-label="Search sensitive terms" placeholder="Search a sensitive term…" value={search} onChange={e => setSearch(e.target.value)} required/><button aria-label="Search" disabled={busy}><ArrowRight size={15}/></button></form>{hits.length > 0 && <div className="search-results"><span>{hits.length} matches</span><button onClick={() => {addSelections(hits); setHits([]);}}>Redact all</button><button aria-label="Clear search" onClick={() => setHits([])}><X size={13}/></button></div>}
              {panel === 'findings' ? <><div className="filter-row"><select aria-label="Filter detections" value={filter} onChange={e => setFilter(e.target.value)}><option value="pending">Pending review ({pending})</option><option value="all">All detections</option><option value="accepted">Accepted</option><option value="rejected">Rejected</option></select><button aria-label="Apply rules" title="Apply a rule template" onClick={openTemplates}><Settings2 size={16}/></button></div>{pending>0 && <div className="bulk-actions"><button onClick={() => bulk(true)}>Accept all pending</button><button onClick={() => bulk(false)}>Reject all pending</button></div>}<div className="findings-list">{visibleFindings.length === 0 && <div className="review-empty"><Check size={25}/><strong>{pending===0?'All detections reviewed':'No matching detections'}</strong><p>Check the pages for anything the detector may have missed.</p></div>}{visibleFindings.map(f => <div className={`finding-card ${draft?.decisions[f.id] || ''}`} key={f.id}><div><span className="category">{label(f.category)}</span><span className="confidence">{Math.round(f.confidence*100)}% confidence</span></div><button className="finding-text" onClick={() => setPage(f.page)}>{f.text}</button><div className="finding-footer"><button onClick={() => setPage(f.page)}>Page {f.page+1}</button><span>{draft?.decisions[f.id] && label(draft.decisions[f.id])}</span><button className="reject" aria-label={`Reject ${f.text}`} onClick={() => decide(f,false)}><X size={15}/></button><button className="accept" aria-label={`Accept ${f.text}`} onClick={() => decide(f,true)}><Check size={15}/></button></div></div>)}</div></> : <div className="findings-list">{draft?.selections.length === 0 && <div className="review-empty"><Square size={25}/><strong>No redactions selected</strong><p>Accept a detection or mark an area on the document.</p></div>}{draft?.selections.map(s => <div className="selection-card" key={s.id}><button onClick={() => setPage(s.page)}><strong>{label(s.category)}</strong><small>Page {s.page+1} · {s.rects.length} region(s) · {s.source}</small></button><button aria-label="Remove redaction" onClick={() => removeSelection(s)}><Trash2 size={15}/></button></div>)}</div>}
              <div className="review-bottom"><p><LockKeyhole size={13}/> Final PDFs contain redacted pixels only.<br/>Text will not be selectable.</p><button className="primary" disabled={busy || pending>0} onClick={() => run(async () => {const version = await save(); const result = await api<Export>(`/documents/${doc.id}/finalize`,{method:'POST',body:JSON.stringify({version})}); setDoc({...doc,exports:[result,...doc.exports.filter(e=>e.id!==result.id)]}); setNotice('Preparing your final preview…');})}><Eye size={16}/> Prepare final preview</button>{pending>0 && <small>Review {pending} remaining detection(s) first</small>}</div></aside></div>
          {latest && <div className="export-bar"><div><span className={`status-dot ${latest.status==='failed'?'failed':''}`}/><strong>Export v{latest.version}</strong><span>{latest.status==='ready'?(latest.confirmed?'Confirmed · Ready to download':'Ready for final review'):label(latest.status)}</span></div><div>{latest.status==='ready' && <><button className="secondary" onClick={() => {setPreview(latest);setPreviewPage(0);}}><Eye size={15}/> Review final PDF</button>{latest.confirmed && <a className="primary" href={`/api/exports/${latest.id}/download`}><Download size={15}/> Download PDF</a>}</>}{jobs.filter(j=>j.export_id===latest.id && (j.state==='failed'||j.stalled)).map(j=><button key={j.id} className="secondary" disabled={busy} onClick={()=>run(async()=>{await api(`/jobs/${j.id}/retry`,{method:'POST'});const next=await api<Detail>(`/documents/${doc.id}`);setDoc(next);})}>Retry export</button>)}</div></div>}
        </section>}
      </div><footer className="workspace-footer"><span><ShieldCheck size={14}/> Built for careful review. Automated detection may miss sensitive information.</span><span>Privixion · Local workspace</span></footer>
    </main>
    {modal && <div className="modal-backdrop" onClick={()=>setModal(null)}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" onClick={e=>e.stopPropagation()}><div className="modal-heading"><h2 id="modal-title">{modal==='templates'?'Rule templates':modal==='audit'?'Audit trail':'Delete document?'}</h2><button aria-label="Close dialog" onClick={()=>setModal(null)}><X size={20}/></button></div>{modal==='delete'?<><p>This deletes the original, previews, drafts, and exports for <strong>{doc?.name}</strong>. Audit records are retained.</p><button className="danger-button" disabled={busy} onClick={()=>run(async()=>{await api(`/documents/${doc!.id}`,{method:'DELETE'});setSelectedDocs(selectedDocs.filter(id=>id!==doc!.id));setDoc(null);setDraft(null);setDirty(false);setModal(null);await refreshDocs();})}><Trash2 size={16}/> Delete permanently</button></>:modal==='audit'?<div className="audit-table">{events.map(e=><details key={e.id}><summary><span>{label(e.action)}</span><small>{new Date(e.timestamp).toLocaleString()} · {e.actor}</small></summary><pre>{JSON.stringify(e.details,null,2)}</pre></details>)}{events.length===0&&<p>No audit events yet.</p>}<small>Showing the latest 1,000 matching events.</small></div>:<><p>Save patterns for repeated use. Categories begin with CUSTOM_. Patterns support literals, character classes, ? and bounded repetition.</p><form className="template-form" onSubmit={e=>{e.preventDefault();run(async()=>{await api('/templates',{method:'POST',body:JSON.stringify({name:templateName,rules:[{category:ruleCategory,pattern:rulePattern}]})});setTemplates(await api<Template[]>('/templates'));setTemplateName('');});}}><label>Template name<input required value={templateName} onChange={e=>setTemplateName(e.target.value)} placeholder="Internal reference numbers" maxLength={100}/></label><label>Category<input required value={ruleCategory} onChange={e=>setRuleCategory(e.target.value.toUpperCase())}/></label><label>Pattern<input required value={rulePattern} onChange={e=>setRulePattern(e.target.value)} maxLength={200}/></label><button className="primary" disabled={busy}><Plus size={15}/> Save template</button></form><div className="template-list">{templates.map(t=><div key={t.id}><div><strong>{t.name}</strong><code>{t.rules.map(r=>r.pattern).join(' · ')}</code></div>{doc?.status==='ready'&&<button className="secondary" disabled={busy} onClick={()=>run(async()=>{const version=await save();const result=await api<{count:number}>(`/documents/${doc.id}/templates/apply`,{method:'POST',body:JSON.stringify({template_id:t.id,version})});const next=await api<Detail>(`/documents/${doc.id}`);setDoc(next);setDraft(clone(next.draft));setUndo([]);setRedo([]);setDirty(false);setNotice(`${result.count} new detections added`);setModal(null);})}>Apply</button>}<button aria-label={`Delete ${t.name}`} disabled={busy} onClick={()=>run(async()=>{await api(`/templates/${t.id}`,{method:'DELETE'});setTemplates(await api<Template[]>('/templates'));})}><Trash2 size={16}/></button></div>)}</div></>}{error&&<div className="error" role="alert">{error}</div>}</section></div>}
    {preview && doc && <div className="modal-backdrop"><section className="modal preview-modal" role="dialog" aria-modal="true" aria-label="Final PDF preview"><div className="modal-heading"><div><h2>Review final PDF</h2><p>Export v{preview.version} · This preview shows the actual export.</p></div><button aria-label="Close final preview" onClick={()=>setPreview(null)}><X size={20}/></button></div><div className="final-page"><img src={`/api/exports/${preview.id}/pages/${previewPage}`} alt={`Final PDF page ${previewPage+1}`}/></div><div className="page-navigation"><button aria-label="Previous final page" disabled={previewPage===0} onClick={()=>setPreviewPage(previewPage-1)}><ChevronLeft size={18}/></button><span>Page {previewPage+1} of {doc.pages.length}</span><button aria-label="Next final page" disabled={previewPage===doc.pages.length-1} onClick={()=>setPreviewPage(previewPage+1)}><ChevronRight size={18}/></button></div><div className="preview-footer"><div><a href={`/api/exports/${preview.id}/report/pdf`}>PDF report</a><a href={`/api/exports/${preview.id}/report/json`}>JSON report</a></div>{preview.confirmed?<a className="primary" href={`/api/exports/${preview.id}/download`}><Download size={16}/> Download PDF</a>:<button className="primary" disabled={busy} onClick={()=>run(async()=>{const next=await api<Export>(`/exports/${preview.id}/confirm`,{method:'POST'});setPreview(next);setDoc({...doc,exports:doc.exports.map(e=>e.id===next.id?next:e)});})}><Check size={16}/> Confirm reviewed export</button>}</div>{error&&<div className="error" role="alert">{error}</div>}</section></div>}
  </div>;
}
