"""
configs/settings.py  —  Central configuration via pydantic-settings
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="configs/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "enterprise_copilot"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "enterprise_knowledge"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # LLM
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "phi3:mini"
    # P6: Level-2 LLM intent classifier model. qwen2.5:3b preferred (better
    # instruction following for classification); falls back to phi3:mini.
    intent_classifier_model: str = "qwen2.5:3b"

    # ── P9: Image-aware retrieval ───────────────────────────────────────────
    # Vision-capable Ollama model used to caption extracted images/diagrams.
    vision_model: str = "gemma3:4b"
    # Where extracted images are saved on disk (referenced by Chunk.image_path).
    image_storage_dir: str = "/tmp/ekc_images"
    # Skip tiny embedded images (icons, bullets, logos) below this byte size.
    min_image_bytes: int = 3000

    # ── Persistent uploaded-file storage ────────────────────────────────────
    # Originally-uploaded PDF/DOCX/PPTX files are copied here (named
    # "<doc_id><ext>") after ingestion, so the Document Detail page can offer
    # an "Open file" / download of the real source document — previously the
    # temp upload was deleted once chunking finished.
    uploaded_files_dir: str = "data/uploads"

    # Embeddings
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384  # bge-small; change to 1024 for bge-large
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    secret_key: str = "change_me_32_chars_minimum_please"
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 8

    # Retrieval
    dense_top_k: int = 20
    bm25_top_k: int = 20
    reranker_top_k: int = 8
    reranker_threshold: float = 0.30
    low_confidence_threshold: float = 0.35
    rrf_k: int = 60

    # ── Dual-pass retrieval (LLD §4.1-4.3) ─────────────────────────────────
    internal_pass_top_k: int = 15
    internal_pass_min_cosine: float = 0.70
    internal_pass_min_results: int = 3   # below this -> full-corpus pass

    # ── Final score threshold (LLD §6.3) ───────────────────────────────────
    final_score_threshold: float = 0.38

    # ── Low confidence retry (LLD §7.1) ────────────────────────────────────
    retry_top_k: int = 25
    min_chunks_after_retry: int = 3

    # ── Staleness (LLD §8.3) ────────────────────────────────────────────────
    stale_days_threshold: int = 180
    internal_boost: float = 1.30

    # ── P7: PII deterministic hashing salt ──────────────────────────────────
    # Used to derive stable hash tokens (e.g. EMAIL_HASH_73AD21) for true PII
    # entities. Same value -> same hash; different values -> different hashes.
    # Override via env (PII_HASH_SALT) per-environment; keep stable within an
    # environment so re-ingestion produces consistent hashes for correlation.
    pii_hash_salt: str = "ekc-default-salt-v1"

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
