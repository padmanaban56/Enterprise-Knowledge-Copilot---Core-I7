-- migration_v5.sql: Access Requests workflow + audit logging FK convenience
-- Safe to run multiple times (IF NOT EXISTS / ON CONFLICT DO NOTHING).

CREATE TABLE IF NOT EXISTS access_requests (
    request_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(user_id),
    resource_type   VARCHAR(50) NOT NULL DEFAULT 'access_role',  -- 'access_role' | 'repository'
    resource_name   VARCHAR(255) NOT NULL,                       -- e.g. 'FINANCE', 'HR'
    justification   TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',      -- pending | approved | rejected
    resolved_by     UUID REFERENCES users(user_id),
    rejection_reason TEXT,
    requested_at    TIMESTAMP DEFAULT NOW(),
    resolved_at     TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_access_requests_user ON access_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_access_requests_status ON access_requests(status);
