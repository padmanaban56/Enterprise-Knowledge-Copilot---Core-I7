"""Persist chat context state and ordered inline events."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg2
from configs.settings import get_settings

if __name__ == '__main__':
    with psycopg2.connect(get_settings().postgres_url) as conn, conn.cursor() as cursor:
        cursor.execute((Path(__file__).resolve().parents[1]/'ingestion/migration_v8.sql').read_text())
    print('v8 migration complete: persistent chat context events enabled')
