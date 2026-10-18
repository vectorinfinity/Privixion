let csrf = '';
export function setCsrf(value: string) { csrf = value; }
export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData) && init.body) headers.set('Content-Type', 'application/json');
  if (init.method && !['GET','HEAD'].includes(init.method)) headers.set('X-CSRF-Token', csrf);
  const response = await fetch('/api' + path, {...init, headers, credentials: 'same-origin'});
  if (!response.ok) {
    const data = await response.json().catch(() => ({detail: 'Request failed'}));
    throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.map((e: {msg: string}) => e.msg).join('; ') || 'Request failed');
  }
  return response.json();
}
export async function batchDownload(export_ids: string[]) {
  const response = await fetch('/api/batch/download', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify({export_ids})});
  if (!response.ok) throw new Error((await response.json()).detail);
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement('a'); a.href = url; a.download = 'redacted-batch.zip'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
