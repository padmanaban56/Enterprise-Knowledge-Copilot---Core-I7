-- migration_v4b.sql: add must_change_password flag to users
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT FALSE;

-- existing demo users must change password on first login
UPDATE users SET must_change_password = TRUE WHERE password_hash IS NULL OR password_hash = '';
