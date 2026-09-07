"""Same-process Spy Clicker HTTP routes, authentication and server lifecycle."""
from __future__ import annotations

import hashlib
import re
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
    FindMoleGameRun,
    FindMoleGameStatus,
    InterceptGameRun,
    InterceptGameStatus,
    NpcStatus,
)
from .service import SpyGameService
from .settings import ITEM_TYPES
from .death_mission_repository import DeathMissionRun
from .death_mission_ui import publish_pending
from .webapp_auth import LaunchContextSigner, WebAppAuthError, validate_init_data
from . import webapp_presenters as presenters
from . import webapp_notifications as notifications
from .webapp_settings import SpyWebAppSettings
from .webapp_support import RequestIdentity, _RateLimiter

__all__ = ["SpyWebAppServer", "SpyWebAppSettings", "RequestIdentity", "_RateLimiter"]


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
        self.game_rate_limiter = _RateLimiter(settings.rate_limit_per_minute)
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
                web.get(f"{self.BASE_PATH}/game/death.js", self.death_javascript),
                web.post(
                    f"{self.BASE_PATH}/api/game/death/{{action}}",
                    self.game_death_action,
                ),
                web.get(f"{self.BASE_PATH}/health", self.health),
                web.get(f"{self.BASE_PATH}/api/state", self.state),
                web.post(
                    f"{self.BASE_PATH}/api/achievements/title", self.achievement_title
                ),
                web.post(
                    f"{self.BASE_PATH}/api/achievements/seen", self.achievements_seen
                ),
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
                web.post(
                    f"{self.BASE_PATH}/api/game/mole/accuse",
                    self.game_mole_accuse,
                ),
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

    @property
    def mole_game_enabled(self) -> bool:
        return self.game_enabled and self.service.settings.html5_mole_enabled

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

    async def death_javascript(self, request: web.Request) -> web.Response:
        return web.FileResponse(
            self.ASSETS / "death.js", headers=self._static_headers("no-store")
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
                "html5_mole_enabled": self.mole_game_enabled,
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

    async def state(self, request: web.Request) -> web.Response:
        identity = await self._authenticate(request, require_chat=False)
        return self._json_response(
            await presenters.state_payload(self.service, identity)
        )

    async def achievement_title(self, request: web.Request) -> web.Response:
        identity = await self._authenticate(request, require_chat=False)
        payload = await self._json_object(request)
        if "achievement_id" not in payload:
            raise web.HTTPBadRequest(text="Укажите титул")
        key = payload["achievement_id"]
        if key is not None and (not isinstance(key, str) or len(key) > 40):
            raise web.HTTPBadRequest(text="Некорректный титул")
        ok = await self.service.select_achievement_title(identity.user.user_id, key)
        return self._json_response({"ok": ok, "status": "success" if ok else "locked"})

    async def achievements_seen(self, request: web.Request) -> web.Response:
        identity = await self._authenticate(request, require_chat=False)
        payload = await self._json_object(request)
        keys = payload.get("achievement_ids")
        if (
            not isinstance(keys, list)
            or len(keys) > 100
            or any(not isinstance(key, str) or len(key) > 40 for key in keys)
        ):
            raise web.HTTPBadRequest(text="Некорректный список достижений")
        await self.service.mark_achievements_seen(identity.user.user_id, keys)
        return self._json_response({"ok": True, "status": "success"})

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
                "required": presenters.agent_costs(result.required),
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
                "required_agents": presenters.agent_costs(result.required_agents),
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
                "reward": presenters.drop_entry(result.reward)
                if result.reward
                else None,
                "required_agents": presenters.agent_costs(result.required_agents),
                "required_items": presenters.item_costs(result.required_items),
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
    ) -> tuple[
        str, InterceptGameRun | DeadDropGameRun | FindMoleGameRun | DeathMissionRun
    ]:
        launch_token = request.headers.get("X-Spy-Game-Token", "")
        game_type, result = await self.service.get_html5_game(launch_token)
        if game_type is None:
            raise web.HTTPUnauthorized(text="Откройте игру заново из Telegram")
        if result.status in {
            InterceptGameStatus.DISABLED,
            DeadDropGameStatus.DISABLED,
            FindMoleGameStatus.DISABLED,
        }:
            raise web.HTTPServiceUnavailable(text="Spy Clicker временно выключен")
        return game_type, result

    def _limit_game_mutation(self, request: web.Request) -> None:
        token = request.headers.get("X-Spy-Game-Token", "")
        subject = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if not self.game_rate_limiter.allow(subject):
            raise web.HTTPTooManyRequests(
                text="Слишком много игровых запросов. Повторите через минуту."
            )

    async def game_state(self, request: web.Request) -> web.Response:
        game_type, result = await self._game_session(request)
        if game_type == "death_operation":
            return self._json_response(presenters.death_payload(result))
        if game_type == "intercept":
            return self._json_response(presenters.intercept_game_payload(result))
        if game_type == "find_mole":
            return self._json_response(presenters.find_mole_game_payload(result))
        return self._json_response(presenters.dead_drop_game_payload(result))

    async def game_death_action(self, request: web.Request) -> web.Response:
        self._limit_game_mutation(request)
        game_type, _ = await self._game_session(request)
        if game_type != "death_operation":
            raise web.HTTPBadRequest(text="Это не Смертельная операция")
        payload = await self._json_object(request)
        try:
            result = await self.service.mutate_death_mission(
                request.headers.get("X-Spy-Game-Token", ""),
                action=request.match_info["action"],
                revision=payload.get("revision"),
                operation_id=payload.get("operation_id"),
                choice=payload.get("choice", {}),
            )
        except (TypeError, ValueError) as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        if self.bot is not None:
            await publish_pending(self.service, self.bot)
        return self._json_response(presenters.death_payload(result))

    async def game_finish(self, request: web.Request) -> web.Response:
        self._limit_game_mutation(request)
        active = await self._intercept_game_run(request)
        if active.status is not InterceptGameStatus.READY:
            return self._json_response(presenters.intercept_game_payload(active))
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
            await notifications.announce_intercept_win(self.bot, result)
        return self._json_response(presenters.intercept_game_payload(result))

    async def game_guess(self, request: web.Request) -> web.Response:
        self._limit_game_mutation(request)
        game_type, active = await self._game_session(request)
        if game_type != "dead_drop":
            raise web.HTTPBadRequest(text="Эта операция не использует кодовый замок")
        if active.status is not DeadDropGameStatus.READY:
            return self._json_response(presenters.dead_drop_game_payload(active))
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
            await notifications.announce_dead_drop_win(self.bot, result)
        return self._json_response(presenters.dead_drop_game_payload(result))

    async def game_mole_accuse(self, request: web.Request) -> web.Response:
        self._limit_game_mutation(request)
        game_type, active = await self._game_session(request)
        if game_type != "find_mole":
            raise web.HTTPBadRequest(text="Эта операция не содержит дела о кроте")
        if active.status is not FindMoleGameStatus.READY:
            return self._json_response(presenters.find_mole_game_payload(active))
        payload = await self._json_object(request)
        suspect_id = payload.get("suspect_id")
        revision = payload.get("revision")
        idempotency_key = payload.get("idempotency_key")
        if not isinstance(suspect_id, str):
            raise web.HTTPBadRequest(text="Неизвестный подозреваемый")
        try:
            result = await self.service.accuse_find_mole_game(
                request.headers.get("X-Spy-Game-Token", ""),
                suspect_id,
                revision,
                idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        if result.newly_won and self.bot is not None:
            await notifications.announce_find_mole_win(self.bot, result)
        return self._json_response(presenters.find_mole_game_payload(result))
