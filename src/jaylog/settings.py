from pathlib import Path
from typing import Optional

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jaylog.formatters import _HOSTNAME, _HOST_USERNAME


class JaylogSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JAYLOG_",
        env_file=('.env.logging', '.env'),
        env_file_encoding='utf-8',
        secrets_dir='secrets',
        extra="ignore"
    )

    # App identity
    app_name: str
    log_dir: Path

    secrets_dir: Path | None = None

    # File handler
    log_level: str = "INFO"
    log_max_bytes: int = 5 * 1024 * 1024  # 5 MB
    log_backup_count: int = 5
    log_retention_days: int = 7

    # HTTP handler
    log_http_endpoint: Optional[str] = None
    log_http_api_key: Optional[str] = None
    log_http_timeout: float = 5.0
    log_http_proxy: Optional[str] = None

    # Screenshot (log_img field) — desativar com JAYLOG_LOG_SCREENSHOT_ENABLED=false
    log_screenshot_enabled: bool = False

    @field_validator("log_dir", mode="after")
    @classmethod
    def validate_log_dir(cls, v: Path) -> Path:
        if v.exists() and not v.is_dir():
            raise ValueError(f"JAYLOG_LOG_DIR '{v}' exists but is not a directory")
        return v

    @computed_field
    @property
    def log_filename(self) -> Path:
        return Path(f"{self.app_name}_{_HOSTNAME}_{_HOST_USERNAME}.log")

    def reload_secrets(self):
        if self.secrets_dir:
            if not self.secrets_dir.exists() or not self.secrets_dir.is_dir:
                raise ValueError('SECRETS_DIR is not valid directory')
                
        return JaylogSettings(
            _env_file=self.model_config.get('env_file'),  # ty:ignore[unresolved-attribute, unknown-argument]
            _secrets_dir=self.secrets_dir)  # ty:ignore[missing-argument, unknown-argument]