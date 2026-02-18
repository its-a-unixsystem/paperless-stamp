# Product Requirements Document (PRD): Paperless-Stamp Extension

## 1. Executive Summary

**Problem Statement**: Users managing documents in Paperless-ngx lack a visual way to mark document status (e.g., "Paid", "Received") that mimics a traditional physical stamping workflow.

**Proposed Solution**: A sidecar extension for Paperless-ngx consisting of two components: a **polling worker** that monitors tags to automatically stamp PDF documents, and a **web configuration interface** for managing settings, previewing stamps, and viewing stamp history.

**Success Criteria**:
*   **Accuracy**: 100% of documents tagged with a `stamp:*` trigger tag receive a corresponding stamp.
*   **Aesthetics**: Stamps must look realistic (transparent ink, slight deterministic tilt, bold double border with subtle ink texture).
*   **Integrity**: The original un-stamped file is never modified. Stamped PDFs are written exclusively to the Paperless-ngx archive file. The original remains accessible via the "Original" download button.
*   **Performance**: Stamping processing time < 5 seconds per document.

## 2. User Experience & Functionality

**User Personas**:
*   **Thomas (Digital Archiver)**: Wants a clean, automated way to see which invoices are paid just by looking at the document preview in the Paperless-ngx UI.

### 2.1 User Stories

*   **Automated Stamping**: As a digital archiver, I want to tag a document as `stamp:paid` so that a "PAID" stamp automatically appears on the first page of the PDF preview within 60 seconds, giving me instant visual confirmation of payment status.
*   **Date Integration**: As a digital archiver, I want to fill the `Paid Date` custom field so that the stamp dynamically includes that specific date (e.g., "PAID / 2024-03-15"), creating a permanent record of when payment occurred.
*   **Received Date Default**: As a digital archiver, I want documents tagged `stamp:received` to automatically include the document's `Created` date on the stamp so that I don't have to manually enter receipt dates for every document, while still being able to override via a `Received Date` custom field.
*   **Multi-Stamp**: As a digital archiver, I want to tag a document with both `stamp:paid` and `stamp:received` so that both stamps appear stacked on the first page without overlapping, reflecting the full lifecycle of a document.
*   **Preview via Web UI**: As a digital archiver, I want to preview what a stamp would look like on a specific document via the web interface so that I can verify placement and appearance before committing to a permanent stamp.
*   **Configuration via Web UI**: As a digital archiver, I want to change stamp colors, text mappings, and polling interval through the web interface so that I can customize the system without restarting the container or editing environment variables.
*   **Stamp History**: As a digital archiver, I want to view a log of recently stamped documents in the web interface so that I can verify successful processing and quickly identify failures.

### 2.2 Acceptance Criteria

*   **Positioning**: Stamps are placed in the top-right corner of page 1, offset 10% from the top and right edges, regardless of PDF page size (A4, Letter, etc.).
*   **Multi-Stamp Stacking**: When multiple stamps are applied, each subsequent stamp is offset downward to avoid overlap.
*   **Visual Design**:
    *   Default color: Ink Blue (#003399) at 50% opacity.
    *   Colors are configurable per stamp type via the web interface or environment variables.
    *   Deterministic tilt of ±3 degrees, seeded by the document ID (same document always produces the same tilt).
    *   Classic rubber stamp style: double-line rectangular border with subtle ink texture (slightly fuzzy edges), bold Courier font, uppercase text, date on second line.
    *   Stamp width: ~20% of page width (~120pt on A4).
*   **Workflow Loop Prevention**: After stamping, the trigger tag (e.g., `stamp:paid`) is removed and a per-type done tag (e.g., `stamped:paid`) is added. The poll query only returns documents with `stamp:*` tags, so processed documents are never re-fetched.
*   **Error Handling**: If stamping fails (encrypted PDF, API error, corrupt file), the trigger tag is removed, a `stamp:error` tag is added, and a note is attached to the document describing the failure.

### 2.3 Non-Goals (MVP)

*   Modifying the Paperless-ngx core frontend code (Angular).
*   Supporting non-PDF file formats (images, docx).
*   OCR of the applied stamp.
*   Multi-page stamping (stamp appears on page 1 only).

## 3. System Requirements & Logic

### 3.1 Dependencies

*   **Python 3.11+**
*   **Paperless-ngx REST API**: Document discovery, download, upload, tag management, custom field reads.
*   **ReportLab**: Generating the transparent PDF stamp overlay (text, border, tilt).
*   **pikepdf**: Merging the stamp overlay onto the document's first page. Chosen over PyPDF for its robustness with edge-case PDFs.
*   **FastAPI**: Web configuration interface backend.
*   **HTMX**: Lightweight frontend interactivity without a JS build step.
*   **SQLite**: Persistent storage for configuration and stamp history.

### 3.2 Evaluation Strategy

*   **Visual Regression**: Compare generated stamp overlays against "Gold Standard" reference PDFs.
*   **API Integrity**: Verify that document metadata (title, correspondent, custom fields) remains unchanged after stamping, except for the expected tag swap.
*   **Idempotency**: Re-running the worker on an already-stamped document (with `stamped:*` tag) must produce no changes.

## 4. Technical Specifications

### 4.1 Architecture Overview

The system has two runtime components running in the same Python process:

**A. Stamp Worker (polling loop)**
1.  **Poll**: Query `/api/documents/?tags__name__istartswith=stamp:` at a configurable interval (default: 60s, set via `STAMP_POLL_INTERVAL`).
2.  **Fetch**: Download the document's archive file (or original if no archive exists) and retrieve custom field values (e.g., `Paid Date`, `Received Date`).
3.  **Generate**: For each `stamp:*` tag on the document, ReportLab creates a single-page transparent PDF overlay containing the stamp at the calculated coordinates. Multiple stamps are offset vertically to avoid overlap.
4.  **Merge**: pikepdf merges all stamp overlays onto page 1 of the document.
5.  **Push**: Upload the stamped PDF as the document's **archive file** via the Paperless-ngx API. The original file is never touched.
6.  **Tag Swap**: Remove each `stamp:*` trigger tag, add corresponding `stamped:*` done tags (e.g., `stamp:paid` → `stamped:paid`).
7.  **On Error**: Remove trigger tag, add `stamp:error` tag, attach a note with the error details.

**B. Web Interface (FastAPI + HTMX)**
*   **Configuration page**: View and edit stamp settings (colors per type, text mappings, poll interval, date field mappings). Changes are written to SQLite and picked up by the worker on the next poll cycle.
*   **Preview page**: Select a document from Paperless-ngx, generate a stamp preview, and display the result without modifying the actual document.
*   **History page**: View a log of stamp operations (document ID, stamp type, timestamp, success/failure, error message if applicable).

### 4.2 Configuration

Configuration is loaded in this priority order (highest wins):
1.  **Web UI** (stored in SQLite, modifiable at runtime)
2.  **Environment variables** (set at container start)
3.  **Defaults** (hardcoded)

| Variable | Default | Description |
|---|---|---|
| `PAPERLESS_URL` | *(required)* | Base URL of the Paperless-ngx instance |
| `PAPERLESS_TOKEN` | *(required)* | API authentication token |
| `STAMP_POLL_INTERVAL` | `60` | Seconds between poll cycles |
| `STAMP_DEFAULT_COLOR` | `#003399` | Default stamp ink color (hex) |
| `STAMP_COLOR_PAID` | *(uses default)* | Override color for `stamp:paid` |
| `STAMP_COLOR_RECEIVED` | *(uses default)* | Override color for `stamp:received` |
| `STAMP_TEXT_PAID` | `PAID` | Display text for `stamp:paid` |
| `STAMP_TEXT_RECEIVED` | `RECEIVED` | Display text for `stamp:received` |
| `STAMP_DATE_FIELD_PAID` | `Paid Date` | Paperless custom field name for paid date |
| `STAMP_DATE_FIELD_RECEIVED` | `Received Date` | Paperless custom field name for received date |
| `STAMP_RECEIVED_DATE_FALLBACK` | `created` | Fallback date source for received stamp (`created` = document created date, `none` = omit) |
| `STAMP_UI_PORT` | `8585` | Port for the web configuration interface |

### 4.3 Stamp Visual Specification

```
    ╔═══════════════════╗
    ║       PAID        ║      ← Bold Courier, uppercase, configurable text
    ║    2024-03-15     ║      ← Date from custom field (optional, omitted if empty)
    ╚═══════════════════╝
         ↑ tilted 1-3°

- Border: Double-line rectangle with subtle ink texture (fuzzy edges via multi-stroke rendering)
- Font: Courier Bold (built into ReportLab, zero dependencies)
- Size: ~20% of page width
- Position: Top-right, 10% offset from top and right edges
- Opacity: 50%
- Tilt: ±3°, deterministic (seeded by document ID via hash)
- Color: Configurable per stamp type, default Ink Blue #003399
```

### 4.4 Database Schema (SQLite)

The SQLite database (`/app/data/stamp.db`) stores runtime configuration and stamp history.

```sql
-- Runtime configuration overrides (takes priority over env vars)
CREATE TABLE config (
    key         TEXT PRIMARY KEY,   -- e.g. "poll_interval", "color.paid", "text.paid"
    value       TEXT NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Log of all stamp operations
CREATE TABLE stamp_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL,       -- Paperless-ngx document ID
    document_title  TEXT,                    -- Cached for display in history UI
    stamp_type      TEXT NOT NULL,           -- e.g. "paid", "received"
    stamp_text      TEXT NOT NULL,           -- Rendered text, e.g. "PAID"
    stamp_date      TEXT,                    -- Date shown on stamp (ISO 8601), NULL if omitted
    status          TEXT NOT NULL,           -- "success" | "error"
    error_message   TEXT,                    -- NULL on success, description on failure
    processing_ms   INTEGER,                -- Processing time in milliseconds
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_history_created ON stamp_history(created_at DESC);
CREATE INDEX idx_history_status ON stamp_history(status);
```

### 4.5 Web UI Pages

**A. Configuration Page (`/`)**
```
┌─────────────────────────────────────────────────────────┐
│  Paperless-Stamp  ·  Config  |  Preview  |  History     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  General Settings                                       │
│  ┌─────────────────────────────────────────────┐        │
│  │ Poll Interval (s):  [ 60            ]       │        │
│  │ Default Color:      [ #003399       ] [■]   │        │
│  └─────────────────────────────────────────────┘        │
│                                                         │
│  Stamp Types                                            │
│  ┌──────────┬──────────┬──────────┬────────────────┐    │
│  │ Type     │ Text     │ Color    │ Date Field     │    │
│  ├──────────┼──────────┼──────────┼────────────────┤    │
│  │ paid     │ [PAID  ] │ [#003399]│ [Paid Date   ] │    │
│  │ received │ [RECEI.] │ [#003399]│ [Received Da.] │    │
│  └──────────┴──────────┴──────────┴────────────────┘    │
│                                                         │
│  Received Date Fallback: (●) Document created  ( ) None │
│                                                         │
│                               [ Save Configuration ]    │
│                                                         │
│  Status: ● Worker running · Last poll: 12s ago          │
└─────────────────────────────────────────────────────────┘
```

**B. Preview Page (`/preview`)**
```
┌─────────────────────────────────────────────────────────┐
│  Paperless-Stamp  ·  Config  |  Preview  |  History     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Document:  [ Search by title or ID...         ] [Go]   │
│  Stamp Type: [▼ paid    ]    Date: [ 2024-03-15 ]       │
│                                                         │
│  ┌─────────────────────────────────────────────┐        │
│  │                                             │        │
│  │                          ╔═══════════╗      │        │
│  │  (PDF first page         ║   PAID    ║      │        │
│  │   rendered as preview    ║ 2024-03-15║      │        │
│  │   with stamp overlay)    ╚═══════════╝      │        │
│  │                                             │        │
│  │                                             │        │
│  └─────────────────────────────────────────────┘        │
│                                                         │
│  [ Apply Stamp to Document ]    [ Download Preview ]    │
└─────────────────────────────────────────────────────────┘
```

**C. History Page (`/history`)**
```
┌─────────────────────────────────────────────────────────┐
│  Paperless-Stamp  ·  Config  |  Preview  |  History     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Filter: [▼ All ] [▼ All types]   Last 50 operations    │
│                                                         │
│  ┌────────────┬────────────┬──────┬────────┬──────────┐ │
│  │ Time       │ Document   │ Type │ Status │ Duration │ │
│  ├────────────┼────────────┼──────┼────────┼──────────┤ │
│  │ 2m ago     │ Invoice #4 │ paid │  ● OK  │   820ms  │ │
│  │ 5m ago     │ Receipt #7 │ recv │  ● OK  │   640ms  │ │
│  │ 12m ago    │ Contract   │ paid │  ✗ ERR │  1200ms  │ │
│  │            │            │      │ Encrypted PDF      │ │
│  │ 1h ago     │ Invoice #3 │ paid │  ● OK  │   710ms  │ │
│  └────────────┴────────────┴──────┴────────┴──────────┘ │
│                                                         │
│                                    [ ← Prev ] [ Next →] │
└─────────────────────────────────────────────────────────┘
```

### 4.6 Integration Points

*   **Paperless-ngx REST API**:
    *   `GET /api/documents/?tags__name__istartswith=stamp:` — discover documents to stamp.
    *   `GET /api/documents/{id}/download/` — download archive/original file.
    *   `POST /api/documents/{id}/update_version/` — upload stamped PDF as a new file version (preserves document ID, keeps original as previous version, regenerates thumbnails). **Depends on [PR #12061](https://github.com/paperless-ngx/paperless-ngx/pull/12061), not yet merged.**
    *   `PATCH /api/documents/{id}/` — update tags (remove trigger, add done/error).
    *   `POST /api/documents/{id}/notes/` — attach error notes.
    *   `GET /api/custom_fields/` — resolve custom field names to IDs.
*   **Auth**: API Token via `PAPERLESS_TOKEN` environment variable (header: `Authorization: Token <token>`).

### 4.7 Security & Privacy

*   **Local Processing**: All PDF manipulation happens within the extension's container; no data is sent to external services.
*   **Web UI**: No authentication by default. The UI is expected to be accessed only on the internal Docker network or behind a reverse proxy with authentication.
*   **No Secrets in SQLite**: The Paperless API token is only read from the environment variable, never stored in the database.

## 5. Deployment

### 5.1 Docker Compose (Primary)

A separate container in the same `docker-compose.yml` as Paperless-ngx:

```yaml
paperless-stamp:
  image: paperless-stamp:latest
  environment:
    - PAPERLESS_URL=http://paperless-webserver:8000
    - PAPERLESS_TOKEN=${PAPERLESS_TOKEN}
  volumes:
    - stamp-data:/app/data  # SQLite database
  ports:
    - "8585:8585"  # Web configuration interface
  depends_on:
    - paperless-webserver
  # No shared media volume needed — stamped PDFs are uploaded via API
  # using POST /api/documents/{id}/update_version/ (file versioning)
```

### 5.2 Standalone

```bash
pip install paperless-stamp
PAPERLESS_URL=http://localhost:8000 PAPERLESS_TOKEN=abc123 paperless-stamp
```

## 6. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Encrypted/signed PDFs** | Stamping fails, pikepdf raises error | Catch exception, apply `stamp:error` tag with descriptive note |
| **Race condition**: user edits document while worker is processing | Stamped archive could overwrite concurrent changes | Immediately remove trigger tag at start of processing (before download). If upload fails, re-add trigger tag for retry on next cycle |
| **Paperless API file versioning not yet released** | Core push step blocked | API versioning via `POST /api/documents/{id}/update_version/` (PR #12061) is the planned approach. Stub method raises `NotImplementedError` until PR merges. No filesystem fallback needed. |
| **Large PDFs** | Processing time > 5s target | Stamp only page 1 (already scoped). Log warning if processing exceeds threshold |

## 7. Roadmap

*   **MVP (v1.0)**: `stamp:paid` and `stamp:received` with configurable text/colors. First-page stamping. Web UI for config, preview, and history. Docker + standalone deployment.
*   **v1.1**: User-defined stamp types via `stamp:custom` or arbitrary `stamp:*` tags. Multi-page stamping option. Additional stamp styles (round, minimal).
*   **v1.2**: Stamp templates with custom positioning. Batch stamping operations via the web UI.

## 8. Paperless-ngx Custom Field Setup

The following custom fields must be created in Paperless-ngx for date integration:

| Field Name | Type | Used By | Notes |
|---|---|---|---|
| `Paid Date` | Date | `stamp:paid` | No default; date omitted from stamp if empty |
| `Received Date` | Date | `stamp:received` | Falls back to document `created` date if empty |

## 9. Open Questions

| # | Question | Status | Resolution |
|---|---|---|---|
| 1 | **Does the Paperless-ngx REST API support uploading/replacing the archive file?** | **Resolved (M1 spike)** | No current endpoint exists in v2.20.6. However, [PR #12061](https://github.com/paperless-ngx/paperless-ngx/pull/12061) adds `POST /api/documents/{id}/update_version/` — file versioning that preserves document ID, keeps the original as a previous version, regenerates thumbnails, and requires no filesystem access. This is the planned approach. The `upload_version` method is stubbed with `NotImplementedError` until the PR merges. |
| 2 | **What is the exact Paperless-ngx API filter syntax for tags?** | **Resolved (M1 spike)** | `tags__name__istartswith=stamp:` works. Confirmed in Paperless-ngx `filters.py` (Django REST Framework filter). |
| 3 | **How does Paperless-ngx handle archive file creation when none exists?** | **Deferred** | The versioning API (PR #12061) should handle this naturally (uploading a version to any document). Will validate once PR merges. For now, documents without an archive file are skipped and tagged with `stamp:error`. |
