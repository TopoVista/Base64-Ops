from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="development", alias="ENVIRONMENT")
    port: int = Field(default=8000, alias="PORT")
    base_url: str = Field(default="http://localhost:8000", alias="BASE_URL")
    frontend_origin: str = Field(default="http://localhost:5173", alias="FRONTEND_ORIGIN")

    mongo_uri: str = Field(default="", alias="MONGO_URI")
    mongo_db_name: str = Field(default="langgraph_devops_agent", alias="MONGO_DB_NAME")
    jwt_secret: str = Field(default="dev-only-change-me", alias="JWT_SECRET")
    jwt_expires_minutes: int = Field(default=60 * 24 * 7, alias="JWT_EXPIRES_MINUTES")

    github_client_id: str = Field(default="", alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="GITHUB_CLIENT_SECRET")
    github_oauth_state_secret: str = Field(
        default="dev-only-github-state", alias="GITHUB_OAUTH_STATE_SECRET"
    )
    github_token_encryption_key: str = Field(
        default="dev-only-token-key", alias="GITHUB_TOKEN_ENCRYPTION_KEY"
    )

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    chat_model: str = Field(default="gpt-5.4-mini", alias="CHAT_MODEL")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    vector_index_name: str = Field(default="devops_rag_index", alias="VECTOR_INDEX_NAME")

    workspace_root: Path = Field(default=Path(".workspaces"), alias="WORKSPACE_ROOT")
    use_docker_workspaces: bool = Field(default=False, alias="USE_DOCKER_WORKSPACES")
    workspace_docker_image: str = Field(default="python:3.13-slim", alias="WORKSPACE_DOCKER_IMAGE")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
