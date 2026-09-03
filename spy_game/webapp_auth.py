"""Authentication primitives for the Telegram Spy Game Mini App."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class WebAppAuthError(ValueError):
    """Raised when Telegram init data or launch context cannot be trusted."""


@dataclass(frozen=True)
class WebAppIdentity:
    user_id: int
    username: str | None
    display_name: str | None
    start_param: str | None
    auth_date: int


def _telegram_secret(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int,
    now: int | None = None,
) -> WebAppIdentity:
    """Validate Telegram.WebApp.initData and return its trusted user identity."""

    if not init_data or len(init_data) > 16_384:
        raise WebAppAuthError("missing or oversized init data")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise WebAppAuthError("malformed init data") from error
    if len({key for key, _ in pairs}) != len(pairs):
        raise WebAppAuthError("duplicate init data fields")
    values = dict(pairs)
    received_hash = values.pop("hash", None)
    if received_hash is None or len(received_hash) != 64:
        raise WebAppAuthError("missing init data hash")
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )
    expected_hash = hmac.new(
        _telegram_secret(bot_token),
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_hash.lower(), expected_hash):
        raise WebAppAuthError("invalid init data hash")

    try:
        auth_date = int(values["auth_date"])
    except (KeyError, TypeError, ValueError) as error:
        raise WebAppAuthError("invalid auth date") from error
    current = int(time.time()) if now is None else now
    if auth_date > current + 30:
        raise WebAppAuthError("init data is from the future")
    if current - auth_date > max_age_seconds:
        raise WebAppAuthError("expired init data")

    try:
        user = json.loads(values["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise WebAppAuthError("invalid Telegram user") from error
    if user_id <= 0 or user.get("is_bot") is True:
        raise WebAppAuthError("invalid Telegram user")
    username = user.get("username")
    if not isinstance(username, str) or not username.strip("@"):
        username = None
    first_name = (
        user.get("first_name") if isinstance(user.get("first_name"), str) else ""
    )
    last_name = user.get("last_name") if isinstance(user.get("last_name"), str) else ""
    display_name = " ".join(part for part in (first_name, last_name) if part).strip()
    return WebAppIdentity(
        user_id=user_id,
        username=username,
        display_name=display_name or None,
        start_param=values.get("start_param") or None,
        auth_date=auth_date,
    )


class LaunchContextSigner:
    """Issue user-bound, short-lived group contexts for ``startapp``."""

    _NONCE_SIZE = 24
    _SIGNATURE_SIZE = hashlib.sha256().digest_size

    def __init__(self, bot_token: str, ttl_seconds: int) -> None:
        self._key = hmac.new(
            bot_token.encode("utf-8"),
            b"spy-game-webapp-launch-v1",
            hashlib.sha256,
        ).digest()
        self.ttl_seconds = ttl_seconds
        self._contexts: dict[str, tuple[int, int, int]] = {}

    def _drop_expired(self, current: int) -> None:
        self._contexts = {
            token: context
            for token, context in self._contexts.items()
            if context[2] >= current
        }

    def issue(self, chat_id: int, user_id: int, *, now: int | None = None) -> str:
        current = int(time.time()) if now is None else now
        self._drop_expired(current)
        nonce = secrets.token_bytes(self._NONCE_SIZE)
        signature = hmac.new(self._key, nonce, hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(nonce + signature).rstrip(b"=").decode()
        self._contexts[token] = (chat_id, user_id, current + self.ttl_seconds)
        return token

    def verify(
        self,
        token: str,
        user_id: int,
        *,
        now: int | None = None,
    ) -> int:
        if not token or len(token) > 512:
            raise WebAppAuthError("invalid launch context")
        try:
            padding = "=" * (-len(token) % 4)
            decoded = base64.b64decode(
                token + padding,
                altchars=b"-_",
                validate=True,
            )
            signature_size = self._SIGNATURE_SIZE
            nonce, signature = (
                decoded[:-signature_size],
                decoded[-signature_size:],
            )
            expected = hmac.new(self._key, nonce, hashlib.sha256).digest()
        except (ValueError, TypeError) as error:
            raise WebAppAuthError("invalid launch context") from error
        if len(nonce) != self._NONCE_SIZE or not hmac.compare_digest(
            signature, expected
        ):
            raise WebAppAuthError("invalid launch context signature")
        current = int(time.time()) if now is None else now
        self._drop_expired(current)
        context = self._contexts.get(token)
        if context is None:
            raise WebAppAuthError("expired or unknown launch context")
        chat_id, expected_user_id, expires_at = context
        if chat_id == 0 or expected_user_id != user_id or expires_at < current:
            raise WebAppAuthError("expired or mismatched launch context")
        return chat_id
