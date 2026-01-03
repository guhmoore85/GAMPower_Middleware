"""
Configuration Management for PAYGO Middleware

CRITICAL: This module validates ALL environment variables at startup.
If any required variable is missing or invalid, the application will
FAIL LOUDLY with a clear error message.

Uses pydantic-settings for type-safe configuration with validation.
"""

import secrets
from functools import lru_cache
from typing import Any, Literal, Optional

from pydantic import (
    Field,
    PostgresDsn,
    RedisDsn,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings with comprehensive validation.

    LOUD FAILURE: All required settings are validated at startup.
    Missing or invalid settings will cause immediate failure with
    clear error messages.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # =========================================================================
    # Environment
    # =========================================================================
    environment: Literal["development", "staging", "production"] = Field(
        default="development",
        description="Deployment environment",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode (detailed errors, verbose logging)",
    )

    # =========================================================================
    # Server
    # =========================================================================
    host: str = Field(
        default="0.0.0.0",
        description="Server host",
    )
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Server port",
    )

    # =========================================================================
    # Database
    # =========================================================================
    database_url: PostgresDsn = Field(
        ...,  # Required - no default
        description="PostgreSQL connection URL",
    )
    db_pool_size: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Database connection pool size",
    )
    db_max_overflow: int = Field(
        default=20,
        ge=0,
        le=100,
        description="Maximum overflow connections",
    )
    db_pool_timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Pool connection timeout in seconds",
    )
    db_pool_recycle: int = Field(
        default=1800,
        ge=60,
        le=7200,
        description="Recycle connections after this many seconds",
    )

    # =========================================================================
    # Redis
    # =========================================================================
    redis_url: RedisDsn = Field(
        ...,  # Required - no default
        description="Redis connection URL",
    )
    cache_ttl: int = Field(
        default=300,
        ge=0,
        le=86400,
        description="Default cache TTL in seconds",
    )

    # =========================================================================
    # Security
    # =========================================================================
    secret_key: str = Field(
        ...,  # Required - no default
        min_length=32,
        description="Secret key for JWT and session signing (minimum 32 characters)",
    )
    encryption_key: str = Field(
        ...,  # Required - no default
        min_length=32,
        max_length=32,
        description="Encryption key for sensitive data (exactly 32 bytes)",
    )
    admin_api_key: str = Field(
        ...,  # Required - no default
        min_length=32,
        description="API key for admin endpoints",
    )
    jwt_expiration_minutes: int = Field(
        default=60,
        ge=5,
        le=1440,
        description="JWT token expiration in minutes",
    )
    cors_origins: list[str] = Field(
        default_factory=list,
        description="Allowed CORS origins",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        """Parse CORS origins from comma-separated string or list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v or []

    # =========================================================================
    # Payment Providers
    # =========================================================================
    # Wave
    wave_api_url: str = Field(
        default="https://api.wave.com/v1",
        description="Wave API base URL",
    )
    wave_api_key: str = Field(
        default="",
        description="Wave API key",
    )
    wave_webhook_secret: str = Field(
        default="",
        description="Wave webhook signing secret",
    )

    # QMoney
    qmoney_api_url: str = Field(
        default="https://api.qmoney.com/v1",
        description="QMoney API base URL",
    )
    qmoney_api_key: str = Field(
        default="",
        description="QMoney API key",
    )
    qmoney_webhook_secret: str = Field(
        default="",
        description="QMoney webhook signing secret",
    )

    # Apple Pay
    apple_pay_merchant_id: str = Field(
        default="",
        description="Apple Pay merchant ID",
    )
    apple_pay_certificate_path: Optional[str] = Field(
        default=None,
        description="Path to Apple Pay certificate",
    )
    apple_pay_private_key_path: Optional[str] = Field(
        default=None,
        description="Path to Apple Pay private key",
    )

    # =========================================================================
    # OpenPAYGO
    # =========================================================================
    openpaygo_default_token_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Default token validity in days",
    )
    openpaygo_max_token_days: int = Field(
        default=365,
        ge=1,
        le=3650,
        description="Maximum token validity in days",
    )
    openpaygo_token_version: int = Field(
        default=2,
        ge=1,
        le=3,
        description="OpenPAYGO token algorithm version",
    )

    # =========================================================================
    # Scheduler
    # =========================================================================
    scheduler_enabled: bool = Field(
        default=True,
        description="Enable background scheduler",
    )
    trigger_evaluation_interval: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Trigger evaluation interval in minutes",
    )

    # =========================================================================
    # Logging
    # =========================================================================
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level",
    )
    log_format: Literal["json", "pretty"] = Field(
        default="json",
        description="Log format",
    )
    log_file_path: Optional[str] = Field(
        default=None,
        description="Log file path (optional)",
    )
    log_rotation_size: str = Field(
        default="50MB",
        description="Log rotation size",
    )
    log_retention_count: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of log files to retain",
    )
    log_sql_queries: bool = Field(
        default=False,
        description="Enable SQL query logging",
    )

    # =========================================================================
    # Rate Limiting
    # =========================================================================
    rate_limit_requests_per_minute: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="API rate limit per minute",
    )
    webhook_rate_limit_requests_per_minute: int = Field(
        default=1000,
        ge=1,
        le=100000,
        description="Webhook rate limit per minute",
    )

    # =========================================================================
    # Health Checks
    # =========================================================================
    db_health_check_timeout: int = Field(
        default=5,
        ge=1,
        le=30,
        description="Database health check timeout in seconds",
    )
    redis_health_check_timeout: int = Field(
        default=5,
        ge=1,
        le=30,
        description="Redis health check timeout in seconds",
    )

    # =========================================================================
    # Metrics
    # =========================================================================
    metrics_enabled: bool = Field(
        default=True,
        description="Enable Prometheus metrics",
    )
    metrics_port: int = Field(
        default=9090,
        ge=1,
        le=65535,
        description="Metrics server port",
    )

    # =========================================================================
    # Validators
    # =========================================================================
    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        """
        Cross-field validation.

        LOUD FAILURE: Raises ValueError with clear message if validation fails.
        """
        errors = []

        # Validate security settings for production
        if self.environment == "production":
            if self.debug:
                errors.append("DEBUG must be False in production")

            if self.secret_key == "your-secret-key-here-change-in-production":
                errors.append("SECRET_KEY must be changed from default in production")

            if len(self.secret_key) < 64:
                errors.append("SECRET_KEY should be at least 64 characters in production")

            if not self.cors_origins:
                errors.append("CORS_ORIGINS must be explicitly set in production")

            if self.log_level == "DEBUG":
                errors.append("LOG_LEVEL should not be DEBUG in production")

        # Validate payment provider settings (warn if not set)
        if not self.wave_api_key:
            # This is a warning, not an error - mock provider will be used
            pass

        # Validate OpenPAYGO settings
        if self.openpaygo_max_token_days < self.openpaygo_default_token_days:
            errors.append(
                f"OPENPAYGO_MAX_TOKEN_DAYS ({self.openpaygo_max_token_days}) "
                f"must be >= OPENPAYGO_DEFAULT_TOKEN_DAYS ({self.openpaygo_default_token_days})"
            )

        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            raise ValueError(error_msg)

        return self

    # =========================================================================
    # Computed Properties
    # =========================================================================
    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.environment == "production"

    @property
    def database_url_sync(self) -> str:
        """Get synchronous database URL (for Alembic)."""
        url = str(self.database_url)
        return url.replace("+asyncpg", "")

    def get_jwt_secret(self) -> bytes:
        """Get JWT secret as bytes."""
        return self.secret_key.encode("utf-8")

    def get_encryption_key(self) -> bytes:
        """Get encryption key as bytes (32 bytes for AES-256)."""
        key = self.encryption_key.encode("utf-8")
        if len(key) != 32:
            raise ValueError(
                f"ENCRYPTION_KEY must be exactly 32 bytes, got {len(key)}. "
                "LOUD FAILURE: Cannot start application with invalid encryption key."
            )
        return key


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.

    LOUD FAILURE: If settings cannot be loaded, raises detailed error.
    """
    try:
        return Settings()
    except Exception as e:
        # Log to stderr since logging may not be configured yet
        import sys

        print(
            f"CRITICAL: Failed to load settings. Application cannot start.\n"
            f"Error: {e}\n"
            f"Please check your .env file and ensure all required variables are set.",
            file=sys.stderr,
        )
        raise


# Global settings instance
settings = get_settings()


def validate_startup_config() -> dict[str, Any]:
    """
    Validate configuration at startup.

    Returns a dict with validation results.
    LOUD FAILURE: Raises ConfigurationError if critical validation fails.

    Call this during application startup to ensure all config is valid.
    """
    from app.core.exceptions import ConfigurationError

    results = {
        "environment": settings.environment,
        "debug": settings.debug,
        "database_url_configured": bool(settings.database_url),
        "redis_url_configured": bool(settings.redis_url),
        "secret_key_configured": bool(settings.secret_key),
        "encryption_key_configured": bool(settings.encryption_key),
        "admin_api_key_configured": bool(settings.admin_api_key),
        "warnings": [],
    }

    # Check for mock payment providers
    if not settings.wave_api_key or settings.wave_api_key.startswith("mock"):
        results["warnings"].append("Wave API key not configured - using mock provider")

    if not settings.qmoney_api_key or settings.qmoney_api_key.startswith("mock"):
        results["warnings"].append("QMoney API key not configured - using mock provider")

    if not settings.apple_pay_merchant_id or settings.apple_pay_merchant_id.startswith("merchant.com.paygo.mock"):
        results["warnings"].append("Apple Pay not configured - using mock provider")

    # Critical validations
    if not settings.database_url:
        raise ConfigurationError("DATABASE_URL", "Database URL is required")

    if not settings.redis_url:
        raise ConfigurationError("REDIS_URL", "Redis URL is required")

    if not settings.secret_key or len(settings.secret_key) < 32:
        raise ConfigurationError("SECRET_KEY", "Secret key must be at least 32 characters")

    if not settings.encryption_key or len(settings.encryption_key) != 32:
        raise ConfigurationError(
            "ENCRYPTION_KEY", "Encryption key must be exactly 32 characters"
        )

    return results
