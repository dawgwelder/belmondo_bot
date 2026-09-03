"""Small same-process HTTP adapter for the Spy Game Telegram Mini App."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiohttp import web

from config import logger

from .models import AgencyStatus, EconomyStatus, EquipmentStatus
from .service import SpyGameService
from .settings import AGENT_TYPES, ITEM_TYPES
from .webapp_auth import (
    LaunchContextSigner,
    WebAppAuthError,
    WebAppIdentity,
    validate_init_data,
)


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
        if self.enabled:
            if not self.launch_url:
                raise ValueError(
                    "SPY_GAME_WEBAPP_LAUNCH_URL is required when Web App is enabled"
                )
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

    @classmethod
    def from_env(cls) -> "SpyWebAppSettings":
        import os

        return cls(
            enabled=_env_bool("SPY_GAME_WEBAPP_ENABLED", False),
            host=os.getenv("SPY_GAME_WEBAPP_HOST", "127.0.0.1"),
            port=_env_int("SPY_GAME_WEBAPP_PORT", 8080),
            launch_url=os.getenv("SPY_GAME_WEBAPP_LAUNCH_URL") or None,
            init_data_max_age_seconds=_env_int(
                "SPY_GAME_WEBAPP_INIT_DATA_MAX_AGE_SECONDS", 5 * 60
            ),
            launch_context_ttl_seconds=_env_int(
                "SPY_GAME_WEBAPP_CONTEXT_TTL_SECONDS", 10 * 60
            ),
            rate_limit_per_minute=_env_int("SPY_GAME_WEBAPP_RATE_LIMIT_PER_MINUTE", 60),
        )


@dataclass(frozen=True)
class RequestIdentity:
    user: WebAppIdentity
    chat_id: int | None


class _RateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._requests: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        requests = self._requests[user_id]
        while requests and requests[0] <= current - 60:
            requests.popleft()
        if len(requests) >= self.limit:
            return False
        requests.append(current)
        return True


class SpyWebAppServer:
    BASE_PATH = "/spy-app"
    ASSETS = Path(__file__).with_name("webapp_static")

    def __init__(
        self,
        service: SpyGameService,
        bot_token: str,
        settings: SpyWebAppSettings,
    ) -> None:
        if not bot_token:
            raise ValueError("Telegram bot token is required for Web App auth")
        self.service = service
        self.bot_token = bot_token
        self.settings = settings
        self.signer = LaunchContextSigner(
            bot_token,
            settings.launch_context_ttl_seconds,
        )
        self.rate_limiter = _RateLimiter(settings.rate_limit_per_minute)
        self._runner: web.AppRunner | None = None
        self.app = self._build_application()

    def _build_application(self) -> web.Application:
        app = web.Application(client_max_size=4 * 1024)
        app.add_routes(
            [
                web.get(f"{self.BASE_PATH}/", self.index),
                web.get(f"{self.BASE_PATH}/app.js", self.javascript),
                web.get(f"{self.BASE_PATH}/styles.css", self.styles),
                web.get(f"{self.BASE_PATH}/health", self.health),
                web.get(f"{self.BASE_PATH}/api/state", self.state),
                web.post(f"{self.BASE_PATH}/api/equipment/equip", self.equip),
                web.post(f"{self.BASE_PATH}/api/equipment/unequip", self.unequip),
                web.post(f"{self.BASE_PATH}/api/prestige", self.prestige),
                web.post(f"{self.BASE_PATH}/api/agency", self.agency),
            ]
        )
        return app

    async def start(self) -> None:
        if not self.settings.enabled or self._runner is not None:
            return
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(runner, self.settings.host, self.settings.port)
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner
        logger.info(
            "Spy Game Web App listening on http://%s:%s%s/",
            self.settings.host,
            self.settings.port,
            self.BASE_PATH,
        )

    async def close(self) -> None:
        if self._runner is None:
            return
        runner, self._runner = self._runner, None
        await runner.cleanup()

    def launch_url(self, chat_id: int, user_id: int) -> str | None:
        if (
            not self.settings.enabled
            or not self.service.settings.enabled
            or not self.settings.launch_url
        ):
            return None
        if not self.service.settings.chat_is_allowed(chat_id):
            return self.settings.launch_url
        token = self.signer.issue(chat_id, user_id)
        parsed = urlsplit(self.settings.launch_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["startapp"] = token
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )

    @staticmethod
    def _static_headers(cache_control: str) -> dict[str, str]:
        return {
            "Cache-Control": cache_control,
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' https://telegram.org; "
                "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
                "frame-ancestors https://web.telegram.org https://*.telegram.org"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }

    async def index(self, request: web.Request) -> web.Response:
        return web.FileResponse(
            self.ASSETS / "index.html",
            headers=self._static_headers("no-store"),
        )

    async def javascript(self, request: web.Request) -> web.Response:
        return web.FileResponse(
            self.ASSETS / "app.js",
            headers=self._static_headers("public, max-age=300"),
        )

    async def styles(self, request: web.Request) -> web.Response:
        return web.FileResponse(
            self.ASSETS / "styles.css",
            headers=self._static_headers("public, max-age=300"),
        )

    async def health(self, request: web.Request) -> web.Response:
        return self._json_response(
            {"ok": True, "game_enabled": self.service.settings.enabled}
        )

    @staticmethod
    def _json_response(payload: dict) -> web.Response:
        return web.json_response(payload, headers={"Cache-Control": "no-store"})

    async def _authenticate(
        self,
        request: web.Request,
        *,
        require_chat: bool,
    ) -> RequestIdentity:
        try:
            user = validate_init_data(
                request.headers.get("X-Telegram-Init-Data", ""),
                self.bot_token,
                max_age_seconds=self.settings.init_data_max_age_seconds,
            )
            if not self.rate_limiter.allow(user.user_id):
                raise web.HTTPTooManyRequests(
                    text="Слишком много запросов. Повторите через минуту."
                )
            chat_id = (
                self.signer.verify(user.start_param, user.user_id)
                if user.start_param
                else None
            )
        except WebAppAuthError as error:
            logger.warning("spy_webapp_auth_failed reason=%s", error)
            raise web.HTTPUnauthorized(text="Откройте приложение заново из Telegram")
        if not self.service.settings.enabled:
            raise web.HTTPServiceUnavailable(text="Spy Clicker временно выключен")
        if chat_id is not None and not self.service.settings.chat_is_allowed(chat_id):
            raise web.HTTPForbidden(text="Этот чат недоступен")
        if require_chat:
            if chat_id is None:
                raise web.HTTPForbidden(
                    text="Откройте приложение кнопкой из группового /spy"
                )
            status = await self.service.get_chat_status(chat_id)
            if not status.enabled:
                raise web.HTTPForbidden(text="Сеть в этом чате не активирована")
        return RequestIdentity(user, chat_id)

    @staticmethod
    async def _json_object(request: web.Request) -> dict:
        try:
            payload = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="Ожидался JSON")
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Ожидался JSON-объект")
        return payload

    async def _state_payload(self, identity: RequestIdentity) -> dict:
        user = identity.user
        profile = await self.service.get_profile(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
        )
        agents = await self.service.get_agents(user.user_id)
        inventory = await self.service.get_inventory(user.user_id)
        leaderboard = await self.service.get_leaderboard()
        chat_status = (
            await self.service.get_chat_status(identity.chat_id)
            if identity.chat_id is not None
            else None
        )
        prestige_costs = self.service.settings.prestige_costs(profile.reputation)
        agency_at_cap = profile.agency_level >= self.service.settings.agency_max_level
        agency_costs = (
            ()
            if agency_at_cap
            else self.service.settings.agency_requirements(profile.agency_level)
        )
        return {
            "profile": {
                "username": f"@{profile.username.lstrip('@')}"
                if profile.username
                else None,
                "reputation": profile.reputation,
                "agency_level": profile.agency_level,
                "agency_max_level": self.service.settings.agency_max_level,
                "total_agents": profile.total_agents,
            },
            "agents": [
                {
                    "id": holding.agent_type,
                    "name": AGENT_TYPES[holding.agent_type].display_name,
                    "emoji": AGENT_TYPES[holding.agent_type].emoji,
                    "tier": AGENT_TYPES[holding.agent_type].tier,
                    "amount": holding.amount,
                }
                for holding in agents
                if holding.agent_type in AGENT_TYPES
            ],
            "inventory": {
                "slot_count": inventory.slot_count,
                "items": [
                    {
                        "id": holding.item_type,
                        "name": ITEM_TYPES[holding.item_type].display_name,
                        "emoji": ITEM_TYPES[holding.item_type].emoji,
                        "category": ITEM_TYPES[holding.item_type].category.value,
                        "amount": holding.amount,
                    }
                    for holding in inventory.items
                    if holding.item_type in ITEM_TYPES
                ],
                "equipped": [
                    {
                        "slot": item.slot,
                        "item_type": item.item_type,
                        "name": ITEM_TYPES[item.item_type].display_name,
                        "emoji": ITEM_TYPES[item.item_type].emoji,
                    }
                    for item in inventory.equipped
                    if item.item_type in ITEM_TYPES
                ],
            },
            "leaderboard": [
                {
                    "rank": entry.rank,
                    "name": entry.display_name,
                    "total_agents": entry.total_agents,
                    "rare_agents": entry.rare_agents,
                    "reputation": entry.reputation,
                    "agency_level": entry.agency_level,
                }
                for entry in leaderboard
            ],
            "prestige": {
                "expected_reputation": profile.reputation,
                "costs": self._agent_costs(prestige_costs),
            },
            "agency": {
                "at_cap": agency_at_cap,
                "expected_level": profile.agency_level,
                "required_reputation": (
                    0
                    if agency_at_cap
                    else self.service.settings.agency_reputation_requirement(
                        profile.agency_level
                    )
                ),
                "costs": self._agent_costs(agency_costs),
                "rare_bonus_percent": min(
                    profile.agency_level
                    * self.service.settings.agency_rare_bonus_percent,
                    self.service.settings.agency_max_level
                    * self.service.settings.agency_rare_bonus_percent,
                ),
            },
            "context": {
                "chat_bound": identity.chat_id is not None,
                "can_mutate": bool(chat_status and chat_status.enabled),
                "network_enabled": bool(chat_status and chat_status.enabled),
                "activity_score": chat_status.activity_score if chat_status else None,
                "activity_profile": chat_status.activity_profile
                if chat_status
                else None,
                "active_event": bool(chat_status and chat_status.active_event_id),
            },
        }

    @staticmethod
    def _agent_costs(costs) -> list[dict]:
        return [
            {
                "id": cost.agent_type,
                "name": AGENT_TYPES[cost.agent_type].display_name,
                "emoji": AGENT_TYPES[cost.agent_type].emoji,
                "amount": cost.amount,
            }
            for cost in costs
        ]

    async def state(self, request: web.Request) -> web.Response:
        identity = await self._authenticate(request, require_chat=False)
        return self._json_response(await self._state_payload(identity))

    async def equip(self, request: web.Request) -> web.Response:
        identity = await self._authenticate(request, require_chat=True)
        payload = await self._json_object(request)
        item_type = payload.get("item_type")
        if not isinstance(item_type, str) or item_type not in ITEM_TYPES:
            raise web.HTTPBadRequest(text="Неизвестный предмет")
        result = await self.service.equip_item(
            chat_id=identity.chat_id,
            user_id=identity.user.user_id,
            item_type=item_type,
        )
        return self._json_response(
            {
                "ok": result.status is EquipmentStatus.SUCCESS,
                "status": result.status.value,
                "slot": result.slot,
            }
        )

    async def unequip(self, request: web.Request) -> web.Response:
        identity = await self._authenticate(request, require_chat=True)
        payload = await self._json_object(request)
        slot = payload.get("slot")
        if type(slot) is not int or slot < 1:
            raise web.HTTPBadRequest(text="Некорректный слот")
        result = await self.service.unequip_item(
            chat_id=identity.chat_id,
            user_id=identity.user.user_id,
            slot=slot,
        )
        return self._json_response(
            {
                "ok": result.status is EquipmentStatus.SUCCESS,
                "status": result.status.value,
            }
        )

    async def prestige(self, request: web.Request) -> web.Response:
        identity = await self._authenticate(request, require_chat=True)
        payload = await self._json_object(request)
        expected = payload.get("expected_reputation")
        if type(expected) is not int or expected < 0:
            raise web.HTTPBadRequest(text="Некорректная репутация")
        result = await self.service.increase_reputation(
            chat_id=identity.chat_id,
            user_id=identity.user.user_id,
            username=identity.user.username,
            display_name=identity.user.display_name,
            expected_reputation=expected,
        )
        return self._json_response(
            {
                "ok": result.status is EconomyStatus.SUCCESS,
                "status": result.status.value,
                "reputation": result.reputation,
                "required": self._agent_costs(result.required),
            }
        )

    async def agency(self, request: web.Request) -> web.Response:
        identity = await self._authenticate(request, require_chat=True)
        payload = await self._json_object(request)
        expected = payload.get("expected_level")
        if type(expected) is not int or expected < 0:
            raise web.HTTPBadRequest(text="Некорректный уровень службы")
        result = await self.service.found_agency(
            chat_id=identity.chat_id,
            user_id=identity.user.user_id,
            username=identity.user.username,
            display_name=identity.user.display_name,
            expected_agency_level=expected,
        )
        return self._json_response(
            {
                "ok": result.status is AgencyStatus.SUCCESS,
                "status": result.status.value,
                "agency_level": result.agency_level,
                "required_reputation": result.required_reputation,
                "required_agents": self._agent_costs(result.required_agents),
            }
        )
