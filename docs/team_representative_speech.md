# Team Representative Speech: Implementation Overview

Good morning everyone.

We are Team Core i7, and our solution is an Enterprise Knowledge Copilot built for the NASSCOM Hackathon use case on enterprise knowledge assistance.

The problem we focused on is very common in large organizations: knowledge is spread across policies, handbooks, SOPs, runbooks, project documents, and support tickets. Employees often lose time searching across multiple systems, and even when they find an answer, they may not know whether it is reliable, current, or allowed for them to access.

Our implementation solves this by creating a secure, intelligent RAG-based copilot that can ingest enterprise documents and tickets, understand a user's question, retrieve the most relevant evidence, and generate a grounded answer with citations.

At the frontend, we built a React portal using Vite and Tailwind. The portal includes login, dashboard, chat, repositories, documents, tickets, access requests, and admin screens. This means the solution is not only a chatbot; it is a complete enterprise knowledge interface where users can browse repositories, view documents, search tickets, track analytics, and manage access.

At the backend, we implemented a FastAPI service. This backend exposes APIs for chat, document ingestion, bulk upload, ticket ingestion, repository management, document viewing, feedback, knowledge gaps, analytics, authentication, RBAC, and system health. PostgreSQL is used for structured data such as users, documents, tickets, feedback, audit logs, and analytics. Qdrant is used as the vector database for semantic retrieval, and Redis is used for BM25 persistence and caching.

The ingestion pipeline is one of the most important parts of the system. When a PDF, DOCX, or PPTX is uploaded, the system extracts the text, classifies the document by department and document type, splits it into smart chunks, preserves useful metadata like page number and section hierarchy, and then enriches the chunks with keywords, summaries, and likely user questions. We also added PII redaction and hashing, so sensitive information is protected before it reaches retrieval or the language model.

For retrieval, we implemented a hybrid pipeline instead of depending on only vector search. Every query goes through normalization, PII scrubbing, intent detection, entity extraction, repository selection, filter extraction, query expansion, HyDE when needed, and query decomposition. Then the system combines dense vector search, question-vector search, and BM25 keyword search. These results are fused using Reciprocal Rank Fusion, boosted based on document priority and metadata, reranked with a CrossEncoder model, and finally packed into a context window for the LLM.

This design improves both accuracy and trust. If a user asks a policy question, the system can find the right handbook section. If a user asks about an incident or support issue, the ticket intelligence flow can search by exact ticket ID, SQL full-text search, and semantic fallback. If the query is low confidence, the platform can track it as a knowledge gap so the organization knows where documentation needs improvement.

On top of the normal chat flow, we also implemented an agentic workflow. The agent can reason about whether it should use document search, ticket lookup, or summarization. If the local LLM is unavailable, the orchestrator falls back to a deterministic plan-execute flow, so the system still returns a usable response instead of failing silently.

Security and governance are also built into the implementation. The authentication layer supports local login and has a provider-agnostic SSO callback design. Role-based access control is carried through the JWT and used during retrieval, so users only retrieve documents that match their access roles. Admin users can manage users, approve access requests, reset passwords, and view audit logs.

For transparency, the chat UI shows the confidence score, sources, citations, pipeline trace, retrieval details, and feedback controls. This is important because enterprise AI should not be a black box. A user should be able to see where an answer came from and how confident the system is.

Finally, we optimized the implementation for realistic local hardware. Instead of requiring a heavy cloud GPU setup, we use lightweight models such as BGE-small for embeddings, MiniLM CrossEncoder for reranking, and a local Ollama model for answer generation. This keeps the system practical for demos and smaller deployments while preserving the same architecture and retrieval logic.

To summarize, our implementation is an end-to-end enterprise knowledge assistant. It ingests documents and tickets, secures them with RBAC and PII protection, retrieves answers using a multi-stage hybrid RAG pipeline, provides cited responses through a React portal, learns from feedback, identifies knowledge gaps, and includes analytics and admin workflows.

Our goal was to build something that feels close to a real enterprise product: trustworthy, explainable, secure, and useful for employees across departments.

Thank you.
