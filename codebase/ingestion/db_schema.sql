-- ingestion/db_schema.sql — PostgreSQL schema for Enterprise Knowledge Copilot Phase 1
-- Run once: psql -U postgres -d enterprise_copilot -f ingestion/db_schema.sql

-- ── Extensions ──────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- for full-text search on tickets

-- ── Documents ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    doc_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           VARCHAR(512) NOT NULL,
    source_file     TEXT NOT NULL,
    source_type     VARCHAR(50) NOT NULL,   -- PDF, DOCX, PPTX, CSV
    doc_type        VARCHAR(50),            -- SOP, Policy, Runbook, Guide, Ticket
    department      VARCHAR(100),
    doc_origin      VARCHAR(20) DEFAULT 'INTERNAL',  -- INTERNAL | EXTERNAL
    priority_tier   INTEGER DEFAULT 1,
    sensitivity     VARCHAR(20) DEFAULT 'internal',
    status          VARCHAR(20) DEFAULT 'PENDING',
    chunk_count     INTEGER DEFAULT 0,
    ingested_at     TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    checksum        VARCHAR(64),            -- SHA-256 for dedup
    metadata_json   JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_documents_dept ON documents(department);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_checksum ON documents(checksum);

-- ── Chunks ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id          UUID REFERENCES documents(doc_id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    section_title   TEXT,
    page_number     INTEGER,
    token_count     INTEGER,
    content         TEXT NOT NULL,
    keywords        TEXT[],
    hypothetical_q  TEXT[],            -- 2 HyDE questions per chunk
    embedding_model VARCHAR(100),
    qdrant_indexed  BOOLEAN DEFAULT FALSE,
    indexed_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_qdrant ON chunks(qdrant_indexed);

-- ── Tickets (structured store) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id       VARCHAR(50) PRIMARY KEY,
    subject         TEXT NOT NULL,
    description     TEXT,
    priority        VARCHAR(20),
    category        VARCHAR(100),
    status          VARCHAR(50) DEFAULT 'open',
    resolution      TEXT,
    requester_email VARCHAR(255),
    created_at      TIMESTAMP,
    resolved_at     TIMESTAMP,
    source_system   VARCHAR(50) DEFAULT 'CSV',
    -- Full-text search vector
    fts_vector      TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(subject,'') || ' ' || coalesce(description,'') || ' ' || coalesce(resolution,''))
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_tickets_fts ON tickets USING GIN(fts_vector);
CREATE INDEX IF NOT EXISTS idx_tickets_category ON tickets(category);
CREATE INDEX IF NOT EXISTS idx_tickets_priority ON tickets(priority);

-- ── Users (simple for Phase 1) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    username        VARCHAR(100) NOT NULL,
    department      VARCHAR(100),
    role            VARCHAR(50) DEFAULT 'employee',
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Default demo user
INSERT INTO users (email, username, department, role)
VALUES ('demo@company.com', 'Demo User', 'IT', 'employee')
ON CONFLICT (email) DO NOTHING;

-- ── Chat Sessions ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID REFERENCES users(user_id),
    title           VARCHAR(255) DEFAULT 'New Chat',
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- ── Chat Messages ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL,  -- user | assistant
    content         TEXT NOT NULL,
    citations       JSONB DEFAULT '[]',    -- [{source, page, section, score}]
    confidence      FLOAT,
    retrieval_meta  JSONB DEFAULT '{}',
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id);

-- ── Audit Logs ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID,
    session_id      UUID,
    action          VARCHAR(100),
    query_text      TEXT,
    confidence      FLOAT,
    chunks_used     INTEGER,
    latency_ms      INTEGER,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ── IDP Pipeline Log ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_logs (
    log_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id          UUID,
    stage           VARCHAR(50),   -- CLASSIFY | CHUNK | EMBED | INDEX
    status          VARCHAR(20),   -- SUCCESS | FAILED | RETRY
    failure_reason  TEXT,
    retry_count     INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT NOW()
);
