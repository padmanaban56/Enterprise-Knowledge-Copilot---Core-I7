"""
scripts/migrate_v4.py  —  Apply v4 migration (auth/RBAC, bundles, help center, admin)
Run: python scripts/migrate_v4.py
Safe to run multiple times (all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import create_engine, text
from configs.settings import get_settings


def _statements(sql: str):
    """Strip full-line `--` comments line-by-line, then split on `;`."""
    cleaned_lines = []
    for line in sql.split("\n"):
        if line.strip().startswith("--"):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    return [s.strip() for s in cleaned.split(";") if s.strip() and len(s.strip()) > 5]


def migrate():
    settings = get_settings()
    engine = create_engine(settings.postgres_url)
    migration_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ingestion", "migration_v4.sql")
    with open(migration_path, encoding="utf-8") as f:
        sql = f.read()

    ok, failed = 0, 0
    with engine.connect() as conn:
        for stmt in _statements(sql):
            try:
                conn.execute(text(stmt))
                conn.commit()
                ok += 1
            except Exception as e:
                conn.rollback()
                failed += 1
                preview = stmt.replace("\n", " ")[:80]
                print(f"  skip ({preview}...): {str(e)[:120]}")

    print(f"v4 migration complete: {ok} statements applied, {failed} skipped "
          f"(auth/RBAC fields, bundles, issues, faq_feedback, access_grants)")


if __name__ == "__main__":
    migrate()