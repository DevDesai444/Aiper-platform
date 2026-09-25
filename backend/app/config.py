"""Single source of truth for runtime configuration."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_JWT_SECRET = "super_secret_jwt_key_change_me"
_DEFAULT_DB_PASSWORD = "aiper_password"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "aiper"

    # Development mode — set AIPER_DEV_MODE=1 to bypass startup secret checks.
    # Never enable this in production.
    aiper_dev_mode: bool = False

    # Azure OpenAI
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-02-15-preview"
    azure_openai_chat_deployment_name: str = "gpt-4o"
    azure_openai_embedding_deployment_name: str = "text-embedding-3-large"

    # Database
    database_url: str = "postgresql+asyncpg://aiper_user:aiper_password@postgres:5432/aiper_db"

    # Security
    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60 * 24 * 7

    # Vector store
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_collection: str = "aiper_pages"
    embedding_dim: int = 3072

    # Agent context window strategy
    agent_trim_tokens: int = 90_000
    agent_summary_trigger_tokens: int = 60_000
    agent_summary_keep_messages: int = 12
    agent_recursion_limit: int = 60

    # Storage
    storage_dir: str = "/data/uploads"
    # Skills are read from <skills_root>/skills/<name>/SKILL.md (copied into the image).
    skills_root: str = "/app"
    max_upload_mb: int = 40

    backend_cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    @property
    def azure_configured(self) -> bool:
        return bool(self.azure_openai_api_key and self.azure_openai_endpoint)

    def check_production_secrets(self) -> None:
        """Raise RuntimeError if unsafe defaults are present in production mode."""
        if self.aiper_dev_mode:
            return
        errors: list[str] = []
        if not self.jwt_secret or self.jwt_secret == _DEFAULT_JWT_SECRET:
            errors.append(
                "JWT_SECRET is empty or still the default placeholder. "
                "Set a strong random value via the JWT_SECRET environment variable."
            )
        if _DEFAULT_DB_PASSWORD in self.database_url:
            errors.append(
                "DATABASE_URL still contains the default password 'aiper_password'. "
                "Set a strong password via the DATABASE_URL environment variable."
            )
        if errors:
            bullet_list = "\n  - ".join(errors)
            raise RuntimeError(
                f"Refusing to start: unsafe default configuration detected.\n  - {bullet_list}\n"
                "Set AIPER_DEV_MODE=1 to bypass these checks during local development."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
