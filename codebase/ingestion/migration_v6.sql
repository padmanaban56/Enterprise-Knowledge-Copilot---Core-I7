-- migration_v6.sql
-- P3 (Metadata Flow) + P7 (PII Preservation Without Information Leakage)
-- Safe to run multiple times (IF NOT EXISTS / ON CONFLICT DO NOTHING).

-- ════════════════════════════════════════════════════════════════════════════
-- P7: PROTECTED PII VAULT
--   hash_token -> original_value mapping for deterministically-hashed PII
--   (EMAIL_HASH_xxxxxx, EMP_HASH_xxxxxx, IP_HASH_xxxxxx, ...).
--
--   * Written by ingestion/pii_redaction.py via api/main.py::_persist_pii_vault()
--     during /ingest/file, BEFORE embedding (chunk.content only ever contains
--     the hash token, never the original value).
--   * NEVER read by the retrieval path, NEVER returned by /chat or
--     /ingest/file responses, and NEVER copied into Qdrant or BM25 payloads.
--   * Intended for privileged compliance/audit tooling only — restrict
--     access via a dedicated role (see GRANT statement below; adjust role
--     name to your environment).
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS pii_vault (
    hash_token      VARCHAR(64) PRIMARY KEY,   -- e.g. 'EMAIL_HASH_73AD21'
    entity_type     VARCHAR(50) NOT NULL,      -- email | employee_id | ip_address | ...
    original_value  TEXT NOT NULL,             -- the real PII value (PROTECTED)
    doc_id          UUID,
    chunk_id        UUID,
    source_file     TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pii_vault_doc ON pii_vault(doc_id);
CREATE INDEX IF NOT EXISTS idx_pii_vault_entity_type ON pii_vault(entity_type);

-- Example access restriction (adjust role names for your environment):
--   REVOKE ALL ON pii_vault FROM PUBLIC;
--   GRANT SELECT ON pii_vault TO compliance_auditor;


-- ════════════════════════════════════════════════════════════════════════════
-- P3: METADATA-FLOW COLUMNS ON THE DOCUMENT CATALOG
--   `doc_id` now matches the SAME id stamped onto every Chunk / Qdrant
--   payload / citation (previously this table generated its own UUID,
--   independent of the retrieval-facing doc_id — see api/main.py
--   _persist_document()).
--   `project_id` / `uploaded_by` complete the metadata-flow field set in the
--   Postgres catalog, mirroring the Qdrant payload fields added by
--   retrieval/vector_store.py::upsert_chunks().
-- ════════════════════════════════════════════════════════════════════════════
ALTER TABLE documents
    ALTER COLUMN doc_id DROP DEFAULT;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS project_id   VARCHAR(255),
    ADD COLUMN IF NOT EXISTS uploaded_by  VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_documents_project_id ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_documents_uploaded_by ON documents(uploaded_by);


-- ════════════════════════════════════════════════════════════════════════════
-- NOTES — Qdrant payload backfill for documents ingested BEFORE this release
-- ════════════════════════════════════════════════════════════════════════════
-- Chunks ingested before this release will be MISSING the new payload
-- fields (repository, access_roles, project_id, uploaded_by, created_at,
-- section_hierarchy, pii_hash_map, is_image_chunk, image_path) added by
-- retrieval/vector_store.py::upsert_chunks(). This is non-breaking:
--   - VectorStore._create_payload_indexes() creates the new keyword indexes
--     regardless of whether existing points have those fields.
--   - HybridRetrievalEngine.retrieve() only applies repository/department
--     scope filters when the document-specific cascade (P4) explicitly
--     requests them — pre-existing points simply won't match those scoped
--     filters and will be picked up at Level 4 (GLOBAL).
--   - RetrievedChunk fields default to "" / [] / False for missing payload
--     keys (see retrieval/hybrid_engine.py::_rrf_fusion), so citations for
--     old chunks will just show blank repository/project_id/etc.
--
-- RECOMMENDED: re-ingest existing documents via /ingest/file (re-upload the
-- same files) to backfill the new metadata fields, PII hash tokens, and (for
-- PDF/PPTX/DOCX) the new P9 image-derived "Diagram" chunks. Re-ingestion is
-- idempotent at the Qdrant point level (chunk_id is content-stable) but will
-- create duplicate `documents` rows unless the checksum-based ON CONFLICT
-- already covers your existing rows.
