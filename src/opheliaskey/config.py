"""Configuration, loaded from environment / .env with an OKEY_ prefix."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OKEY_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: Path = PROJECT_ROOT / "data" / "opheliaskey.db"
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    log_level: str = "INFO"

    # Gmail
    gmail_client_secret_file: Path = PROJECT_ROOT / "secrets" / "gmail_client_secret.json"
    gmail_token_file: Path = PROJECT_ROOT / "secrets" / "gmail_token.json"
    gmail_since: str = "2024-01-01"

    # Amazon Business (Login with Amazon)
    amazon_client_id: str = ""
    amazon_client_secret: str = ""
    amazon_refresh_token: str = ""
    amazon_region: str = "na"
    amazon_csv_dir: Path = PROJECT_ROOT / "data" / "imports" / "amazon"

    # Plaid
    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"
    plaid_access_token: str = ""

    @property
    def amazon_configured(self) -> bool:
        return bool(self.amazon_client_id and self.amazon_client_secret and self.amazon_refresh_token)

    @property
    def plaid_configured(self) -> bool:
        return bool(self.plaid_client_id and self.plaid_secret)

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.amazon_csv_dir.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
