from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_fernet_key(value: str) -> bool:
    """Return whether ``value`` is a URL-safe base64-encoded 256-bit Fernet key."""

    try:
        Fernet(value.encode("ascii"))
    except (AttributeError, UnicodeEncodeError, ValueError):
        return False
    return True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    environment: str = Field(default="development", alias="ENVIRONMENT")
    # The public API has the broadest configuration requirements. Internal
    # workers are deliberately least-privileged and validate only the secrets
    # required for their narrow server-created job types.
    service_role: str = Field(default="api", alias="SERVICE_ROLE")
    port: int = Field(default=8001, alias="PORT")
    base_url: str = Field(default="http://localhost:8001", alias="BASE_URL")
    frontend_origin: str = Field(default="http://localhost:5173", alias="FRONTEND_ORIGIN")
    # Comma-separated public host names. Keep this explicit in production so
    # Host-header attacks cannot reach an otherwise valid API deployment.
    trusted_hosts: str = Field(default="", alias="TRUSTED_HOSTS")

    # Edge/CDN protection should be enabled in front of the API as well. This
    # application-level limiter is a second line of defence for authenticated
    # and direct-origin traffic. Configure REDIS_URL for a shared limit across
    # Render instances; the in-process store is only a development fallback.
    redis_url: str = Field(default="", alias="REDIS_URL")
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_requests_per_minute: int = Field(default=120, alias="RATE_LIMIT_REQUESTS_PER_MINUTE")
    rate_limit_expensive_requests_per_minute: int = Field(
        default=8, alias="RATE_LIMIT_EXPENSIVE_REQUESTS_PER_MINUTE"
    )
    rate_limit_fail_closed: bool = Field(default=False, alias="RATE_LIMIT_FAIL_CLOSED")
    # Never infer a browser address from X-Forwarded-For: intermediaries can
    # append to it and callers may spoof a leading value. Configure only an
    # edge-overwritten, single-IP header in production.
    trusted_client_ip_header: str = Field(default="", alias="TRUSTED_CLIENT_IP_HEADER")
    # Only used by the development in-process limiter. Production requires
    # Redis, but this keeps local testing safe against unbounded unique keys.
    rate_limit_local_max_keys: int = Field(default=10_000, ge=100, le=100_000, alias="RATE_LIMIT_LOCAL_MAX_KEYS")
    max_request_body_bytes: int = Field(default=1_500_000, alias="MAX_REQUEST_BODY_BYTES")
    max_concurrent_agent_runs_per_tenant: int = Field(
        default=1, ge=1, le=1, alias="MAX_CONCURRENT_AGENT_RUNS_PER_TENANT"
    )
    agent_run_lease_seconds: int = Field(default=900, alias="AGENT_RUN_LEASE_SECONDS")
    indexing_mode: str = Field(default="inline", alias="INDEXING_MODE")
    background_job_max_attempts: int = Field(default=2, alias="BACKGROUND_JOB_MAX_ATTEMPTS")
    # A claimed worker renews this lease while it works. Keep recovery bounded
    # after a crash or forced deploy; one hour would strand queued work far too
    # long during an autoscaling event.
    background_job_lease_seconds: int = Field(default=900, alias="BACKGROUND_JOB_LEASE_SECONDS")
    ci_verification_poll_seconds: int = Field(default=60, alias="CI_VERIFICATION_POLL_SECONDS")
    ci_verification_max_polls: int = Field(default=20, alias="CI_VERIFICATION_MAX_POLLS")
    # ``on_read`` is the minimal-cost MVP mode: the Code workbench's bounded
    # commit-status refresh is the observer, so no always-on CI worker is
    # required. ``queued`` retains the autonomous observer for scaled setups.
    ci_verification_mode: str = Field(default="queued", alias="CI_VERIFICATION_MODE")
    # Session state is browser-polled. Keep Actions discovery opt-in so a
    # passive page view never creates external GitHub/workspace work. Manual
    # refresh and the bounded post-delivery verifier remain available.
    ci_auto_refresh_seconds: int = Field(
        default=0, ge=0, le=3_600, alias="CI_AUTO_REFRESH_SECONDS"
    )

    # Tenant quotas are enforced server-side before a token-consuming graph
    # run begins. Payment webhooks may change a tenant tier, but browsers never
    # submit a quota or tier that the API trusts.
    free_monthly_agent_runs: int = Field(default=25, alias="FREE_MONTHLY_AGENT_RUNS")
    pro_monthly_agent_runs: int = Field(default=500, alias="PRO_MONTHLY_AGENT_RUNS")
    team_monthly_agent_runs: int = Field(default=2_000, alias="TEAM_MONTHLY_AGENT_RUNS")
    # Token ceilings are operational safety limits, not prices. Provider
    # usage is recorded after each bounded call and the next model run is
    # refused once a tenant reaches its configured monthly allowance.
    free_monthly_model_tokens: int = Field(default=250_000, alias="FREE_MONTHLY_MODEL_TOKENS")
    pro_monthly_model_tokens: int = Field(default=5_000_000, alias="PRO_MONTHLY_MODEL_TOKENS")
    team_monthly_model_tokens: int = Field(default=25_000_000, alias="TEAM_MONTHLY_MODEL_TOKENS")
    agent_run_idempotency_ttl_seconds: int = Field(
        default=60 * 60 * 24 * 7, alias="AGENT_RUN_IDEMPOTENCY_TTL_SECONDS"
    )

    # Razorpay remains entirely server-side. Plan identifiers are created in
    # the merchant dashboard; a browser may request only a known tier, never a
    # price, provider plan id, or entitlement.
    razorpay_key_id: str = Field(default="", alias="RAZORPAY_KEY_ID")
    razorpay_key_secret: str = Field(default="", alias="RAZORPAY_KEY_SECRET")
    razorpay_webhook_secret: str = Field(default="", alias="RAZORPAY_WEBHOOK_SECRET")
    razorpay_pro_plan_id: str = Field(default="", alias="RAZORPAY_PRO_PLAN_ID")
    razorpay_team_plan_id: str = Field(default="", alias="RAZORPAY_TEAM_PLAN_ID")

    # MongoDB
    mongo_uri: str = Field(default="", alias="MONGO_URI")
    mongo_db_name: str = Field(default="langgraph_devops_agent", alias="MONGO_DB_NAME")
    mongo_connect_max_retries: int = Field(default=2, alias="MONGO_CONNECT_MAX_RETRIES")
    mongo_connect_timeout_ms: int = Field(default=5_000, alias="MONGO_CONNECT_TIMEOUT_MS")
    # Keep a 512 MB replica from opening an unbounded number of Atlas sockets.
    # Scale by adding stateless replicas only after sizing this shared budget.
    mongo_max_pool_size: int = Field(default=20, ge=1, le=100, alias="MONGO_MAX_POOL_SIZE")
    mongo_wait_queue_timeout_ms: int = Field(
        default=5_000, ge=100, le=60_000, alias="MONGO_WAIT_QUEUE_TIMEOUT_MS"
    )
    mongo_reconnect_interval_seconds: int = Field(default=15, alias="MONGO_RECONNECT_INTERVAL_SECONDS")

    # Clerk
    clerk_secret_key: str = Field(default="", alias="CLERK_SECRET_KEY")
    clerk_jwks_url: str = Field(default="", alias="CLERK_JWKS_URL")
    # Clerk session tokens carry an issuer matching the Frontend API URL and,
    # when present, an authorized-party (azp) origin. Bind both in production
    # so a signature alone is not treated as sufficient tenant authentication.
    clerk_issuer: str = Field(default="", alias="CLERK_ISSUER")
    clerk_authorized_parties: str = Field(default="", alias="CLERK_AUTHORIZED_PARTIES")

    # GitHub OAuth
    github_client_id: str = Field(default="", alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="GITHUB_CLIENT_SECRET")
    github_oauth_state_secret: str = Field(default="dev-only-github-state", alias="GITHUB_OAUTH_STATE_SECRET")
    github_token_encryption_key: str = Field(default="dev-only-token-key", alias="GITHUB_TOKEN_ENCRYPTION_KEY")
    # One-time migration aid for deployments that encrypted GitHub tokens with
    # the pre-Fernet passphrase derivation. It is read only as a decrypt
    # fallback; all new OAuth tokens use GITHUB_TOKEN_ENCRYPTION_KEY.
    github_token_encryption_legacy_key: str = Field(default="", alias="GITHUB_TOKEN_ENCRYPTION_LEGACY_KEY")
    github_oauth_state_ttl_seconds: int = Field(
        default=600, ge=60, le=3_600, alias="GITHUB_OAUTH_STATE_TTL_SECONDS"
    )
    # GitHub repository lists are metadata only and are cached briefly per
    # tenant. Authorization is still verified live before a repository is
    # cloned or attached to a session.
    github_repository_cache_ttl_seconds: int = Field(
        default=60, ge=0, le=600, alias="GITHUB_REPOSITORY_CACHE_TTL_SECONDS"
    )

    # GitHub Actions read-only investigation limits.
    ci_log_max_download_bytes: int = Field(default=500_000, alias="CI_LOG_MAX_DOWNLOAD_BYTES")
    ci_log_max_excerpt_bytes: int = Field(default=8_000, alias="CI_LOG_MAX_EXCERPT_BYTES")
    ci_log_max_excerpts_per_job: int = Field(default=3, alias="CI_LOG_MAX_EXCERPTS_PER_JOB")
    ci_log_max_jobs_per_run: int = Field(default=20, alias="CI_LOG_MAX_JOBS_PER_RUN")

    # LLM
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    chat_model: str = Field(default="gpt-4o-mini", alias="CHAT_MODEL")
    # Product-guide traffic is high volume and constrained to documented
    # support. Keep it on the economical model; evidence-backed repository
    # diagnosis separately uses CHAT_MODEL.
    support_model: str = Field(default="gpt-5.6-luna", alias="SUPPORT_MODEL")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    vector_index_name: str = Field(default="devops_rag_index", alias="VECTOR_INDEX_NAME")
    # Lexical retrieval is the reliable baseline. Atlas vector search is an
    # optional acceleration because compatible Mongo deployments may not offer
    # the $vectorSearch/search-index commands.
    rag_vector_enabled: bool = Field(default=False, alias="RAG_VECTOR_ENABLED")
    rag_retrieval_limit: int = Field(default=6, alias="RAG_RETRIEVAL_LIMIT")
    # Indexing operates in a 512 MB worker. Bound the aggregate repository
    # corpus as well as each individual file so a legitimate large repository
    # cannot turn one background job into an unbounded memory/Atlas write.
    rag_index_max_files: int = Field(default=300, ge=1, le=500, alias="RAG_INDEX_MAX_FILES")
    rag_index_max_total_bytes: int = Field(
        default=12_000_000, ge=100_000, le=50_000_000, alias="RAG_INDEX_MAX_TOTAL_BYTES"
    )
    rag_index_max_chunks: int = Field(default=8_000, ge=100, le=20_000, alias="RAG_INDEX_MAX_CHUNKS")
    # Bounded model context is a correctness control as well as a cost
    # control: evidence selected for the UI stays persisted, but only the most
    # relevant bounded excerpts are admitted to an LLM prompt.
    llm_max_context_chars: int = Field(default=24_000, alias="LLM_MAX_CONTEXT_CHARS")
    llm_max_source_chars: int = Field(default=4_000, alias="LLM_MAX_SOURCE_CHARS")
    llm_max_patch_candidate_chars: int = Field(default=16_000, alias="LLM_MAX_PATCH_CANDIDATE_CHARS")
    llm_max_output_tokens: int = Field(default=1_200, alias="LLM_MAX_OUTPUT_TOKENS")
    support_max_output_tokens: int = Field(default=500, alias="SUPPORT_MAX_OUTPUT_TOKENS")
    # A model call is an externally metered dependency. These hard limits stop
    # a stalled provider or repeated transient retry from holding an agent
    # lease indefinitely or spending unbounded tokens.
    llm_timeout_seconds: int = Field(default=45, ge=5, le=120, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=1, ge=0, le=2, alias="LLM_MAX_RETRIES")

    # Workspace & Patch Limits
    workspace_root: Path = Field(default=Path(".workspaces"), alias="WORKSPACE_ROOT")
    use_docker_workspaces: bool = Field(default=False, alias="USE_DOCKER_WORKSPACES")
    workspace_docker_image: str = Field(default="python:3.13.5-slim", alias="WORKSPACE_DOCKER_IMAGE")
    max_patch_files: int = Field(default=8, alias="MAX_PATCH_FILES")
    max_patch_lines: int = Field(default=500, alias="MAX_PATCH_LINES")
    # Automatic CI remediation is the flagship, one-click workflow. Its
    # scope is deliberately much narrower than a user-authored code proposal:
    # a broadly plausible refactor is not an acceptable "minimal repair".
    ci_repair_max_files: int = Field(default=2, ge=1, le=4, alias="CI_REPAIR_MAX_FILES")
    ci_repair_max_lines: int = Field(default=80, ge=1, le=250, alias="CI_REPAIR_MAX_LINES")
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

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """Fail closed for deployment mistakes that would weaken isolation."""

        if not self.is_production:
            return self
        role = self.service_role.strip().lower()
        if role == "maintenance":
            if not self.mongo_uri:
                raise ValueError("MONGO_URI must be configured for production maintenance workers")
            return self
        if role in {"indexer", "ci_observer"}:
            if not self.mongo_uri:
                raise ValueError("MONGO_URI must be configured for production workers")
            if not _is_fernet_key(self.github_token_encryption_key):
                raise ValueError(
                    "GITHUB_TOKEN_ENCRYPTION_KEY must be a URL-safe base64-encoded "
                    "32-byte Fernet key in production workers"
                )
            if self.github_token_encryption_legacy_key and len(
                self.github_token_encryption_legacy_key.encode("utf-8")
            ) < 32:
                raise ValueError("GITHUB_TOKEN_ENCRYPTION_LEGACY_KEY must be at least 32 bytes when configured")
            return self
        if role != "api":
            raise ValueError("SERVICE_ROLE must be api, maintenance, indexer, or ci_observer")
        if self.indexing_mode.lower() not in {"inline", "queued"}:
            raise ValueError("INDEXING_MODE must be inline or queued")
        if self.ci_verification_mode.lower() not in {"on_read", "queued"}:
            raise ValueError("CI_VERIFICATION_MODE must be on_read or queued")
        if not self.trusted_hosts.strip():
            raise ValueError("TRUSTED_HOSTS must be configured in production")
        if not self.redis_url:
            raise ValueError("REDIS_URL must be configured in production for shared rate limits")
        if not self.rate_limit_fail_closed:
            raise ValueError("RATE_LIMIT_FAIL_CLOSED must be enabled in production")
        if not self.trusted_client_ip_header.strip():
            raise ValueError("TRUSTED_CLIENT_IP_HEADER must be configured in production")
        if self.trusted_client_ip_header.strip().lower() == "x-forwarded-for":
            raise ValueError("TRUSTED_CLIENT_IP_HEADER must not use X-Forwarded-For")
        if not self.frontend_origin.startswith("https://"):
            raise ValueError("FRONTEND_ORIGIN must use HTTPS in production")
        if not self.base_url.startswith("https://"):
            raise ValueError("BASE_URL must use HTTPS in production")
        if urlparse(self.base_url).hostname in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("BASE_URL must not target a loopback host in production")
        if not self.mongo_uri:
            raise ValueError("MONGO_URI must be configured in production")
        if not self.clerk_jwks_url.startswith("https://"):
            raise ValueError("CLERK_JWKS_URL must use HTTPS in production")
        if not self.clerk_issuer.startswith("https://"):
            raise ValueError("CLERK_ISSUER must use HTTPS in production")
        if not self.github_client_id or not self.github_client_secret:
            raise ValueError("GitHub OAuth credentials must be configured in production")
        if self.github_oauth_state_secret in {"", "dev-only-github-state"} or len(
            self.github_oauth_state_secret.encode("utf-8")
        ) < 32:
            raise ValueError("GITHUB_OAUTH_STATE_SECRET must contain at least 32 bytes in production")
        if not _is_fernet_key(self.github_token_encryption_key):
            raise ValueError(
                "GITHUB_TOKEN_ENCRYPTION_KEY must be a URL-safe base64-encoded 32-byte Fernet key in production"
            )
        if self.github_token_encryption_legacy_key and len(
            self.github_token_encryption_legacy_key.encode("utf-8")
        ) < 32:
            raise ValueError("GITHUB_TOKEN_ENCRYPTION_LEGACY_KEY must be at least 32 bytes when configured")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
