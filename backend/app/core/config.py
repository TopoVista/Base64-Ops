from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="development", alias="ENVIRONMENT")
    port: int = Field(default=8001, alias="PORT")
    base_url: str = Field(default="http://localhost:8001", alias="BASE_URL")
    frontend_origin: str = Field(default="http://localhost:5173", alias="FRONTEND_ORIGIN")

    # MongoDB
    mongo_uri: str = Field(default="", alias="MONGO_URI")
    mongo_db_name: str = Field(default="langgraph_devops_agent", alias="MONGO_DB_NAME")
    mongo_connect_max_retries: int = Field(default=2, alias="MONGO_CONNECT_MAX_RETRIES")
    mongo_connect_timeout_ms: int = Field(default=5_000, alias="MONGO_CONNECT_TIMEOUT_MS")
    mongo_reconnect_interval_seconds: int = Field(default=15, alias="MONGO_RECONNECT_INTERVAL_SECONDS")

    # Clerk
    clerk_secret_key: str = Field(default="", alias="CLERK_SECRET_KEY")
    clerk_jwks_url: str = Field(default="", alias="CLERK_JWKS_URL")

    # GitHub OAuth
    github_client_id: str = Field(default="", alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="GITHUB_CLIENT_SECRET")
    github_oauth_state_secret: str = Field(default="dev-only-github-state", alias="GITHUB_OAUTH_STATE_SECRET")
    github_token_encryption_key: str = Field(default="dev-only-token-key", alias="GITHUB_TOKEN_ENCRYPTION_KEY")

    # GitHub Actions read-only investigation limits.
    ci_log_max_download_bytes: int = Field(default=500_000, alias="CI_LOG_MAX_DOWNLOAD_BYTES")
    ci_log_max_excerpt_bytes: int = Field(default=8_000, alias="CI_LOG_MAX_EXCERPT_BYTES")
    ci_log_max_excerpts_per_job: int = Field(default=3, alias="CI_LOG_MAX_EXCERPTS_PER_JOB")
    ci_log_max_jobs_per_run: int = Field(default=20, alias="CI_LOG_MAX_JOBS_PER_RUN")

    # LLM
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    chat_model: str = Field(default="gpt-4o-mini", alias="CHAT_MODEL")
    support_model: str = Field(default="gpt-5.6-sol", alias="SUPPORT_MODEL")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    vector_index_name: str = Field(default="devops_rag_index", alias="VECTOR_INDEX_NAME")
    # Lexical retrieval is the reliable baseline. Atlas vector search is an
    # optional acceleration because compatible Mongo deployments may not offer
    # the $vectorSearch/search-index commands.
    rag_vector_enabled: bool = Field(default=False, alias="RAG_VECTOR_ENABLED")

    # Workspace & Patch Limits
    workspace_root: Path = Field(default=Path(".workspaces"), alias="WORKSPACE_ROOT")
    use_docker_workspaces: bool = Field(default=False, alias="USE_DOCKER_WORKSPACES")
    workspace_docker_image: str = Field(default="python:3.13-slim", alias="WORKSPACE_DOCKER_IMAGE")
    max_patch_files: int = Field(default=8, alias="MAX_PATCH_FILES")
    max_patch_lines: int = Field(default=500, alias="MAX_PATCH_LINES")
    max_patch_file_bytes: int = Field(default=500_000, alias="MAX_PATCH_FILE_BYTES")
    max_patch_content_bytes: int = Field(default=250_000, alias="MAX_PATCH_CONTENT_BYTES")

    # Execution Sandbox Settings
    # "disabled" is the production-safe default for hosts that cannot provide
    # an isolated Docker runtime. It keeps static checks available but never
    # falls back to executing an untrusted repository on the API process.
    execution_mode: str = Field(default="docker-sandbox", alias="EXECUTION_MODE")
    validation_timeout_seconds: int = Field(default=120, alias="VALIDATION_TIMEOUT_SECONDS")
    validation_memory_mb: int = Field(default=1024, alias="VALIDATION_MEMORY_MB")
    validation_max_output_bytes: int = Field(default=100_000, alias="VALIDATION_MAX_OUTPUT_BYTES")

    # Live Evaluation Framework Settings
    enable_live_evals: bool = Field(default=False, alias="BASE64_ENABLE_LIVE_EVALS")
    live_test_repository: str = Field(default="", alias="BASE64_LIVE_TEST_REPOSITORY")
    live_eval_allowed_repos: str = Field(default="", alias="BASE64_LIVE_EVAL_ALLOWED_REPOS")

    # Operational Memory Settings
    memory_enabled: bool = Field(default=True, alias="MEMORY_ENABLED")
    memory_learning_enabled: bool = Field(default=True, alias="MEMORY_LEARNING_ENABLED")
    memory_max_context_entries: int = Field(default=5, alias="MEMORY_MAX_CONTEXT_ENTRIES")
    memory_stale_ttl_days: int = Field(default=90, alias="MEMORY_STALE_TTL_DAYS")
    memory_min_confidence: float = Field(default=0.6, alias="MEMORY_MIN_CONFIDENCE")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
