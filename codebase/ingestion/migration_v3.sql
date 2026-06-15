-- ============================================================
-- Enterprise Knowledge Copilot v3 — Migration
-- Adds: knowledge_gaps, user_feedback, chunk_feedback_boosts
-- Safe to run multiple times (all IF NOT EXISTS)
-- ============================================================

-- ── Knowledge Gaps ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS knowledge_gaps (
    gap_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    query_hash      VARCHAR(32) UNIQUE,   -- MD5 of normalized query
    query_text      TEXT NOT NULL,
    intent          VARCHAR(50),
    repositories_searched TEXT[],
    frequency       INTEGER DEFAULT 1,
    resolved        BOOLEAN DEFAULT FALSE,
    resolved_at     TIMESTAMP,
    last_seen       TIMESTAMP DEFAULT NOW(),
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gaps_resolved ON knowledge_gaps(resolved);
CREATE INDEX IF NOT EXISTS idx_gaps_freq     ON knowledge_gaps(frequency DESC);

-- Add query_hash as computed column from query_text (populated on insert)
ALTER TABLE knowledge_gaps
  ADD COLUMN IF NOT EXISTS query_hash_auto VARCHAR(32)
  GENERATED ALWAYS AS (md5(lower(trim(query_text)))) STORED;

-- Drop and recreate unique on computed column
-- (skip if already exists — safe in idempotent migration)

-- ── User Feedback ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_feedback (
    feedback_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID,
    query_text      TEXT NOT NULL,
    rating          SMALLINT NOT NULL,   -- 1=positive, -1=negative, 0=neutral
    comment         TEXT,
    cited_chunk_ids TEXT[],
    repositories_used TEXT[],
    confidence      FLOAT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feedback_session ON user_feedback(session_id);
CREATE INDEX IF NOT EXISTS idx_feedback_rating  ON user_feedback(rating);
CREATE INDEX IF NOT EXISTS idx_feedback_ts      ON user_feedback(created_at DESC);

-- ── Chunk Feedback Boosts (learning loop) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunk_feedback_boosts (
    chunk_id        VARCHAR(36) PRIMARY KEY,
    boost_score     FLOAT DEFAULT 0.0,   -- range: -0.05 to +0.10
    feedback_count  INTEGER DEFAULT 0,
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- ── Add reranker_top_score to analytics if missing ───────────────────────────
ALTER TABLE retrieval_analytics
  ADD COLUMN IF NOT EXISTS reranker_top_score FLOAT DEFAULT 0.0,
  ADD COLUMN IF NOT EXISTS pipeline_stats     JSONB DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS sub_queries        TEXT[],
  ADD COLUMN IF NOT EXISTS hyde_used          BOOLEAN DEFAULT FALSE;
