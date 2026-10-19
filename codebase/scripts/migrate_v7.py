"""Apply soft deletion for bundles. Safe to run multiple times."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg2
from configs.settings import get_settings

if __name__ == '__main__':
    with psycopg2.connect(get_settings().postgres_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute((Path(__file__).resolve().parents[1] / 'ingestion/migration_v7.sql').read_text(encoding='utf-8'))
    print('v7 migration complete: bundle soft deletion enabled')
