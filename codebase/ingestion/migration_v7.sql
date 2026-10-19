-- Preserve deleted bundles and permit reusing their names.
ALTER TABLE bundles ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE bundles DROP CONSTRAINT IF EXISTS bundles_user_id_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_bundles_active_name
    ON bundles (user_id, name) WHERE deleted_at IS NULL;
