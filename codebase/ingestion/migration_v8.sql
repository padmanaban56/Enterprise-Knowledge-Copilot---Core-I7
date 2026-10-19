ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS context_state JSONB;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS timeline_seq BIGSERIAL;
CREATE TABLE IF NOT EXISTS chat_context_updates (
    request_id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
