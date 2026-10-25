# Attachment Document Parsing Handoff

## Goal and Scope

Track delivery against the attachment document parsing spec supplied for Datrix. V1 covers PDF, DOCX, PPTX, DOC, XLSX, XLS, TXT, Markdown, and HTML. OCR, image understanding, and vector-index rollout remain out of V1 unless separately approved.

## Baseline (2026-10-13)

The working tree already contains a substantial, uncommitted implementation. Preserve those edits; do not reset or overwrite unrelated changes.

- Parser models, format detection, bounded parsing, cache, worker isolation, and parsers for the nine formats exist under `packages/dbgpt-app/src/dbgpt_app/attachment/`.
- DOCX parsing exposes paragraph/table locations, table cells, and template placeholder tokens. XLSX/XLS parsing exposes worksheet, row, cell coordinate, formula, merged-range, style, and placeholder metadata.
- The chat ReAct path parses submitted files and records compact parse summaries in session state.
- A synchronous parse API and attachment tools are mounted (`list`, `parse`, keyword `search`, region `read`, and export).
- Uploads preserve path compatibility and now also return stable attachment objects/IDs; normalized blocks have IDs.
- A generic `document-analysis` skill and basic parse lifecycle display are now included. Full preview remains out of scope.
- Async job/status APIs, durable retries/recovery, per-user parse concurrency limits, structural chunk persistence, and scoped BM25 retrieval are implemented as opt-in/automatic backend paths. Vector embeddings, hybrid ranking, and OCR are not implemented.

## Delivery Plan

| Part | Scope | Status |
|---|---|---|
| 1. Stable attachment contract | Return attachment metadata and stable IDs while preserving legacy path fields; carry IDs into parse results and block IDs | Complete |
| 2. Agent access | Prefer attachment IDs in tools/API; add text export in Markdown, JSON, TXT, and table-only CSV | Complete |
| 3. User workflow | Add a generic document-analysis skill and expose parse status in the chat attachment UI | Complete |
| 4a. Async and lexical index | Durable parse jobs, status polling, retry/recovery, chunk persistence, scoped BM25 search | Complete |
| 4b. Vector hybrid retrieval | Configured embeddings and BM25/vector hybrid ranking | In progress; embedding provider/configuration is not available in the current runtime config |
| 5. OCR and multimodal retrieval | OCR/image understanding | Not started |

## Acceptance Notes

- Keep old upload `data` paths and `file_path`/`file_paths` chat fields working during migration.
- Ensure IDs resolve only to files attached to the current request/session and still enforce the authenticated user's upload root.
- Keep export bounded by the existing parse limits; CSV export must reject/non-include non-table blocks.
- Keep the reserved `.attachments.json` metadata filename unavailable to uploads.
- Avoid full-repository lint/build/service restarts for these changes. Run only tests covering touched behavior.

## Latest Verification

- `conda run -n dataman python -m pytest packages/dbgpt-app/src/dbgpt_app/tests/test_attachment_parsers.py packages/dbgpt-app/src/dbgpt_app/tests/test_attachment_api.py -q`: 19 passed.
- `conda run -n dataman python -m pytest packages/dbgpt-app/src/dbgpt_app/tests/test_python_upload_api.py -q`: 9 passed.
- After Parts 1–3: focused attachment/parser/upload tests: 29 passed.
- Phase 4 targeted API/upload regression after async/index changes: **19 passed**.
- Latest focused API/upload regression before API lifecycle coverage: **21 passed**; changed backend modules compile successfully.
- API-level async submission, status polling, and cross-user status isolation are covered; final focused regression now has **22 passed**.
- SQLite FTS5 smoke check returned matches through the BM25 query path.
- Phase 4 changed backend modules compile successfully with `py_compile`.

## Part 1 Completed (2026-10-13)

- Upload responses now include `attachments` metadata with `attachment_id`, filename, size, MIME type, SHA-256, storage path, status, and extension format.
- Legacy `data` paths, `file_path`, and `file_paths` remain available for existing frontend callers.
- `attachment_ids` can accompany parse requests; automatic ReAct parsing carries upload IDs into `AttachmentParseResult` and session state.
- Normalized blocks receive stable per-result IDs such as `b_00001`; the parser cache version was bumped so old cache entries are regenerated.
- Agent state can resolve tools by attachment ID while retaining path compatibility; the prompt now exposes document summaries instead of raw absolute paths.
- Focused tests were added for aligned IDs, block IDs, and upload metadata. Existing attachment/upload tests remain the validation target.

## Part 2 Next

Part 3 adds the generic document-analysis Skill and visible parse lifecycle in the chat attachment card.

## Part 2 Completed (2026-10-13)

- All attachment selection tools accept stable `attachment_id` in addition to legacy paths.
- Added bounded `export_attachment_text` with Markdown, JSON, TXT, and table-only CSV output.
- Export responses retain attachment ID and filename; CSV emits only structured table rows.
- Focused attachment/API/upload tests: 29 passed.

## Part 3 Completed (2026-10-13)

- Added `skills/document-analysis/SKILL.md` with the required attachment-tool workflow for summaries, extraction, comparison, citations, warnings, and exports.
- Included the Skill in the app package's built-in skill distribution.
- ReAct emits `attachment.status` after automatic parsing; the chat attachment card now shows completed/failed/OCR-needed status and page/slide counts when available.
- Frontend changes are limited to the existing attachment card and SSE handling; no preview pane or polling service was added.

## Part 4A Completed (2026-10-13)

- Added a SQLite-backed parse-job table at `pilot/meta_data/attachment_jobs/jobs.sqlite3`; queued, parsing, completed, partial, failed, needs-OCR, and unsupported states are persisted.
- Added lease recovery, bounded retry/backoff, duplicate request reuse, and a shared per-user active-job limit across manager instances.
- Added opt-in `async_parse` submission with the existing synchronous behavior retained by default; `GET /v1/attachments/parse/{job_id}` is mounted, polls durable state, and enforces user ownership.
- Added structure-aware chunking with block IDs, locations, table text, configurable chunk size/overlap defaults, and a private SQLite chunk index at `pilot/meta_data/attachment_index/chunks.sqlite3`.
- Enabled SQLite FTS5 BM25 retrieval with a bounded lexical fallback, user isolation, attachment filtering, `top_k`, and table inclusion controls.
- Asynchronous jobs and attachment tools update the chunk index; `search_attachment` searches persisted chunks instead of reparsing blocks for every match. The synchronous parse API remains a direct parse-and-return path and does not wait for indexing.
- Reserved `.attachments.json` cannot be uploaded as a user file, preventing manifest corruption.

## Part 4B Next

- Vector indexing is not enabled: the current chunk store persists structural chunks and BM25/FTS5 data only.
- Hybrid ranking needs an explicit embedding provider/configuration and failure policy before enabling network or model calls in the parse worker.
- The frontend still exposes only the existing parse lifecycle; asynchronous status polling and a dedicated preview panel remain optional UX work.

## Closeout (2026-10-13)

- Python compile check passed for all changed attachment/backend modules.
- Focused attachment/parser/upload tests passed: **29 passed**.
- ESLint passed for `web/pages/index.tsx` and `web/new-components/chat/content/ManusLeftPanel.tsx` after project formatter fixes.
- `git diff --check` passed for the changed attachment, frontend, test, and handoff files.
- No service restart or full frontend build was run, per request to avoid unnecessary validation.

## Deferred Follow-up

- Phase 4B remaining: vector indexing and BM25/vector hybrid ranking. Async jobs now use the existing parser/cache and are opt-in via `async_parse: true` for backward compatibility.
- Phase 5: OCR and multimodal/image understanding.
- Optional UX: dedicated preview panel, citation click-through, and richer warning details.
- The upload response still keeps `data` as legacy paths; migrate external clients to `attachments`/`attachment_id` before removing compatibility fields.
