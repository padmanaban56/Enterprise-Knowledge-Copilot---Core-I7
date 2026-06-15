-- ============================================================
-- Enterprise Knowledge Copilot v4 — Auth / RBAC Migration
-- Adds: password_hash, access_roles, sso fields on users;
--       bundles, issue_drafts/issues, faq_feedback tables
-- Safe to run multiple times (all IF NOT EXISTS)
-- ============================================================

-- ── Users: auth & RBAC fields ─────────────────────────────────────────────────
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS password_hash   VARCHAR(255),
  ADD COLUMN IF NOT EXISTS access_roles    TEXT[] DEFAULT ARRAY['EMPLOYEE'],
  ADD COLUMN IF NOT EXISTS sso_provider    VARCHAR(50),   -- 'local' | 'azure_ad' | 'okta' | 'google' | 'saml'
  ADD COLUMN IF NOT EXISTS sso_subject     VARCHAR(255),  -- external IdP subject/user id
  ADD COLUMN IF NOT EXISTS is_active       BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS last_login_at   TIMESTAMP;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_sso ON users(sso_provider, sso_subject)
  WHERE sso_provider IS NOT NULL;

-- Default demo users with role-appropriate access (password = "password123" for local-auth demo)
-- bcrypt hash of "password123"
INSERT INTO users (email, username, department, role, password_hash, access_roles, sso_provider)
VALUES
  ('demo@company.com',  'Demo User',     'IT',      'employee', '$2b$12$3whreqEB6jzzOaoL04vekORW/MOOJRykeEaSGWFBbf79gv4Cd2KfW', ARRAY['EMPLOYEE'], 'local'),
  ('admin@company.com', 'Admin User',    'IT',      'admin',    '$2b$12$3whreqEB6jzzOaoL04vekORW/MOOJRykeEaSGWFBbf79gv4Cd2KfW', ARRAY['EMPLOYEE','IT_ADMIN','EXECUTIVE'], 'local')
ON CONFLICT (email) DO NOTHING;

-- ── Chat Sessions: active_documents for bundle apply ────────────────────────────
ALTER TABLE chat_sessions
  ADD COLUMN IF NOT EXISTS active_documents TEXT[] DEFAULT ARRAY[]::TEXT[];

-- ── Bundles (LLD §6.3) ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bundles (
    bundle_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID REFERENCES users(user_id),
    name            VARCHAR(255) NOT NULL,
    document_ids    TEXT[] DEFAULT ARRAY[]::TEXT[],
    pinned          BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_bundles_user   ON bundles(user_id);
CREATE INDEX IF NOT EXISTS idx_bundles_pinned ON bundles(user_id, pinned DESC, created_at DESC);

-- ── Issue Drafts / Submitted Issues (LLD §6.4 Help Center) ──────────────────────
CREATE TABLE IF NOT EXISTS issues (
    issue_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID REFERENCES users(user_id),
    chat_id         UUID,
    title           VARCHAR(255),
    description     TEXT,
    category        VARCHAR(100),
    priority        VARCHAR(20) DEFAULT 'medium',
    attachments     JSONB DEFAULT '[]',
    status          VARCHAR(20) DEFAULT 'draft',  -- draft | submitted
    submitted_at    TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, chat_id, status)
);

CREATE INDEX IF NOT EXISTS idx_issues_user   ON issues(user_id);
CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);

-- ── FAQ Feedback (LLD §6.4) ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS faq_feedback (
    faq_feedback_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID REFERENCES users(user_id),
    faq_id          VARCHAR(64) NOT NULL,
    vote            SMALLINT NOT NULL,  -- 1 = thumbs_up, -1 = thumbs_down
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, faq_id)
);

CREATE INDEX IF NOT EXISTS idx_faq_feedback_faq ON faq_feedback(faq_id);

-- ── Access Grants (admin grant_access endpoint) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS access_grants (
    grant_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID REFERENCES users(user_id),
    resource_type   VARCHAR(50) NOT NULL,  -- 'collection' | 'tenant' | 'repository'
    resource_name   VARCHAR(255) NOT NULL,
    role            VARCHAR(50) NOT NULL,
    granted_by      UUID REFERENCES users(user_id),
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, resource_type, resource_name, role)
);

CREATE INDEX IF NOT EXISTS idx_grants_user ON access_grants(user_id);
