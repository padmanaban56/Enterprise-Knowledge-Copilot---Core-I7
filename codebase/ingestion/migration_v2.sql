-- ============================================================
-- Enterprise Knowledge Copilot v2 — Additive Migration
-- Run: python scripts/migrate_v2.py
-- Does NOT drop or modify existing tables.
-- ============================================================

-- ── Knowledge Repositories ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS repositories (
    repository_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(100) UNIQUE NOT NULL,   -- HR, Finance, IT, Engineering, Projects, External
    display_name    VARCHAR(200) NOT NULL,
    description     TEXT,
    color           VARCHAR(20) DEFAULT '#6366f1',  -- UI badge color
    icon            VARCHAR(50) DEFAULT 'folder',   -- lucide icon name
    document_count  INTEGER DEFAULT 0,
    chunk_count     INTEGER DEFAULT 0,
    last_updated    TIMESTAMP DEFAULT NOW(),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Seed the 6 default repositories
INSERT INTO repositories (name, display_name, description, color, icon) VALUES
  ('HR',          'Human Resources',    'Employee handbooks, leave policies, onboarding, payroll guides', '#10b981', 'users'),
  ('Finance',     'Finance',            'Invoice SOPs, budget policies, expense procedures, tax guides',   '#f59e0b', 'banknotes'),
  ('IT',          'Information Technology', 'Network runbooks, VPN guides, incident procedures, IT SOPs',  '#3b82f6', 'server'),
  ('Engineering', 'Engineering',        'Architecture docs, API guides, deployment runbooks, tech specs',  '#8b5cf6', 'code-2'),
  ('Projects',    'Projects',           'Project charters, milestones, risk registers, sprint docs',       '#ec4899', 'kanban'),
  ('External',    'External Knowledge', 'Vendor docs, Kubernetes/GitLab/Docker public documentation',      '#64748b', 'globe')
ON CONFLICT (name) DO NOTHING;

-- ── Add repository_id to documents ───────────────────────────────────────────
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS repository_id UUID REFERENCES repositories(repository_id),
  ADD COLUMN IF NOT EXISTS access_roles  TEXT[] DEFAULT ARRAY['EMPLOYEE','MANAGER','HR','FINANCE','IT_ADMIN','EXECUTIVE'],
  ADD COLUMN IF NOT EXISTS heading_structure JSONB DEFAULT '[]',  -- [{level, title, path}]
  ADD COLUMN IF NOT EXISTS content_types TEXT[] DEFAULT '{}';      -- headings/tables/code/faq

CREATE INDEX IF NOT EXISTS idx_documents_repo ON documents(repository_id);

-- ── RBAC: enhanced users table ────────────────────────────────────────────────
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS roles      TEXT[] DEFAULT ARRAY['EMPLOYEE'],
  ADD COLUMN IF NOT EXISTS clearance  VARCHAR(20) DEFAULT 'standard';

-- ── Retrieval Analytics ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS retrieval_analytics (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    query_text      TEXT NOT NULL,
    intent          VARCHAR(50),
    repository_id   UUID REFERENCES repositories(repository_id),
    repositories_searched TEXT[],
    expanded_queries TEXT[],
    chunks_retrieved INTEGER DEFAULT 0,
    confidence      FLOAT,
    latency_ms      INTEGER,
    reranker_top_score FLOAT,
    low_confidence  BOOLEAN DEFAULT FALSE,
    user_id         UUID,
    session_id      UUID,
    timestamp       TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analytics_ts  ON retrieval_analytics(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_rep ON retrieval_analytics(repository_id);

-- ── Ticket Intelligence ───────────────────────────────────────────────────────
-- Add resolution tracking to tickets
ALTER TABLE tickets
  ADD COLUMN IF NOT EXISTS cluster_id     VARCHAR(50),     -- for ticket clustering
  ADD COLUMN IF NOT EXISTS resolution_type VARCHAR(100),   -- known-fix, escalated, self-serve
  ADD COLUMN IF NOT EXISTS similar_count   INTEGER DEFAULT 0; -- how many similar tickets exist

-- Known issue patterns mined from resolved tickets
CREATE TABLE IF NOT EXISTS known_issues (
    issue_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           VARCHAR(512) NOT NULL,
    category        VARCHAR(100),
    symptom_keywords TEXT[],
    resolution_summary TEXT,
    ticket_count    INTEGER DEFAULT 1,
    resolution_rate FLOAT DEFAULT 0.0,
    last_seen       TIMESTAMP DEFAULT NOW(),
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_known_issues_cat ON known_issues(category);

-- ── Query Expansion Cache ─────────────────────────────────────────────────────
-- Cache expanded queries for reuse (avoid redundant LLM calls)
CREATE TABLE IF NOT EXISTS query_expansion_cache (
    cache_key       VARCHAR(64) PRIMARY KEY,  -- MD5 of normalized query
    original_query  TEXT,
    expanded_queries TEXT[],
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ── Update repository stats function ─────────────────────────────────────────
CREATE OR REPLACE FUNCTION refresh_repository_stats()
RETURNS void AS $$
BEGIN
    UPDATE repositories r
    SET
        document_count = (SELECT COUNT(*) FROM documents d WHERE d.repository_id = r.repository_id),
        chunk_count    = (SELECT COUNT(*) FROM chunks c
                          JOIN documents d ON c.doc_id = d.doc_id
                          WHERE d.repository_id = r.repository_id),
        last_updated   = NOW()
    WHERE r.repository_id IN (SELECT DISTINCT repository_id FROM documents WHERE repository_id IS NOT NULL);
END;
$$ LANGUAGE plpgsql;
