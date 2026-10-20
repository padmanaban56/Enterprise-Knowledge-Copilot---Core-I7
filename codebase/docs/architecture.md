# Enterprise Knowledge Copilot architecture

This diagram describes the current source implementation. Application components inside the FastAPI boundary are Python modules in one backend process, rather than independently deployed services. Ingestion runs as background jobs in that application. Solid arrows show calls or data access; dashed arrows mark optional paths or feedback signals.

The editable diagram is in [architecture.mmd](architecture.mmd). Paste its contents into a Mermaid-compatible viewer to render or export it.

## Main flows

1. **Ask a question:** React sends an authenticated request through the `/api` proxy. The backend applies safety checks, resolves conversation and document scope, understands the query, retrieves accessible evidence, fuses and reranks results, builds context, and generates a checked answer with citations and confidence. Certain non-document questions bypass retrieval. The optional agent uses document search, ticket lookup, and summarization tools.
2. **Add knowledge:** Uploads become cancellable background jobs. The pipeline parses documents, classifies and chunks text, hashes PII, enriches chunks using Ollama, and optionally extracts images through OCR and vision. It stores embeddings and chunk payloads in Qdrant, updates BM25, persists relational records in PostgreSQL, and preserves original files on disk. Ticket CSV imports have a separate ingestion path.
3. **Improve results:** Query analytics, evaluations, knowledge gaps, and user feedback are persisted. Feedback-derived chunk boosts influence subsequent ranking.

## Implementation details

- Qdrant stores four named vectors: `content`, `summary`, `question`, and `keyword`. The normal hybrid retrieval path explicitly searches content, question, and summary vectors plus BM25. Storing a keyword vector does not imply a separate keyword-vector search branch in that path.
- BM25 searches in backend memory; Redis persists its corpus between restarts. Redis also provides optional embedding caching.
- BGE embeddings and the MiniLM cross-encoder run locally in the Python application. Ollama serves generation models. OpenRouter is an optional answer-generation provider; selecting it does not move local ingestion or retrieval to the cloud.
- JWT authentication, repository roles, document visibility filters, and source-download authorization are enforced by the application. The sharing gateway adds password protection and API restrictions when enabled.
- Ports shown for PostgreSQL (`55432`) and Ollama (`11435`) follow this workspace's documented native setup in `LOCAL-SETUP.md`. Source defaults are PostgreSQL `5432` and Ollama `11434`; deployments can override settings.
- Model names and provider availability are configurable. The default answer model is `phi3:mini`, with separate configuration for intent classification and vision.

## Source references

- `api/main.py`: application startup, chat coordination, ingestion jobs, source downloads, and routes.
- `api/auth.py`, `api/access_control.py`, `api/guardrails.py`, `api/citation_policy.py`: authentication, authorization, safety, and citation handling.
- `retrieval/query_understanding.py`, `retrieval/hybrid_engine.py`, `retrieval/hyde_service.py`: query processing and hybrid retrieval.
- `retrieval/vector_store.py`, `retrieval/bm25_store.py`, `retrieval/embedder.py`: vector storage, sparse index, and embeddings.
- `ingestion/idp_pipeline.py`, `ingestion/image_pipeline.py`, `ingestion/pii_redaction.py`: document preparation and enrichment.
- `api/llm_service.py`, `api/model_service.py`, `api/agent_orchestrator.py`: generation, provider selection, and agent tools.
- `ui-react/vite.config.js`, `scripts/share_app.py`, workspace `LOCAL-SETUP.md`: frontend proxy and deployment topology.
