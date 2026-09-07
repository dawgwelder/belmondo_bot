"""Environment configuration for the same-process Spy Clicker HTTP server."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


def _env_bool(name: str, default: bool) -> bool:
    import os

    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_int(name: str, default: int) -> int:
    import os

    value = os.getenv(name)
    try:
        return default if value is None else int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


@dataclass(frozen=True)
class SpyWebAppSettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8080
    launch_url: str | None = None
    game_url: str | None = None
    game_short_name: str = "spies"
    init_data_max_age_seconds: int = 5 * 60
    launch_context_ttl_seconds: int = 10 * 60
    rate_limit_per_minute: int = 60

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65_535:
            raise ValueError("SPY_GAME_WEBAPP_PORT must be between 1 and 65535")
        if (
            self.init_data_max_age_seconds <= 0
            or self.launch_context_ttl_seconds <= 0
            or self.rate_limit_per_minute <= 0
        ):
            raise ValueError(
                "Spy Game Web App timeouts and rate limit must be positive"
            )
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", self.game_short_name):
            raise ValueError("SPY_GAME_HTML5_SHORT_NAME is invalid")
        if self.enabled:
            if not self.launch_url and not self.game_url:
                raise ValueError(
                    "a Mini App launch URL or HTML5 Game URL is required when "
                    "the Web App server is enabled"
                )
        if self.launch_url:
            parsed = urlsplit(self.launch_url)
            path_parts = [part for part in parsed.path.split("/") if part]
            if (
                parsed.scheme != "https"
                or parsed.hostname not in {"t.me", "telegram.me"}
                or len(path_parts) != 2
            ):
                raise ValueError(
                    "SPY_GAME_WEBAPP_LAUNCH_URL must be an HTTPS Telegram "
                    "direct link such as https://t.me/bot/app"
                )
        if self.game_url:
            parsed = urlsplit(self.game_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("SPY_GAME_HTML5_URL must be a public HTTPS URL")

    @classmethod
    def from_env(cls) -> "SpyWebAppSettings":
        import os

        return cls(
            enabled=_env_bool("SPY_GAME_WEBAPP_ENABLED", False),
            host=os.getenv("SPY_GAME_WEBAPP_HOST", "127.0.0.1"),
            port=_env_int("SPY_GAME_WEBAPP_PORT", 8080),
            launch_url=os.getenv("SPY_GAME_WEBAPP_LAUNCH_URL") or None,
            game_url=os.getenv("SPY_GAME_HTML5_URL") or None,
            game_short_name=os.getenv("SPY_GAME_HTML5_SHORT_NAME", "spies"),
            init_data_max_age_seconds=_env_int(
                "SPY_GAME_WEBAPP_INIT_DATA_MAX_AGE_SECONDS", 5 * 60
            ),
            launch_context_ttl_seconds=_env_int(
                "SPY_GAME_WEBAPP_CONTEXT_TTL_SECONDS", 10 * 60
            ),
            rate_limit_per_minute=_env_int("SPY_GAME_WEBAPP_RATE_LIMIT_PER_MINUTE", 60),
        )
