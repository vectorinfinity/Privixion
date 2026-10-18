# API contract

All routes use `/api`. Login and health are unauthenticated. Other routes require the `privixion_session` cookie. POST, PUT, and DELETE require `X-CSRF-Token` returned by login or `GET /auth/me`. Same-origin clients are supported; CORS is deliberately not enabled.

| Route | Behavior |
|---|---|
| `POST /auth/login` | `{password}` → `{csrf,user}` plus session cookie |
| `GET /auth/me`, `POST /auth/logout` | Session identity and revocation |
| `GET /config` | Active upload, page, and batch limits |
| `GET /documents` | Document names, formats, states, creation times |
| `POST /documents` | Multipart `files` → accepted documents and per-file failures |
| `GET /documents/{id}` | Page words/geometry, findings, current draft, exports |
| `GET /documents/{id}/jobs` | Progress, errors, timings, and stalled status |
| `POST /jobs/{id}/retry` | Retry failed or stalled processing/export |
| `GET /documents/{id}/pages/{page}` | Authenticated converted-page PNG, zero-based page |
| `PUT /documents/{id}/draft` | `{version,selections,decisions}` → new version; 409 on conflict |
| `POST /documents/{id}/search` | `{term}` → literal case-insensitive matches and geometry |
| `POST /documents/{id}/finalize` | `{version}` → immutable export/job; review decisions required |
| `GET /exports/{id}/pages/{page}` | Actual final PDF rendered as PNG |
| `POST /exports/{id}/confirm` | Record user confirmation of this artifact |
| `GET /exports/{id}/download` | Confirmed image-only PDF download |
| `GET /exports/{id}/report/{pdf\|json}` | Redaction summary without sensitive values |
| `POST /batch/download` | `{export_ids}` → ZIP; all exports must be confirmed |
| `GET /audit?document_id=…` | Latest 1,000 matching events, retained after deletion |
| `DELETE /documents/{id}` | Delete originals and derivatives; 409 while jobs are active |
| `GET/POST /templates` | List/create reusable rules |
| `PUT /templates/{id}` | Update a template; prior document findings and exports are unchanged |
| `DELETE /templates/{id}` | Delete a template; existing suggestions remain |
| `POST /documents/{id}/templates/apply` | `{template_id,version}` → append suggestions and increment draft version |

A selection is `{id,page,rects,category,source}`. `rects` is an array of `[left,top,right,bottom]` values in `[0,1]` relative to the displayed page. Rectangles must be nonempty. `source` is `manual`, `automatic`, or `search`. Categories contain uppercase letters, digits, and underscores. No sensitive strings belong in a selection or audit entry.

Findings add `text` and `confidence`. Decisions map finding IDs to `accepted` or `rejected`. Accepted detections must have a matching selection at finalization. Missing decisions block finalization; detecting no findings does not establish that a document is safe. An export remains immutable after finalization even when the current draft changes.

A template is `{name,rules:[{category,pattern}]}`. Custom categories start with `CUSTOM_`. Patterns are limited to 200 characters; literal text, character classes, `?`, and bounded repetition up to 100 are supported. Groups, lookarounds, backreferences, `*`, `+`, alternation, and unbounded repetition are rejected. Time-budget failures return 422.

Document states: `queued`, `processing`, `ready`, `failed`. Jobs additionally expose `converting`, `analyzing`, and `exporting`. Exports: `queued`, `processing`, `ready`, `failed`. A queued job whose dispatch failed is marked failed and can be retried after queue recovery. Stalled detection uses the configured processing time limit plus 60 seconds.
