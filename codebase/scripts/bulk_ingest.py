"""
scripts/bulk_ingest.py  —  Bulk ingest a folder of PDFs/DOCX/PPTX files

Usage:
  python scripts/bulk_ingest.py --folder /path/to/gitlab/handbooks
  python scripts/bulk_ingest.py --folder ./data --department Operations
  python scripts/bulk_ingest.py --tickets tickets.csv

The script talks to the running FastAPI backend.
"""
import argparse
import os
import sys
import time
from pathlib import Path

import httpx

API_BASE = "http://localhost:8000"


def ingest_file(filepath: Path, department: str = None, doc_origin: str = "INTERNAL") -> dict:
    with open(filepath, "rb") as f:
        files = {"file": (filepath.name, f, "application/octet-stream")}
        data = {"doc_origin": doc_origin}
        if department:
            data["department"] = department

        resp = httpx.post(
            f"{API_BASE}/ingest/file",
            files=files,
            data=data,
            timeout=120.0,
        )
        return resp.json()


def ingest_tickets(filepath: Path) -> dict:
    with open(filepath, "rb") as f:
        resp = httpx.post(
            f"{API_BASE}/ingest/tickets",
            files={"file": (filepath.name, f, "text/csv")},
            timeout=600.0,
        )
        return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Bulk ingest documents into Knowledge Copilot")
    parser.add_argument("--folder", help="Folder containing PDF/DOCX/PPTX files")
    parser.add_argument("--file", help="Single file to ingest")
    parser.add_argument("--tickets", help="Path to tickets CSV file")
    parser.add_argument("--department", help="Override department classification")
    parser.add_argument("--origin", default="INTERNAL", choices=["INTERNAL", "EXTERNAL"])
    args = parser.parse_args()

    # Check backend
    try:
        r = httpx.get(f"{API_BASE}/status", timeout=5)
        print(f"✅ Backend connected. Qdrant: {r.json().get('qdrant', {}).get('vectors_count', 0)} vectors")
    except Exception:
        print("❌ Backend not reachable. Start it with: python -m uvicorn api.main:app --port 8000")
        sys.exit(1)

    # Ingest tickets
    if args.tickets:
        path = Path(args.tickets)
        print(f"\n🎫 Ingesting tickets: {path.name}")
        result = ingest_tickets(path)
        print(f"  ✅ {result.get('tickets_ingested', 0)} tickets indexed")

    # Ingest single file
    if args.file:
        path = Path(args.file)
        print(f"\n📄 Ingesting: {path.name}")
        result = ingest_file(path, args.department, args.origin)
        print(f"  ✅ {result}")

    # Ingest folder
    if args.folder:
        folder = Path(args.folder)
        supported_exts = {".pdf", ".docx", ".pptx"}
        files = [f for f in folder.rglob("*") if f.suffix.lower() in supported_exts]

        print(f"\n📂 Found {len(files)} files in {folder}")

        success, failed = 0, 0
        for i, filepath in enumerate(files, 1):
            print(f"  [{i}/{len(files)}] {filepath.name}...", end=" ", flush=True)
            try:
                result = ingest_file(filepath, args.department, args.origin)
                chunks = result.get("chunks_created", 0)
                dept = result.get("department", "?")
                print(f"✅ {chunks} chunks | {dept}")
                success += 1
            except Exception as e:
                print(f"❌ {str(e)[:60]}")
                failed += 1
            time.sleep(0.2)  # small delay between files

        print(f"\n📊 Done: {success} succeeded, {failed} failed")
        print(f"   Total vectors now: ", end="")
        try:
            r = httpx.get(f"{API_BASE}/status", timeout=5)
            print(r.json().get("qdrant", {}).get("vectors_count", "?"))
        except Exception:
            print("?")


if __name__ == "__main__":
    main()
