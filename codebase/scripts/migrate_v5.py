"""
scripts/migrate_v5.py  —  Apply v5 migration (access_requests table)
Run: python scripts/migrate_v5.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import create_engine, text
from configs.settings import get_settings


def _statements(sql: str):
    cleaned_lines = [line for line in sql.split("\n") if not line.strip().startswith("--")]
    cleaned = "\n".join(cleaned_lines)
    return [s.strip() for s in cleaned.split(";") if s.strip() and len(s.strip()) > 5]


def migrate():
    settings = get_settings()
    engine = create_engine(settings.postgres_url)
    migration_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ingestion", "migration_v5.sql")
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

    print(f"v5 migration complete: {ok} applied, {failed} skipped (access_requests)")


if __name__ == "__main__":
    migrate()
