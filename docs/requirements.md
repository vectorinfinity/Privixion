# Requirements coverage

Implemented describes code coverage, not a claim that every external engine has been verified on every deployment. The verification report records what actually ran.

| ID | Status | Coverage / remaining work |
|---|---|---|
| FR-1 | Implemented | All listed inputs; Office conversion requires LibreOffice |
| FR-2 | Partial | Local English entities and India-focused identifiers; heuristic addresses/names/accounts need review |
| FR-3 | Implemented | Text selections, rectangles for text/images, whole-page blackout |
| FR-4 | Implemented | Built-in patterns and custom rule templates with review |
| FR-5 | Implemented | Fresh raster-only export; original object content is excluded |
| FR-6 | Implemented | Draft overlays and actual finalized PDF preview |
| FR-7 | Implemented | Immutable new exports; encrypted originals retained |
| FR-8 | Implemented | Actor/time/categories/geometry/version audit; sensitive values omitted |
| FR-9 | Deferred | Separate authorized reviewer and approval history |
| FR-10 | Implemented | Literal term search and all-occurrence redaction |
| FR-11 | Implemented | PDF and JSON reports with counts and checksum |
| FR-12 | Implemented | Multi-upload jobs, individual review, confirmed ZIP export |
| FR-13 | Implemented | Local OCR of scanned/mixed pages; engine-dependent test |
| FR-14 | Implemented | Create/apply/delete templates; bounded regex; API supports multiple rules |
| FR-15 | Implemented | Image-only PDF export |
| NFR-1 | Partial | Benchmark tool; 100 pages / 30 seconds is not guaranteed |
| NFR-2 | Deferred | No 99.9% availability SLA in local Compose deployment |
| NFR-3 | Partial | AES-GCM artifacts/names/patterns; HTTPS required for remote transit, host/database volume encryption configured operationally |
| NFR-4 | Deferred | One administrator account; multi-role RBAC deferred |
| NFR-5 | Deferred | Technical controls do not constitute GDPR/HIPAA/CCPA certification |
| NFR-6 | Partial | Focused editor and responsive UI; usability study pending |
| NFR-7 | Deferred | Single user; concurrent tabs have version-conflict protection |
| NFR-8 | Partial | Persistent application audit; tamper-evident/WORM storage deferred |
| NFR-9 | Partial | Original preservation, durable queue, atomic artifacts, retry and isolated restore drill verified; broader host-failure injection remains |
| NFR-10 | Deferred | Bounded single-worker MVP; capacity measurement and scaling later |
| NFR-11 | Partial | Containerized backend and browser UI; OS/browser certification matrix pending |
| NFR-12 | Partial | Evaluation tooling with per-category precision/recall; 99% not established |
| NFR-13 | Implemented | Isolated recognizers, versioned drafts, reusable custom rules |
| NFR-14 | Implemented | Timestamped login, upload, search, save, finalize, confirm, download, deletion, template and failure events |
| NFR-15 | Partial | Encrypted backups, stream tests and full Docker restore drill verified; operational scheduling and offsite recovery procedures remain |

Exports contain no selectable text. Confirmation is by the same administrator, not independent approval. Detection quality depends on language, layout, OCR quality, and rule coverage. No external AI service receives documents.
