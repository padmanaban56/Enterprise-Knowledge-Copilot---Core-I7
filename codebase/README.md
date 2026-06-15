# Enterprise Knowledge Copilot — v3

> **NASSCOM Hackathon · Use Case 2** · Enterprise Knowledge Assistant (RAG + Agentic Workflow)  
> Team: Core i7

---

## What This Builds

An enterprise-grade RAG platform that goes far beyond generic chatbot RAG. The core differentiator is a **14-stage retrieval pipeline** that operates on every query, plus a feedback learning loop, knowledge gap detection, and a full React enterprise portal.

---

## Your Current Setup

| Item | Status |
|---|---|
| VS Code | ✅ Installed |
| Ollama | ✅ Installed |
| Python | ❌ Need to install |
| Docker Desktop | ❌ Need to install |
| Node.js | ❌ Need to install (for React UI) |

---

## Step 0 — Install Prerequisites (one time)

### Python 3.11
Download from https://python.org/downloads/release/python-3119/  
During install: **check "Add Python to PATH"**

Verify: open VS Code terminal (`Ctrl+\``) and run:
```
python --version
```

### Docker Desktop
Download from https://docker.com/products/docker-desktop  
Install and start Docker Desktop. Wait for the whale icon in system tray to show "Running".

Verify:
```
docker --version
```

### Node.js 20 LTS
Download from https://nodejs.org/en/download  
Install the LTS version.

Verify:
```
node --version
npm --version
```

---

## Step 1 — Extract Project

Unzip `enterprise_copilot_v3.zip` to a folder, e.g. `C:\Projects\enterprise-copilot`

Open that folder in VS Code: **File → Open Folder**

Open the integrated terminal: **Ctrl+`**

---

## Step 2 — Start Docker Services

Run these one by one in the VS Code terminal:

```bash
# Vector database (stores document embeddings)
docker run -d --name qdrant-ekc -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant:latest

# PostgreSQL (tickets, sessions, analytics, feedback)
docker run -d --name postgres-ekc -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=enterprise_copilot -p 5432:5432 postgres:16-alpine

# Redis (BM25 persistence, embedding cache)
docker run -d --name redis-ekc -p 6379:6379 redis:7-alpine
```

Verify all three are running:
```bash
docker ps
```
You should see 3 containers: `qdrant-ekc`, `postgres-ekc`, `redis-ekc`.

---

## Step 3 — Python Environment

```bash
# Create virtual environment
py -3.11 -m venv .venv

# Activate it (Windows)
.venv\Scripts\activate

# You should see (.venv) in your prompt now

# Install all packages (~5 min, downloads ~1.2GB of models)
.venv\Scripts\python.exe -m pip install --upgrade pip
pip install -r requirements.txt

# Download NLP model for entity extraction
python -m spacy download en_core_web_sm
```

---

## Step 4 — Database Setup

```bash
# Create all tables (Phase 1 schema)
python scripts/init_db.py

# Add v2 tables: repositories, RBAC, analytics
python scripts/migrate_v2.py

# Add v3 tables: knowledge gaps, feedback, chunk boosts
python scripts/migrate_v3.py

python scripts/migrate_v4.py


Get-Content ingestion/migration_v4b.sql | docker exec -i postgres-ekc psql -U postgres -d enterprise_copilot

python scripts/migrate_v5.py

Get-Content ingestion/migration_v6.sql | docker exec -i postgres-ekc psql -U postgres -d enterprise_copilot

python fix_password.py
```
You should see:
```
✅ Database schema initialized successfully
✅ v2 migration complete
📂 Repositories: Engineering, External, Finance, HR, IT, Projects
✅ v3 migration complete — knowledge_gaps, user_feedback, chunk_feedback_boosts created
```


---

## Step 6 — Pull LLM Model (Ollama)

Open a **new terminal** (keep the first one for later):

```bash
# Start Ollama service  without enviroment vaiables set
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" list
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull phi3:mini
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull qwen2.5:3b
```

Open **another new terminal** and pull the model:
```bash
# phi3:mini = 2.3GB, fast on CPU, good instruction following
ollama pull phi3:mini
ollama pull gemma3:4b
```

This takes 3–10 minutes depending on your internet speed.

---

## Step 7 — Start the Backend

In your first terminal (with .venv activated):

```bash
python -m uvicorn api.main:app --reload --port 8000
```

Wait for:
```
✅ Qdrant: ...
✅ BM25 index: 0 docs
✅ PostgreSQL: all v3 services connected
✅ Ollama: ready (phi3:mini)
🎉 Enterprise Knowledge Copilot v3 ready!
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Test it: open http://localhost:8000/status in your browser.  
You should see JSON with `"status": "running"`.

---

## Step 8 — Start the React UI

Open a **fourth terminal**:

```bash
cd ui-react
npm install
npm run dev
```

Open **http://localhost:3000** in your browser.

---

## Step 9 — Load Your Data

### Load tickets (the 2000+ IT tickets)

1. Open http://localhost:3000/admin
2. Click the **"Upload"** tab
3. Under **"Load Tickets CSV"**, click the upload area
4. Select your `tickets.csv` file
5. Wait ~30 seconds → you'll see "✓ 2001 tickets ingested"

### Load GitLab Handbook PDFs

For each PDF in your GitLab handbooks folder:
1. Under **"Upload Document"** in Admin
2. Select repository: `External` (it's the GitLab public handbook)
3. Origin: `EXTERNAL`
4. Click the upload area and select the PDF
5. Repeat for each handbook

Or use the bulk script (faster):
```bash
# From the backend terminal (with .venv active)
python scripts/bulk_ingest.py --folder /path/to/gitlab-handbooks --origin EXTERNAL --repository External

# Then ingest tickets
python scripts/bulk_ingest.py --tickets tickets.csv
```

---

## Using the Platform

### Chat Assistant (http://localhost:3000/chat)

Ask any question. Every answer shows:
- **Pipeline Trace** — all 9 query understanding steps with their outputs
- **Confidence Score** — 3-signal weighted score
- **Sources** — which document chunks were used, with page numbers
- **Chunk Detail** — individual RRF scores, reranker scores, priority tiers
- **HyDE indicator** — shows if hypothetical document embedding was used
- **Decomposed indicator** — shows if query was split into sub-queries
- **Thumbs up/down** — feeds the learning loop

### Example queries to try:
```
How do I connect to the VPN?
What is the annual leave carry-forward policy?
Kubernetes pod is in CrashLoopBackOff state
How does GitLab handle diversity and inclusion?
```

---

## 14-Stage Retrieval Pipeline

Every single query goes through all of these:

```
User Query
   │
   ▼  Step 1  NORMALIZER        unicode NFC + whitespace collapse
   ▼  Step 2  PII SCRUBBER       emails/phones → <REDACTED>
   ▼  Step 3  INTENT DETECTION   hybrid: regex rules + keyword scoring
                                  → SEARCH | SUMMARIZE | TICKET_LOOKUP | ESCALATE
   ▼  Step 4  ENTITY EXTRACTION  departments, ticket IDs, tech terms, dates
   ▼  Step 5  REPOSITORY SELECT  keyword-weighted routing → HR/IT/Finance/etc
   ▼  Step 6  FILTER EXTRACTION  Qdrant payload filters (dept, doc_type)
   ▼  Step 7  HyDE               Ollama generates hypothetical document passage
                                  triggers when: query < 5 tokens OR first score < 0.55
   ▼  Step 8  QUERY EXPANSION    2-5 semantic variants from synonym dictionary
   ▼  Step 9  DECOMPOSITION      compound queries → 2-3 atomic sub-queries
   │
   ▼  DENSE RETRIEVAL      BGE-small content vectors (top 20, per sub-query)
   ▼  QUESTION RETRIEVAL   BGE-small HyDE question vectors (top 20)
   ▼  BM25 RETRIEVAL        keyword sparse search (top 20 + expanded queries)
   │
   ▼  RRF FUSION           Reciprocal Rank Fusion k=60
                            INTERNAL Tier-1 × 1.30 boost
                            INTERNAL Tier-2 × 1.15 boost
                            EXTERNAL Tier-3 × 1.00 (neutral)
                            EXTERNAL Tier-4 × 0.85 penalty
   │
   ▼  CROSS ENCODER        ms-marco-MiniLM-L-6-v2, up to 44 pairs
                            sigmoid-normalised → [0, 1]
                            threshold: drop score < 0.30
   │
   ▼  FINAL SCORE          = reranker + priority_additive + freshness_decay + feedback_boost
   │
   ▼  CONTEXT BUILDER      max 8 chunks, max 3/doc, 4096 token budget
   │
   ▼  LLM (phi3:mini)       grounded answer with [Source N] citations
```

---

## Architecture Overview

```
ui-react/          React + Vite + Tailwind enterprise portal
  pages/
    Dashboard      KPI cards, daily query chart, repo cards, recent queries
    Repositories   Browse documents by business domain
    Documents      Full doc list with RBAC roles, chunk counts, filters
    Tickets        Search + known issue detection + resolution mining
    Chat           9-step pipeline chat with full transparency
    Admin          Upload, evaluation (P@5/R@5/MRR), knowledge gaps, feedback

api/
  main.py          FastAPI — all endpoints
  llm_service.py   Context builder + Ollama generation
  repository_service.py   Repository routing + query expansion
  confidence_service.py   3-signal weighted confidence score
  ticket_intelligence.py  Known issue detection, resolution mining
  analytics_service.py    Query logging, dashboard metrics
  feedback_service.py     Learning loop — chunk boost signals
  
retrieval/
  query_understanding.py  Full 9-step pipeline
  hyde_service.py          Async HyDE passage generation
  hybrid_engine.py         Dense + Question + BM25 → RRF → CrossEncoder + Context Builder
  rag_evaluator.py         Precision@5, Recall@5, MRR, Hit Rate, knowledge gaps
  vector_store.py          Qdrant dual-vector (content + question)
  bm25_store.py            In-memory BM25 with Redis persistence
  embedder.py              BGE-small-en-v1.5 singleton

ingestion/
  idp_pipeline.py          L1/L2 classification, smart chunking, chunk enrichment
  db_schema.sql            Phase 1 schema
  migration_v2.sql         Repositories, RBAC, analytics
  migration_v3.sql         Knowledge gaps, feedback, chunk boosts
```

---

## Hardware Adaptation (Intel i7-1065G7 · 8GB RAM)

| Component | LLD Spec | This Build | Reason |
|---|---|---|---|
| Embeddings | BGE-large (1.3GB, 1024d) | BGE-small (134MB, 384d) | RAM constraint |
| LLM | Mistral-7B (4.1GB) | phi3:mini (2.3GB) | RAM constraint |
| Reranker | ms-marco-MiniLM | Same ✅ | Already tiny |
| GPU inference | NVIDIA T4 | CPU-only | No CUDA GPU |

All retrieval logic, ranking algorithms, and pipeline steps are identical to the LLD. Only model sizes differ.

**Memory usage at runtime:**
- BGE-small model: ~300MB
- CrossEncoder reranker: ~120MB
- phi3:mini via Ollama: ~2.5GB
- Qdrant + PostgreSQL + Redis: ~600MB
- Total: ~3.5GB → fits in 8GB RAM

---

## Troubleshooting

**"Cannot connect to backend"**  
→ Make sure `uvicorn api.main:app --port 8000` is running in a terminal with `.venv` activated

**"Ollama not ready"**  
→ Run `ollama serve` in a separate terminal, then `ollama pull phi3:mini`

**Docker containers not starting**  
→ Make sure Docker Desktop is running (check system tray)
→ Run `docker ps` to see what's running
→ If port conflict: `docker rm -f qdrant-ekc postgres-ekc redis-ekc` then re-run Step 2

**"ModuleNotFoundError"**  
→ Make sure you activated the venv: `.venv\Scripts\activate` (Windows)

**Slow responses (15-30s)**  
→ Normal for phi3:mini on CPU. The retrieval pipeline is fast (<1s); the LLM generation takes 10-20s.
→ Close Chrome tabs and other apps to free RAM

**BM25 index empty after restart**  
→ BM25 is rebuilt from Redis on startup. If Redis is running, it auto-loads.
→ If BM25 is empty, re-ingest your documents — takes ~1 min.

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/chat` | POST | Full pipeline chat (9-step QU + hybrid retrieval) |
| `/ingest/file` | POST | Upload PDF/DOCX/PPTX |
| `/ingest/tickets` | POST | Upload tickets CSV |
| `/repositories` | GET | List all 6 repositories with stats |
| `/documents` | GET | List all documents |
| `/tickets/search` | GET | Search tickets + known issue detection |
| `/analytics/dashboard` | GET | Dashboard KPIs |
| `/analytics/evaluation` | GET | Precision@5, Recall@5, MRR, Hit Rate |
| `/feedback` | POST | Submit thumbs up/down |
| `/knowledge-gaps` | GET | Unresolved knowledge gaps |
| `/status` | GET | System health |

Interactive API docs: http://localhost:8000/docs

---

## Restart After Reboot

Every time you restart your PC:

```bash
# 1. Start Docker Desktop (system tray)

# 2. Start Ollama (terminal 1)
ollama serve

# 3. Start backend (terminal 2, in project folder)
.venv\Scripts\activate
python -m uvicorn api.main:app --reload --port 8000

# 4. Start React UI (terminal 3)
cd ui-react
npm run dev
```

Open http://localhost:3000
