"""
Run from enterprise-copilot folder with venv activated:
python fix_password.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import bcrypt
from sqlalchemy import create_engine, text

DB_URL = "postgresql://postgres:postgres@localhost:5432/enterprise_copilot"

password = b"password123"
new_hash = bcrypt.hashpw(password, bcrypt.gensalt(rounds=12)).decode("utf-8")
print(f"Generated hash (len={len(new_hash)}): {new_hash[:25]}...")

engine = create_engine(DB_URL)
with engine.begin() as conn:
    for email in ["demo@company.com", "admin@company.com"]:
        conn.execute(
            text("UPDATE users SET password_hash = :h WHERE email = :e"),
            {"h": new_hash, "e": email}
        )
    rows = conn.execute(text("SELECT email, length(password_hash) as hash_len FROM users")).fetchall()
    for r in rows:
        print(f"  {r.email}: hash_len={r.hash_len}")

print("Done - try logging in with password123")