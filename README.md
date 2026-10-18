# Privixion

A self-hosted document redaction workspace. Upload documents, review local detection suggestions, add manual redactions, and export a fresh image-only PDF. Originals are retained separately. English is the initial supported language.

## Start on Windows

Requirements: Docker Desktop with a working Linux container engine, Docker Compose v2, at least 8 GB of available RAM, and Python 3.11+ for setup. The first image build downloads dependencies, LibreOffice, Tesseract, and the English NLP model. Runtime workers have no outbound internet access.

```powershell
# From your cloned repository's root directory:
python -m pip install argon2-cffi cryptography
python scripts/bootstrap.py
docker compose up --build -d
```

Open **http://localhost:8080** and sign in using the password you chose. `bootstrap.py --generate-password` instead creates a random password in `secrets/initial-password.txt`. Setup never replaces an existing encryption key. Keep `secrets/` private and outside source control. On Windows, protect that directory with NTFS permissions; POSIX file modes alone do not restrict Windows access.

If `python` is unavailable, use an installed interpreter explicitly. A virtual environment at `.venv\Scripts\python.exe` can also run the bootstrap script after installing its setup dependencies.

```powershell
docker compose ps
docker compose logs --tail 80 api worker
docker compose stop
```

Only the web gateway is bound to the host, at `127.0.0.1:8080`. PostgreSQL, Redis, and the processing API are internal services. Docker Desktop must be running; a Docker CLI installation alone is insufficient. Do not use `docker compose down -v` unless you intend to destroy all document and database volumes.

## Workflow

1. Upload up to 20 files: PDF, DOCX, XLSX, PPTX, UTF-8 TXT, TIFF, JPEG, or PNG. Defaults: 50 MB per input and 500 pages per document.
2. Wait for conversion, native extraction, OCR, and detection. Scanned and mixed-content pages are OCRed locally. Failed or stalled jobs can be retried without changing the original.
3. Review each detection. Accept or reject it, or review all pending suggestions together. Confidence is a detector score, not a guarantee that the content is sensitive.
4. Click or drag across text, draw rectangles, or black out a whole page. Search literal terms and redact all returned matches. Use undo/redo and save the draft.
5. Optionally apply a reusable custom regex template. The UI creates one pattern per template; the API supports up to 20. Applying rules adds suggestions and preserves prior draft selections.
6. Prepare the final PDF. This freezes the saved draft version and generates new pages from pixels at 300 DPI, with selected regions permanently painted black.
7. Inspect the **actual final PDF preview**, confirm it, then download. Download PDF/JSON reports from the final-preview dialog. Select confirmed documents in the list to download a ZIP.

Office conversion can change fonts, spreadsheet print areas, slide rendering, or pagination. The export represents the **converted visible pages**, not the entire native workbook/presentation data model. Review conversion before redacting. Password-protected PDFs are rejected. Native Office outputs and selectable PDF text are not included in this version.

Page coordinates are relative to the displayed page, including PDF rotation and cropping. Detection suggestions and text words are encrypted at rest; only authenticated responses expose them for review. Audit entries and reports contain categories, counts, IDs, and geometry, never matched sensitive text or search terms.

## Verification

```powershell
# Full engine tests inside the built image; isolated temporary test database and keys
docker compose run --rm --no-deps -T -v "${PWD}/scripts:/scripts:ro" worker python -m pytest -q -p no:cacheprovider

# Build and type-check the UI
cd frontend
npm ci
npm run build
```

Local backend tests: `cd backend` then `..\.venv\Scripts\python.exe -m pytest -q`. Install `backend/requirements.txt` into a normal Python environment if the virtual environment is absent. Tests needing LibreOffice, Tesseract, or the English model skip when those engines are missing; skipped tests do not establish those capabilities.

Browser tests use a **synthetic test server**, real API/storage/export code, and a temporary SQLite database. The test harness replaces Celery dispatch with a local thread and disables OCR/NLP; it is never the production server. Run from `frontend`:

```powershell
npx playwright install chromium
npm run test:e2e
```

With the Docker application running, `npm run test:deployment` checks the actual Nginx-served UI, real document processing, final PDF downloads, mobile layout, and cleanup. It reads the generated initial password file, or a privately supplied `TEST_ADMIN_PASSWORD` environment variable. It does not record authentication traces or place the password in the page DOM.

From the project root, run the full live API and restore drill:

```powershell
python scripts/verify_deployment.py
```

This exercises all eight input formats, validates originals and exports, creates an encrypted backup, restores it into a uniquely named Compose project on port 8081, compares checksums and audit history, and removes that temporary project and its volumes. It briefly stops the main application for a consistent backup and leaves it running afterward. Only synthetic documents uploaded by the drill are deleted. Backups remain under ignored `data/deployment-drill-*` directories, with corresponding key copies under ignored `secrets/deployment-drill-*` directories.

Copy `examples/sample.txt` into the app to exercise India-specific rules. OOXML/PDF/image fixtures are generated by the tests. No fixture represents verified real identity data.

## Detection evaluation and performance

Use `scripts/evaluate.py` with an exact-span labeled JSON dataset. The included dataset is a tiny synthetic rule smoke test, not evidence of 99% detection quality. To run with the complete image:

```powershell
docker compose run --rm --no-deps -T -v "${PWD}/scripts:/tools:ro" -v "${PWD}/examples:/examples:ro" worker python /tools/evaluate.py --dataset /examples/detection-labels.json --no-nlp
docker compose run --rm --no-deps -T -v "${PWD}/scripts:/tools:ro" worker python /tools/evaluate.py --benchmark
```

The benchmark uses 100 synthetic text pages and records conversion, native rendering/extraction, OCR, detection, and export time. Run additional representative scans and Office documents before making capacity claims. Track precision and recall separately per category. The 30-second and 99% goals remain uncommitted targets.

## Backup and recovery

Backups stop the application and worker to obtain a consistent database/artifact snapshot, then restart them. Both the PostgreSQL dump and encrypted artifact archive are wrapped in authenticated AES-256-GCM encryption. Keys and deployment credentials are copied to a separate location. Use protected, separate storage destinations:

```powershell
python scripts/backup.py backup D:\Backups\privixion-2026-10-11 --keys-dir E:\PrivateKeys\privixion-2026-10-11
```

The helper verifies authentication after backup. Test restore in a **fresh checkout with empty volumes** using the same built backend image:

```powershell
python scripts/backup.py restore D:\Backups\privixion-2026-10-11 --keys-dir E:\PrivateKeys\privixion-2026-10-11
```

Restore refuses to replace different secrets or populate a nonempty database/artifact volume. It validates every encrypted stream before writing data, restores records and artifacts, invalidates sessions, and marks interrupted jobs retryable. Sign in, download a known confirmed export, compare its checksum, inspect the audit trail, and verify that an original is preserved. Losing the artifact key makes existing documents and backups unreadable. Encryption does not protect keys stored alongside a backup by an operator.

## Deployment limits

This is a single-administrator MVP. Separate reviewers, approval history, multi-user RBAC, native-format exports, regulatory certification, high availability, and measured high-volume capacity are deferred. Audit records are append-only through the application API; they are not protected against database administrators altering the database.

For remote deployment, put the gateway behind a trusted HTTPS reverse proxy, set `COOKIE_SECURE=true` and `PUBLIC_ORIGIN` to the exact public origin, forward the public Host and protocol correctly, and configure host/firewall access deliberately. Keep internal service ports unpublished. Local loopback HTTP is the development default; network transmission encryption requires HTTPS. The backend trusts forwarded protocol headers only because its port is confined to the private Compose network. When using the Vite development proxy, set `PUBLIC_ORIGIN=http://127.0.0.1:5173` on the API process.

Docker volumes hold encrypted artifacts and database state. Redis persists job IDs, not document content. Runtime files live in container `/tmp` tmpfs and are discarded after jobs. Host/database encryption, secure key custody, restore drills, monitoring, and external uptime controls remain deployment responsibilities.

## Publishing and private files

Follow the [GitHub publishing guide](docs/github.md) before committing. The
repository excludes `.env`, keys/passwords, runtime data/backups, dependencies,
builds, caches, browser traces, and local verification/benchmark reports. These
files remain on your computer. `.env.example` is a blank reference, not a live
configuration. Each new installation creates its own credentials with the setup
script.

Run `python scripts/check_publication.py` to inspect publication candidates. A
local pre-commit hook and GitHub Actions also check for private paths and secrets;
see [security guidance](SECURITY.md). No live application secrets are required by CI.

See [architecture](docs/architecture.md), [API notes](docs/api.md), and
[requirements coverage](docs/requirements.md).
