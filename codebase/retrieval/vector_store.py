"""
retrieval/vector_store.py  —  Qdrant vector store with 4-vector schema

Stores four named vectors per chunk (all same embedding model/dimension):
  - "content"  : embedding of the chunk text
  - "summary"  : embedding of the LLM-generated chunk_summary
  - "question" : embedding of the LLM-generated chunk_questions
  - "keyword"  : embedding of the LLM-generated chunk_keywords

This multi-vector approach significantly improves recall for question-style,
keyword-style, and summary-style queries.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    NamedVector,
    PayloadSchemaType,
    PointStruct,
    SearchRequest,
    VectorParams,
)

from configs.settings import get_settings
from ingestion.idp_pipeline import Chunk
from retrieval.embedder import get_embedder

logger = logging.getLogger(__name__)
settings = get_settings()


class VectorStore:
    """
    Qdrant wrapper. Four named vectors per point:
      - "content"  : BGE embedding of chunk text
      - "summary"  : BGE embedding of chunk_summary (LLM-generated summary)
      - "question" : BGE embedding of chunk_questions (LLM-generated questions)
      - "keyword"  : BGE embedding of chunk_keywords (LLM-generated keywords)

    All four use the same embedding model/dimension — only the input text
    differs per vector.
    """

    COLLECTION = settings.qdrant_collection

    # Names of the named vectors every point in COLLECTION must have, and
    # that `_ensure_collection()` declares when creating the collection.
    VECTOR_NAMES = ("content", "summary", "question", "keyword")

    def __init__(self):
        self.client = QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
        )
        self.embedder = get_embedder()
        self._ensure_collection()

    def _ensure_collection(self):
        """Create collection if it doesn't exist.

        Declares all 4 named vectors (content/summary/question/keyword) —
        all same dimension since they share one embedder, only the input
        text differs. If the collection ALREADY exists with a different
        vector schema (e.g. only "content"/"question" from before the
        summary/keyword vectors were added), upserts will fail with
        Qdrant's "Not existing vector name" error — see
        `_warn_if_schema_mismatch()` below.
        """
        existing = [c.name for c in self.client.get_collections().collections]
        if self.COLLECTION not in existing:
            dim = self.embedder.dim
            self.client.create_collection(
                collection_name=self.COLLECTION,
                vectors_config={
                    name: VectorParams(size=dim, distance=Distance.COSINE)
                    for name in self.VECTOR_NAMES
                },
            )
            self._create_payload_indexes()
            logger.info(f"Created Qdrant collection: {self.COLLECTION} (dim={dim}, "
                        f"vectors={list(self.VECTOR_NAMES)})")
        else:
            self._warn_if_schema_mismatch()

    def _warn_if_schema_mismatch(self):
        """If an existing collection's named vectors don't match
        VECTOR_NAMES, log a loud, actionable warning. Doesn't auto-delete
        data — `upsert_chunks()` will raise Qdrant's "Not existing vector
        name" error until the collection is recreated (e.g. via the Admin
        Danger Zone -> Clear Everything, which calls `clear_collection()`)."""
        try:
            info = self.client.get_collection(self.COLLECTION)
            configured = set(info.config.params.vectors.keys())
        except Exception as e:
            logger.debug(f"Could not inspect '{self.COLLECTION}' vector schema: {e}")
            return

        expected = set(self.VECTOR_NAMES)
        if configured != expected:
            logger.warning(
                f"Qdrant collection '{self.COLLECTION}' has named vectors "
                f"{sorted(configured)} but this code expects "
                f"{sorted(expected)}. upsert_chunks() will fail with "
                f"'Not existing vector name' until the collection is "
                f"recreated — use Admin > Danger Zone > Clear Everything "
                f"(or `DELETE /collections/{self.COLLECTION}` on Qdrant "
                f"directly, then restart the backend) to recreate it with "
                f"the correct schema. This will require re-uploading "
                f"documents."
            )

    def _create_payload_indexes(self):
        """
        P3: payload indexes for every field used as a Qdrant filter — including
        the metadata-flow fields (repository, access_roles, doc_id) that
        previously existed only in Postgres and never reached Qdrant.
        Safe to call repeatedly (Qdrant ignores duplicate index creation errors).

        Uses the `PayloadSchemaType.KEYWORD` enum (rather than the bare string
        "keyword") for compatibility across qdrant-client versions — some
        versions/servers reject a raw string `field_schema` value.
        """
        for field_name in [
            "department", "doc_type", "doc_origin", "priority_tier",
            "repository", "access_roles", "doc_id", "project_id",
            "is_image_chunk",
        ]:
            try:
                self.client.create_payload_index(
                    collection_name=self.COLLECTION,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception as e:
                # Non-fatal: filtering on this field will simply be unindexed
                # (slower) until the index is created successfully. Logged at
                # warning (not debug) so this is visible if it keeps failing.
                logger.warning(f"Payload index for '{field_name}' could not be created: {e}")

    def upsert_chunks(self, chunks: List[Chunk]) -> int:
        """
        Embed and upsert a batch of chunks.
        Returns number of successfully indexed chunks.

        Writes 4 named vectors per point (content/summary/question/keyword —
        see VECTOR_NAMES) plus the FULL metadata-flow field set to the Qdrant
        payload — repository, access_roles, project_id, uploaded_by,
        section_hierarchy, created_at, chunk_summary/chunk_keywords/
        chunk_questions, plus image-aware fields (is_image_chunk, image_path)
        — so these survive Ingestion -> Chunk -> Qdrant -> Retrieval -> Citation.

        If the collection's existing vector schema doesn't include all of
        VECTOR_NAMES, Qdrant raises "Not existing vector name" here — see
        `_warn_if_schema_mismatch()`.
        """
        if not chunks:
            return 0

        contents = [c.content for c in chunks]
        content_vecs = self.embedder.embed_documents(contents)

        # ── Summary embeddings ──────────────────────────────────────────
        summary_texts = []
        for c in chunks:
            summary = (c.chunk_summary or "").strip()
            summary_texts.append(summary if summary else c.content[:200])
        summary_vecs = self.embedder.embed_documents(summary_texts)

        # ── Question embeddings ─────────────────────────────────────────
        question_texts = []
        for c in chunks:
            if c.chunk_questions:
                question_texts.append("\n".join(c.chunk_questions))
            elif c.hypothetical_questions:
                question_texts.append("\n".join(c.hypothetical_questions))
            else:
                question_texts.append(c.content[:200])
        question_vecs = self.embedder.embed_documents(question_texts)

        # ── Keyword embeddings ──────────────────────────────────────────
        keyword_texts = []
        for c in chunks:
            if c.chunk_keywords:
                keyword_texts.append("\n".join(c.chunk_keywords))
            elif c.keywords:
                keyword_texts.append("\n".join(c.keywords))
            else:
                keyword_texts.append(c.content[:200])
        keyword_vecs = self.embedder.embed_documents(keyword_texts)

        now_iso = datetime.utcnow().isoformat()
        points = []
        for i, chunk in enumerate(chunks):
            # Back-compat: hybrid_engine.py's _rrf_fusion/_citation_dict/
            # _compute_entity_boost read payload["keywords"] and
            # payload["hypothetical_questions"]. Populate them from the new
            # chunk_keywords/chunk_questions fields (falling back to the
            # legacy fields) so those code paths keep working unchanged.
            legacy_keywords = chunk.chunk_keywords or chunk.keywords or []
            legacy_questions = chunk.chunk_questions or chunk.hypothetical_questions or []

            payload = {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "section_title": chunk.section_title or "",
                "section_hierarchy": getattr(chunk, "section_hierarchy", []) or [],
                "page_number": chunk.page_number,
                "doc_type": chunk.doc_type or "Guide",
                "department": chunk.department or "Unknown",
                "doc_origin": chunk.doc_origin or "INTERNAL",
                "priority_tier": chunk.priority_tier or 1,
                "source_file": chunk.source_file or "",
                # New 4-vector-schema fields
                "chunk_summary": chunk.chunk_summary or "",
                "chunk_keywords": chunk.chunk_keywords or [],
                "chunk_questions": chunk.chunk_questions or [],
                # Back-compat fields (read by retrieval/hybrid_engine.py)
                "keywords": legacy_keywords,
                "hypothetical_questions": legacy_questions,
                "ingested_at": now_iso,
                # ── P3: metadata-flow fields (previously Postgres-only / missing) ──
                "repository": getattr(chunk, "repository", "") or chunk.department or "Unknown",
                "access_roles": getattr(chunk, "access_roles", None) or
                                ["EMPLOYEE", "MANAGER", "HR", "FINANCE", "IT_ADMIN", "EXECUTIVE"],
                "project_id": getattr(chunk, "project_id", "") or "",
                "uploaded_by": getattr(chunk, "uploaded_by", "") or "",
                "created_at": getattr(chunk, "created_at", "") or now_iso,
                # ── P7: PII hash map for this chunk (hash -> type), if any ──
                "pii_hash_map": getattr(chunk, "pii_hash_map", {}) or {},
                # ── P9: image-aware retrieval ──
                "is_image_chunk": getattr(chunk, "is_image_chunk", False),
                "image_path": getattr(chunk, "image_path", "") or "",
            }
            points.append(
                PointStruct(
                    id=chunk.chunk_id,
                    vector={
                        "content": content_vecs[i].tolist(),
                        "summary": summary_vecs[i].tolist(),
                        "question": question_vecs[i].tolist(),
                        "keyword": keyword_vecs[i].tolist(),
                    },
                    payload=payload,
                )
            )

        self.client.upsert(collection_name=self.COLLECTION, points=points, wait=True)
        logger.info(f"Upserted {len(points)} chunks to Qdrant")
        return len(points)

    def search(
        self,
        query: str,
        top_k: int = 20,
        filters: Optional[Dict[str, Any]] = None,
        vector_name: str = "content",
    ) -> List[Dict]:
        """
        Dense ANN search.
        vector_name: one of "content", "summary", "question", "keyword"
                     (see VECTOR_NAMES)
        Returns list of {chunk_id, score, payload} dicts.
        """
        query_vec = self.embedder.embed_query(query)
        qdrant_filter = self._build_filter(filters) if filters else None

        results = self.client.search(
            collection_name=self.COLLECTION,
            query_vector=NamedVector(name=vector_name, vector=query_vec.tolist()),
            query_filter=qdrant_filter,
            limit=top_k,
            with_payload=True,
        )

        return [
            {"chunk_id": r.id, "score": r.score, "payload": r.payload}
            for r in results
        ]

    def _build_filter(self, filters: Dict[str, Any]) -> Filter:
        """Convert dict filters to Qdrant Filter object."""
        conditions = []
        for key, val in filters.items():
            if isinstance(val, list):
                conditions.append(
                    FieldCondition(key=key, match=MatchAny(any=val))
                )
            else:
                conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=val))
                )
        return Filter(must=conditions) if conditions else None

    def get_collection_info(self) -> Dict:
        info = self.client.get_collection(self.COLLECTION)
        return {
            "vectors_count": info.vectors_count,
            "points_count": info.points_count,
            "status": str(info.status),
        }

    def get_chunks_by_doc(self, doc_id: str, source_file: Optional[str] = None, limit: int = 500) -> List[Dict]:
        """
        Fetch all chunks belonging to a document (used by the document
        detail page — content, summary, keywords per chunk).

        Returns a list of payload dicts (with "chunk_id" included),
        sorted by chunk_index (falling back to page_number) so chunks
        render in document order.

        Some documents — e.g. ingested before doc_id was stamped
        consistently between Postgres and Qdrant (see `_persist_document`
        in api/main.py) — won't have any Qdrant points matching
        `doc_id`. If the doc_id filter returns nothing and `source_file`
        is provided, fall back to matching on the chunk payload's
        `source_file` field (the original filename), which is stamped on
        every chunk regardless of doc_id consistency.
        """
        def _scroll(filter_: Filter) -> List[Dict]:
            try:
                points, _ = self.client.scroll(
                    collection_name=self.COLLECTION,
                    scroll_filter=filter_,
                    limit=limit,
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception as e:
                logger.warning(f"get_chunks_by_doc scroll failed: {e}")
                return []
            out = []
            for p in points:
                payload = dict(p.payload or {})
                payload["chunk_id"] = payload.get("chunk_id") or p.id
                out.append(payload)
            return out

        chunks = _scroll(Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        ))

        if not chunks and source_file:
            filename = source_file.split("/")[-1]
            chunks = _scroll(Filter(
                must=[FieldCondition(key="source_file", match=MatchValue(value=filename))]
            ))
            if not chunks and filename != source_file:
                chunks = _scroll(Filter(
                    must=[FieldCondition(key="source_file", match=MatchValue(value=source_file))]
                ))

        chunks.sort(key=lambda c: (
            c.get("chunk_index") if c.get("chunk_index") is not None else 10**9,
            c.get("page_number") or 0,
        ))
        return chunks

    def update_payload_for_doc(self, doc_id: str, payload_updates: Dict[str, Any], source_file: Optional[str] = None) -> int:
        """
        Update payload fields (e.g. repository, doc_origin, access_roles)
        on every Qdrant point belonging to `doc_id`, so RBAC/repository
        filtering at retrieval time stays in sync with edits made on the
        Document detail page. Returns the number of points matched.

        Falls back to matching on `source_file` (see `get_chunks_by_doc`)
        if no points have a matching `doc_id` payload field.
        """
        if not payload_updates:
            return 0

        def _apply(filter_: Filter) -> int:
            try:
                self.client.set_payload(
                    collection_name=self.COLLECTION,
                    payload=payload_updates,
                    points=FilterSelector(filter=filter_),
                    wait=True,
                )
                points, _ = self.client.scroll(
                    collection_name=self.COLLECTION,
                    scroll_filter=filter_,
                    limit=1,
                    with_payload=False,
                    with_vectors=False,
                )
                return len(points)
            except Exception as e:
                logger.warning(f"update_payload_for_doc failed: {e}")
                return 0

        doc_filter = Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
        matched = _apply(doc_filter)

        if matched == 0 and source_file:
            filename = source_file.split("/")[-1]
            file_filter = Filter(must=[FieldCondition(key="source_file", match=MatchValue(value=filename))])
            matched = _apply(file_filter)

        return matched

    def clear_collection(self):
        """
        Delete and recreate the Qdrant collection (wipes all vectors/points).
        Used by the admin 'Clear Knowledge Base' action.
        """
        try:
            self.client.delete_collection(collection_name=self.COLLECTION)
        except Exception as e:
            logger.warning(f"Qdrant delete_collection failed (may not exist): {e}")
        self._ensure_collection()