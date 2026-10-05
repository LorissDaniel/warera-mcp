"""Application configuration.

Configuration is process-level only. It never carries WarEra player
credentials: those are request-scoped tool arguments (see
:mod:`warera_mcp.auth`). The upstream host is fixed by default and guarded
against accidental SSRF-style redirection.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The only production upstream host. Kept as a constant so the guard below and
#: the client share a single source of truth.
FIXED_UPSTREAM_HOST = "api2.warera.io"
FIXED_UPSTREAM_URL = f"https://{FIXED_UPSTREAM_HOST}"

#: Bind addresses treated as local-only.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


class Settings(BaseSettings):
    """Validated, immutable process settings.

    Every field is overridable through ``WARERA_MCP_*`` environment variables so
    that deployments stay provider-neutral and no vendor-specific service is a
    prerequisite. Unknown environment keys are rejected to catch typos early.
    """

    model_config = SettingsConfigDict(
        env_prefix="WARERA_MCP_",
        extra="forbid",
        case_sensitive=False,
        frozen=True,
        # Complex env values are comma-separated lists, not JSON. Disabling
        # pydantic-settings decoding lets the CSV field validators below see the
        # raw string instead of failing on json.loads.
        enable_decoding=False,
    )

    # ------------------------------------------------------------------ upstream
    warera_base_url: AnyHttpUrl = AnyHttpUrl(FIXED_UPSTREAM_URL)
    allow_custom_upstream: bool = False
    """Explicit opt-in required to point the client at a non-WarEra host.

    Only intended for local mock servers in tests. The default keeps the fixed
    ``api2.warera.io`` origin mandated by the security model.
    """
    warera_origin: str = "https://app.warera.io"
    request_user_agent: str = "warera-mcp/0.1 (+https://github.com/example/warera-mcp)"
    connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    read_timeout_seconds: float = Field(default=6.0, gt=0, le=60)
    write_timeout_seconds: float = Field(default=4.0, gt=0, le=60)
    pool_timeout_seconds: float = Field(default=1.0, gt=0, le=30)
    max_connections: int = Field(default=16, ge=1, le=256)
    max_keepalive_connections: int = Field(default=8, ge=0, le=256)
    max_concurrent_requests: int = Field(default=8, ge=1, le=128)
    max_response_bytes: int = Field(default=8_000_000, ge=1024, le=64_000_000)

    # ---------------------------------------------------------------- batching
    batching_enabled: bool = True
    batch_window_seconds: float = Field(default=0.3, ge=0, le=0.4)
    batch_max_size: int = Field(default=20, ge=1, le=50)
    batch_max_url_bytes: int = Field(default=8000, ge=1024, le=64_000)

    # ------------------------------------------------------------------ retries
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_base_backoff_seconds: float = Field(default=0.2, ge=0, le=5)
    retry_max_backoff_seconds: float = Field(default=2.0, ge=0, le=30)
    retry_max_retry_after_seconds: float = Field(default=10.0, ge=0, le=120)

    # --------------------------------------------------------------- resilience
    circuit_failure_threshold: int = Field(default=5, ge=1, le=100)
    circuit_cooldown_seconds: float = Field(default=15.0, ge=0, le=300)
    outbound_rate_per_second: float = Field(default=8.0, gt=0, le=1000)
    outbound_rate_burst: int = Field(default=16, ge=1, le=1000)

    # -------------------------------------------------------------------- cache
    cache_enabled: bool = True
    cache_max_entries: int = Field(default=512, ge=1, le=100_000)

    # ----------------------------------------------------------------- tool caps
    max_fanout: int = Field(default=10, ge=1, le=50)
    max_output_bytes: int = Field(default=262_144, ge=4096, le=8_000_000)
    tool_deadline_seconds: float = Field(default=12.0, gt=0, le=120)
    """Hard wall-clock budget for one tool call, retries and fan-out included."""
    max_pending_requests: int = Field(default=64, ge=1, le=10_000)
    """Upstream reads allowed to wait on the shared limiter before new ones are shed."""

    # ------------------------------------------------------------------- server
    server_name: str = "warera-mcp"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    streamable_http_path: str = "/mcp"
    stateless_http: bool = True
    json_response: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = True
    health_path: str = "/healthz"
    max_request_body_bytes: int = Field(default=1_000_000, ge=1024, le=16_000_000)

    # ------------------------------------------------- MCP client authentication
    require_client_auth: bool = False
    client_auth_tokens: tuple[SecretStr, ...] = ()
    """Hashed-at-load bearer tokens for MCP clients. Distinct from WarEra creds."""
    client_rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)
    client_rate_limit_burst: int = Field(default=30, ge=1, le=10_000)
    trusted_hosts: tuple[str, ...] = ()
    """Allowed ``Host`` header values; enables DNS-rebinding protection when set."""
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)
    """Number of trusted reverse proxies in front of the server (0 = none).

    With a value of N, the client address is the Nth entry from the right of
    ``X-Forwarded-For``; the header is ignored entirely when this is 0.
    """
    enable_cors: bool = False
    cors_allow_origins: tuple[str, ...] = ()

    @field_validator("client_auth_tokens", "cors_allow_origins", "trusted_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allow ``a,b,c`` in environment variables for tuple fields."""
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @model_validator(mode="after")
    def _guard_upstream(self) -> Settings:
        if self.allow_custom_upstream:
            return self
        host = (self.warera_base_url.host or "").lower()
        if host != FIXED_UPSTREAM_HOST:
            raise ValueError(
                "warera_base_url must remain the fixed WarEra host "
                f"'{FIXED_UPSTREAM_HOST}' unless allow_custom_upstream is enabled"
            )
        if self.warera_base_url.scheme != "https":
            raise ValueError("warera_base_url must use https")
        return self

    @model_validator(mode="after")
    def _guard_client_auth(self) -> Settings:
        if self.require_client_auth and not self.client_auth_tokens:
            raise ValueError("require_client_auth=True needs at least one client_auth_tokens entry")
        return self

    @property
    def binds_loopback_only(self) -> bool:
        return self.host in LOOPBACK_HOSTS

    def exposure_warnings(self) -> tuple[str, ...]:
        """Human-readable notices about risky remote-deployment combinations."""
        notices: list[str] = []
        if not self.binds_loopback_only and not self.require_client_auth:
            notices.append(
                f"listening on {self.host} without client authentication: every caller that "
                "can reach this port may use the server. Enable require_client_auth or "
                "put an authenticating gateway in front of it."
            )
        if not self.binds_loopback_only and not self.trusted_hosts:
            notices.append(
                "no trusted_hosts configured: Host/Origin validation (DNS-rebinding "
                "protection) is disabled for this non-loopback bind."
            )
        return tuple(notices)

    @property
    def upstream_host(self) -> str:
        return self.warera_base_url.host or FIXED_UPSTREAM_HOST


def load_settings(**overrides: object) -> Settings:
    """Load settings from the environment, applying explicit overrides."""
    return Settings(**overrides)  # type: ignore[arg-type]


__all__ = [
    "FIXED_UPSTREAM_HOST",
    "FIXED_UPSTREAM_URL",
    "LOOPBACK_HOSTS",
    "Settings",
    "load_settings",
]
