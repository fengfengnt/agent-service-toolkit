from ipaddress import ip_network
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
        env_file=None,
    )

    visitor_message_limit: int = Field(default=100, gt=0, validation_alias="VISITOR_MESSAGE_LIMIT")
    visitor_retention_seconds: int = Field(
        default=14400, gt=0, validation_alias="VISITOR_RETENTION_SECONDS"
    )
    toolkit_base_url: str = Field(validation_alias="TOOLKIT_BASE_URL")
    auth_secret: SecretStr = Field(validation_alias="AUTH_SECRET")
    sso_bridge_enabled: bool = Field(default=False, validation_alias="SSO_XIAOHE_BRIDGE_ENABLED")
    sso_bridge_base_url: str | None = Field(default=None, validation_alias="XIAOHE_SSO_BASE_URL")
    sso_bridge_application_id: str | None = Field(
        default=None, validation_alias="XIAOHE_SSO_APPLICATION_ID"
    )
    sso_bridge_secret: SecretStr | None = Field(
        default=None, validation_alias="XIAOHE_SSO_APPLICATION_SECRET"
    )
    sso_bridge_ca_file: str | None = Field(default=None, validation_alias="XIAOHE_SSO_CA_FILE")
    sso_bridge_redirect_uri: str | None = Field(
        default=None, validation_alias="XIAOHE_SSO_REDIRECT_URI"
    )
    database_url: SecretStr | None = Field(default=None, validation_alias="XIAOHE_DATABASE_URL")
    minio_endpoint: str | None = Field(default=None, validation_alias="XIAOHE_MINIO_ENDPOINT")
    minio_bucket: str | None = Field(default=None, validation_alias="XIAOHE_MINIO_BUCKET")
    minio_access_key: SecretStr | None = Field(
        default=None, validation_alias="XIAOHE_MINIO_ACCESS_KEY"
    )
    minio_secret_key: SecretStr | None = Field(
        default=None, validation_alias="XIAOHE_MINIO_SECRET_KEY"
    )
    reception_base_url: str | None = Field(
        default=None, validation_alias="XIAOHE_RECEPTION_BASE_URL"
    )
    reception_application_id: str | None = Field(
        default=None, validation_alias="XIAOHE_RECEPTION_APPLICATION_ID"
    )
    reception_secret: SecretStr | None = Field(
        default=None, validation_alias="XIAOHE_RECEPTION_APPLICATION_SECRET"
    )
    reception_file_application_id: str | None = Field(
        default=None, validation_alias="XIAOHE_RECEPTION_FILE_APPLICATION_ID"
    )
    reception_file_secret: SecretStr | None = Field(
        default=None, validation_alias="XIAOHE_RECEPTION_FILE_SECRET"
    )
    reception_file_system_id: str | None = Field(
        default=None, validation_alias="XIAOHE_RECEPTION_FILE_SYSTEM_ID"
    )
    trusted_proxy_cidrs: list[str] = Field(
        default_factory=list, validation_alias="XIAOHE_TRUSTED_PROXY_CIDRS"
    )
    knowledge_root: Path | None = Field(default=None, validation_alias="XIAOHE_KNOWLEDGE_ROOT")
    knowledge_embedding_base_url: str | None = Field(
        default=None, validation_alias="XIAOHE_KNOWLEDGE_EMBEDDING_BASE_URL"
    )
    knowledge_embedding_model: str | None = Field(
        default=None, validation_alias="XIAOHE_KNOWLEDGE_EMBEDDING_MODEL"
    )
    knowledge_embedding_api_key: SecretStr | None = Field(
        default=None, validation_alias="XIAOHE_KNOWLEDGE_EMBEDDING_API_KEY"
    )
    knowledge_embedding_dimensions: int | None = Field(
        default=None, gt=0, validation_alias="XIAOHE_KNOWLEDGE_EMBEDDING_DIMENSIONS"
    )

    @field_validator("knowledge_embedding_base_url")
    @classmethod
    def embedding_endpoint(cls, value):
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or any(char.isspace() for char in value)
        ):
            raise ValueError("invalid knowledge embedding endpoint")
        parsed.port
        return value.rstrip("/")

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def valid_networks(cls, value: list[str]) -> list[str]:
        return [str(ip_network(item, strict=False)) for item in value]

    @field_validator("toolkit_base_url")
    @classmethod
    def internal_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or any(char.isspace() or ord(char) < 32 for char in value)
        ):
            raise ValueError("TOOLKIT_BASE_URL must be a server-controlled HTTP origin")
        try:
            parsed.port
        except ValueError:
            raise ValueError("invalid internal port") from None
        return value.rstrip("/")

    @field_validator("auth_secret")
    @classmethod
    def required_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if not secret.strip() or any(ord(char) <= 32 or ord(char) >= 127 for char in secret):
            raise ValueError("AUTH_SECRET must be a nonempty ASCII bearer credential")
        return value

    @field_validator("minio_endpoint")
    @classmethod
    def minio_origin(cls, value):
        return cls.internal_origin(value) if value is not None else None

    @field_validator("reception_base_url")
    @classmethod
    def reception_origin(cls, value):
        return cls.internal_origin(value) if value is not None else None

    @field_validator("reception_secret", "reception_file_secret")
    @classmethod
    def reception_credential(cls, value):
        import re

        if (
            value is not None
            and re.fullmatch(r"[A-Za-z0-9._~-]{32,256}", value.get_secret_value()) is None
        ):
            raise ValueError("invalid reception service credential")
        return value

    @model_validator(mode="after")
    def independent_file_credential(self):
        if self.reception_file_secret is not None:
            existing = [
                secret.get_secret_value()
                for secret in (self.auth_secret, self.reception_secret, self.sso_bridge_secret)
                if secret is not None
            ]
            if self.reception_file_secret.get_secret_value() in existing:
                raise ValueError("file reader requires an independent credential")
        return self

    @field_validator("sso_bridge_base_url")
    @classmethod
    def sso_origin(cls, value):
        if value is None:
            return value
        origin = cls.internal_origin(value)
        if not origin.startswith("https://"):
            raise ValueError("SSO bridge requires a fixed HTTPS origin")
        return origin

    @field_validator("minio_bucket")
    @classmethod
    def bucket_name(cls, value):
        import re

        if value is not None and re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value) is None:
            raise ValueError("invalid bucket name")
        return value
