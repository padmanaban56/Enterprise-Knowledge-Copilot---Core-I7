"""
scripts/recreate_qdrant_collection.py

Deletes the Qdrant collection so it gets recreated with the current 4-vector
schema (content/summary/question/keyword) the next time the backend starts
(VectorStore.__init__ -> _ensure_collection()).

IMPORTANT: stop the backend (Ctrl+C the uvicorn process) BEFORE running this
script, and don't start it again until this script has finished. If the
backend is running while you delete the collection, its already-constructed
VectorStore won't recreate it — only `_ensure_collection()` at startup does.

Usage:
  1. Stop the backend.
  2. python scripts/recreate_qdrant_collection.py
  3. Start the backend again — watch the startup log for:
       "Created Qdrant collection: enterprise_knowledge (dim=384,
        vectors=['content', 'summary', 'question', 'keyword'])"
  4. Re-upload your documents (the deleted collection had no usable vectors
     for the new schema anyway, so nothing of value is lost).
"""
import sys

from qdrant_client import QdrantClient

from configs.settings import get_settings

settings = get_settings()


def main():
    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    collection = settings.qdrant_collection

    existing = [c.name for c in client.get_collections().collections]
    print(f"Qdrant at {settings.qdrant_host}:{settings.qdrant_port}")
    print(f"Existing collections: {existing}")

    if collection not in existing:
        print(f"Collection '{collection}' does not exist — nothing to delete. "
              f"It will be created fresh (with the 4-vector schema) on next "
              f"backend startup.")
        return

    info = client.get_collection(collection)
    try:
        current_vectors = sorted(info.config.params.vectors.keys())
    except Exception:
        current_vectors = "<unable to read>"
    print(f"Collection '{collection}' currently has named vectors: {current_vectors}")
    print(f"Points count: {info.points_count}")

    confirm = input(f"Delete collection '{collection}'? Re-upload will be "
                     f"required afterwards. Type DELETE to confirm: ")
    if confirm.strip().upper() != "DELETE":
        print("Aborted — nothing deleted.")
        sys.exit(0)

    client.delete_collection(collection_name=collection)
    print(f"Deleted '{collection}'. Now START THE BACKEND — it will be "
          f"recreated automatically with vectors=['content', 'summary', "
          f"'question', 'keyword'].")


if __name__ == "__main__":
    main()