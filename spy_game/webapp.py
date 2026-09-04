"""Small same-process HTTP adapter for the Spy Game Telegram Mini App."""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiohttp import web

from config import logger

from .models import (
    AgencyStatus,
    DeadDropGameRun,
    DeadDropGameStatus,
    EconomyStatus,
    EquipmentStatus,
    InterceptGameRun,
    InterceptGameStatus,
    NpcStatus,
)
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
        bot=None,
    ) -> None:
        if not bot_token:
            raise ValueError("Telegram bot token is required for Web App auth")
        self.service = service
        self.bot_token = bot_token
        self.settings = settings
        self.bot = bot
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
                web.get(f"{self.BASE_PATH}/game/", self.game),
                web.get(f"{self.BASE_PATH}/game/game.js", self.game_javascript),
                web.get(f"{self.BASE_PATH}/game/game.css", self.game_styles),
                web.get(f"{self.BASE_PATH}/health", self.health),
                web.get(f"{self.BASE_PATH}/api/state", self.state),
                web.post(f"{self.BASE_PATH}/api/equipment/equip", self.equip),
                web.post(f"{self.BASE_PATH}/api/equipment/unequip", self.unequip),
                web.post(f"{self.BASE_PATH}/api/prestige", self.prestige),
                web.post(f"{self.BASE_PATH}/api/agency", self.agency),
                web.post(
                    f"{self.BASE_PATH}/api/contacts/exchange",
                    self.contact_exchange,
                ),
                web.get(f"{self.BASE_PATH}/api/game/state", self.game_state),
                web.post(f"{self.BASE_PATH}/api/game/finish", self.game_finish),
                web.post(f"{self.BASE_PATH}/api/game/guess", self.game_guess),
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

    @property
    def game_enabled(self) -> bool:
        return bool(
            self.settings.enabled
            and self.service.settings.enabled
            and self.settings.game_url
        )

    def game_launch_url(self, launch_token: str) -> str | None:
        if not self.game_enabled or not self.settings.game_url:
            return None
        parsed = urlsplit(self.settings.game_url)
        fragment = dict(parse_qsl(parsed.fragment, keep_blank_values=True))
        fragment["run"] = launch_token
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.query,
                urlencode(fragment),
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

    async def game(self, request: web.Request) -> web.Response:
        return web.FileResponse(
            self.ASSETS / "game.html",
            headers=self._static_headers("no-store"),
        )

    async def game_javascript(self, request: web.Request) -> web.Response:
        return web.FileResponse(
            self.ASSETS / "game.js",
            headers=self._static_headers("no-store"),
        )

    async def game_styles(self, request: web.Request) -> web.Response:
        return web.FileResponse(
            self.ASSETS / "game.css",
            headers=self._static_headers("public, max-age=300"),
        )

    async def health(self, request: web.Request) -> web.Response:
        return self._json_response(
            {
                "ok": True,
                "game_enabled": self.service.settings.enabled,
                "html5_game_enabled": self.game_enabled,
            }
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
        contact_names = {
            "operations_chief": "Начальник операций",
            "counterintelligence": "Контрразведка",
        }
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
            "contacts": [
                {
                    "id": recipe.id,
                    "npc_id": recipe.npc_id,
                    "npc_name": contact_names[recipe.npc_id],
                    "name": recipe.display_name,
                    "agent_costs": self._agent_costs(recipe.agent_costs),
                    "item_costs": self._item_costs(recipe.item_costs),
                    "reward": self._drop_entry(recipe.rewards[0]),
                }
                for recipe in self.service.settings.permanent_contact_recipes
            ],
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

    @staticmethod
    def _item_costs(costs) -> list[dict]:
        return [
            {
                "id": cost.item_type,
                "name": ITEM_TYPES[cost.item_type].display_name,
                "emoji": ITEM_TYPES[cost.item_type].emoji,
                "amount": cost.amount,
            }
            for cost in costs
        ]

    @staticmethod
    def _drop_entry(reward) -> dict:
        registry = AGENT_TYPES if reward.reward_type == "agent" else ITEM_TYPES
        definition = registry[reward.reward_id]
        return {
            "type": reward.reward_type,
            "id": reward.reward_id,
            "name": definition.display_name,
            "emoji": definition.emoji,
            "amount": reward.amount,
        }

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

    async def contact_exchange(self, request: web.Request) -> web.Response:
        identity = await self._authenticate(request, require_chat=True)
        payload = await self._json_object(request)
        recipe_id = payload.get("recipe_id")
        operation_id = payload.get("operation_id")
        if not isinstance(recipe_id, str) or not recipe_id:
            raise web.HTTPBadRequest(text="Неизвестная сделка")
        if not isinstance(operation_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", operation_id
        ):
            raise web.HTTPBadRequest(text="Некорректный идентификатор операции")
        result = await self.service.exchange_with_contact(
            operation_id=operation_id,
            recipe_id=recipe_id,
            chat_id=identity.chat_id,
            user_id=identity.user.user_id,
            username=identity.user.username,
            display_name=identity.user.display_name,
        )
        return self._json_response(
            {
                "ok": result.status is NpcStatus.SUCCESS,
                "status": result.status.value,
                "reward": self._drop_entry(result.reward) if result.reward else None,
                "required_agents": self._agent_costs(result.required_agents),
                "required_items": self._item_costs(result.required_items),
            }
        )

    async def _intercept_game_run(self, request: web.Request) -> InterceptGameRun:
        launch_token = request.headers.get("X-Spy-Game-Token", "")
        result = await self.service.get_intercept_game(launch_token)
        if result.status is InterceptGameStatus.NOT_FOUND:
            raise web.HTTPUnauthorized(text="Откройте игру заново из Telegram")
        if result.status is InterceptGameStatus.DISABLED:
            raise web.HTTPServiceUnavailable(text="Spy Clicker временно выключен")
        return result

    async def _game_session(
        self,
        request: web.Request,
    ) -> tuple[str, InterceptGameRun | DeadDropGameRun]:
        launch_token = request.headers.get("X-Spy-Game-Token", "")
        intercept = await self.service.get_intercept_game(launch_token)
        if intercept.status is not InterceptGameStatus.NOT_FOUND:
            if intercept.status is InterceptGameStatus.DISABLED:
                raise web.HTTPServiceUnavailable(text="Spy Clicker временно выключен")
            return "intercept", intercept
        dead_drop = await self.service.get_dead_drop_game(launch_token)
        if dead_drop.status is DeadDropGameStatus.NOT_FOUND:
            raise web.HTTPUnauthorized(text="Откройте игру заново из Telegram")
        if dead_drop.status is DeadDropGameStatus.DISABLED:
            raise web.HTTPServiceUnavailable(text="Spy Clicker временно выключен")
        return "dead_drop", dead_drop

    def _intercept_game_payload(self, result: InterceptGameRun) -> dict:
        reward = None
        if result.reward is not None and result.reward.reward_id in ITEM_TYPES:
            item = ITEM_TYPES[result.reward.reward_id]
            reward = {
                "id": item.id,
                "name": item.display_name,
                "emoji": item.emoji,
                "amount": result.reward.amount,
            }
        return {
            "game_type": "intercept",
            "status": result.status.value,
            "prompt": result.prompt,
            "targets": list(result.targets),
            "expires_at": result.expires_at.isoformat() if result.expires_at else None,
            "success_score": result.success_score,
            "score": result.score,
            "reward": reward,
        }

    def _dead_drop_game_payload(self, result: DeadDropGameRun) -> dict:
        reward = None
        if result.reward is not None:
            if (
                result.reward.reward_type == "item"
                and result.reward.reward_id in ITEM_TYPES
            ):
                item = ITEM_TYPES[result.reward.reward_id]
                reward = {
                    "type": "item",
                    "id": item.id,
                    "name": item.display_name,
                    "emoji": item.emoji,
                    "amount": result.reward.amount,
                }
            elif (
                result.reward.reward_type == "agent"
                and result.reward.reward_id in AGENT_TYPES
            ):
                agent = AGENT_TYPES[result.reward.reward_id]
                reward = {
                    "type": "agent",
                    "id": agent.id,
                    "name": agent.display_name,
                    "emoji": agent.emoji,
                    "amount": result.reward.amount,
                }
            else:
                reward = {
                    "type": "empty",
                    "id": None,
                    "name": "Тайник пуст",
                    "emoji": "∅",
                    "amount": 0,
                }
        return {
            "game_type": "dead_drop",
            "status": result.status.value,
            "code_length": result.code_length,
            "attempts": [
                {
                    "digits": list(attempt.digits),
                    "exact": attempt.exact,
                    "misplaced": attempt.misplaced,
                }
                for attempt in result.attempts
            ],
            "expires_at": result.expires_at.isoformat() if result.expires_at else None,
            "reward": reward,
        }

    async def game_state(self, request: web.Request) -> web.Response:
        game_type, result = await self._game_session(request)
        if game_type == "intercept":
            return self._json_response(self._intercept_game_payload(result))
        return self._json_response(self._dead_drop_game_payload(result))

    async def game_finish(self, request: web.Request) -> web.Response:
        active = await self._intercept_game_run(request)
        if active.status is not InterceptGameStatus.READY:
            return self._json_response(self._intercept_game_payload(active))
        payload = await self._json_object(request)
        locks = payload.get("locks")
        if not isinstance(locks, list):
            raise web.HTTPBadRequest(text="Некорректный журнал перехвата")
        try:
            result = await self.service.finish_intercept_game(
                request.headers.get("X-Spy-Game-Token", ""),
                tuple(locks),
            )
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        if result.status is InterceptGameStatus.WON and self.bot is not None:
            await self._announce_intercept_win(result)
        return self._json_response(self._intercept_game_payload(result))

    async def game_guess(self, request: web.Request) -> web.Response:
        game_type, active = await self._game_session(request)
        if game_type != "dead_drop":
            raise web.HTTPBadRequest(text="Эта операция не использует кодовый замок")
        if active.status is not DeadDropGameStatus.READY:
            return self._json_response(self._dead_drop_game_payload(active))
        payload = await self._json_object(request)
        guess = payload.get("guess")
        if not isinstance(guess, list):
            raise web.HTTPBadRequest(text="Некорректный код тайника")
        try:
            result = await self.service.guess_dead_drop_game(
                request.headers.get("X-Spy-Game-Token", ""),
                tuple(guess),
            )
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        if result.status is DeadDropGameStatus.WON and self.bot is not None:
            await self._announce_dead_drop_win(result)
        return self._json_response(self._dead_drop_game_payload(result))

    async def _announce_intercept_win(self, result: InterceptGameRun) -> None:
        if result.chat_id is None or result.reward is None:
            return
        item = ITEM_TYPES.get(result.reward.reward_id or "")
        reward_text = (
            f"{item.emoji} {item.display_name} ×{result.reward.amount}"
            if item is not None
            else "награда Центра"
        )
        try:
            if result.message_id is not None:
                await self.bot.edit_message_reply_markup(
                    chat_id=result.chat_id,
                    message_id=result.message_id,
                    reply_markup=None,
                )
        except Exception:
            logger.warning(
                "spy_game: HTML5 intercept keyboard remained event_id=%s",
                result.event_id,
            )
        try:
            await self.bot.send_message(
                chat_id=result.chat_id,
                text=(
                    "✅ ШИФР РАСКРЫТ\n"
                    f"{result.public_name or 'Скрытый агент'} восстановил канал "
                    f"и получил {reward_text}."
                ),
            )
        except Exception:
            logger.exception(
                "spy_game: HTML5 intercept announcement failed event_id=%s",
                result.event_id,
            )

    async def _announce_dead_drop_win(self, result: DeadDropGameRun) -> None:
        if result.chat_id is None or result.reward is None:
            return
        if result.reward.reward_type == "item":
            item = ITEM_TYPES.get(result.reward.reward_id or "")
            reward_text = (
                f"{item.emoji} {item.display_name} ×{result.reward.amount}"
                if item is not None
                else "предмет Центра"
            )
        elif result.reward.reward_type == "agent":
            agent = AGENT_TYPES.get(result.reward.reward_id or "")
            reward_text = (
                f"{agent.emoji} {agent.display_name} ×{result.reward.amount}"
                if agent is not None
                else "агент Центра"
            )
        else:
            reward_text = "ничего — тайник оказался пуст"
        try:
            if result.message_id is not None:
                await self.bot.edit_message_reply_markup(
                    chat_id=result.chat_id,
                    message_id=result.message_id,
                    reply_markup=None,
                )
        except Exception:
            logger.warning(
                "spy_game: HTML5 dead drop keyboard remained event_id=%s",
                result.event_id,
            )
        try:
            await self.bot.send_message(
                chat_id=result.chat_id,
                text=(
                    "✅ ТАЙНИК ВСКРЫТ\n"
                    f"{result.public_name or 'Скрытый агент'} подобрал код "
                    f"и нашёл: {reward_text}."
                ),
            )
        except Exception:
            logger.exception(
                "spy_game: HTML5 dead drop announcement failed event_id=%s",
                result.event_id,
            )
