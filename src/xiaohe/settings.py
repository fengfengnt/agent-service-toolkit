from ipaddress import ip_network
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
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
    database_url: SecretStr | None = Field(default=None, validation_alias="XIAOHE_DATABASE_URL")
    trusted_proxy_cidrs: list[str] = Field(
        default_factory=list, validation_alias="XIAOHE_TRUSTED_PROXY_CIDRS"
    )

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
