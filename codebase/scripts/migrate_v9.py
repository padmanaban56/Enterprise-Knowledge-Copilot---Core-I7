"""Enable immediate invalidation of sessions after password or role changes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text
from configs.settings import get_settings

if __name__ == '__main__':
    with create_engine(get_settings().postgres_url).begin() as conn:
        conn.execute(text((Path(__file__).resolve().parents[1] / 'ingestion/migration_v9.sql').read_text()))
    print('v9 migration complete: user session versions enabled')
