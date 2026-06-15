"""
scripts/migrate_v2.py  —  Apply v2 additive database migration
Run: python scripts/migrate_v2.py
Safe to run multiple times (all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING).
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import create_engine, text
from configs.settings import get_settings


def _statements(sql: str):
    """
    Split SQL into statements on `;`, but treat `$$ ... $$` (PL/pgSQL function
    bodies) as atomic blocks — semicolons inside them do not split.
    Strips full-line `--` comments before splitting.
    """
    cleaned_lines = [
        line for line in sql.split("\n")
        if not line.strip().startswith("--")
    ]
    cleaned = "\n".join(cleaned_lines)

    # Split on `;` that is NOT inside a $$ ... $$ block
    parts = re.split(r"(\$\$.*?\$\$)", cleaned, flags=re.DOTALL)
    statements, buf = [], ""
    for part in parts:
        if part.startswith("$$") and part.endswith("$$"):
            buf += part
        else:
            segments = part.split(";")
            for i, seg in enumerate(segments):
                if i < len(segments) - 1:
                    buf += seg
                    stmt = buf.strip()
                    if stmt and len(stmt) > 5:
                        statements.append(stmt)
                    buf = ""
                else:
                    buf += seg
    if buf.strip() and len(buf.strip()) > 5:
        statements.append(buf.strip())
    return statements


def migrate():
    settings = get_settings()
    engine = create_engine(settings.postgres_url)

    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "ingestion", "migration_v2.sql"
    )

    with open(migration_path, "r", encoding="utf-8") as f:
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
                print(f"  [WARN] ({preview}...): {str(e)[:100]}")

    print(f"v2 migration complete: {ok} applied, {failed} skipped")

    # Print repository summary
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name, display_name FROM repositories ORDER BY name"))
        print("\nRepositories:")
        for row in rows:
            print(f"  - {row.name:<15} {row.display_name}")


if __name__ == "__main__":
    migrate()