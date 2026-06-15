"""
scripts/init_db.py  —  Initialize PostgreSQL schema via SQLAlchemy (no psql CLI needed)
Run: python scripts/init_db.py
"""
import re
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import create_engine, text
from configs.settings import get_settings


def _strip_comments(sql: str) -> str:
    """Remove full-line and trailing `--` SQL comments (not inside string literals
    in this schema, which contains none with `--`)."""
    cleaned_lines = []
    for line in sql.split("\n"):
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        # Remove a trailing `-- comment` if present
        line = re.sub(r"\s*--.*$", "", line)
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def init_db():
    settings = get_settings()
    print(f"Connecting to: {settings.postgres_url}")
    engine = create_engine(settings.postgres_url)

    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ingestion", "db_schema.sql")

    with open(schema_path, "r") as f:
        sql = _strip_comments(f.read())

    statements = [s.strip() for s in sql.split(";") if s.strip()]

    ok, failed = 0, 0
    for stmt in statements:
        # Each statement gets its own transaction so one failure doesn't
        # poison the rest.
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            ok += 1
        except Exception as e:
            failed += 1
            preview = stmt.replace("\n", " ")[:80]
            print(f"  Skip ({preview}...): {str(e)[:120]}")

    print(f"Database schema initialized: {ok} statements applied, {failed} skipped")


if __name__ == "__main__":
    init_db()