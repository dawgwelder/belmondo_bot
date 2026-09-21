"""Spy Clicker Telegram menu."""
from __future__ import annotations

from datetime import datetime, timezone
from telegram import Update
from telegram.ext import ContextTypes
from guards import pause
from spy_game.models import Profile
from spy_game.service import SpyGameService
from .context import _service
from .formatting import _display_name
from .menu_views import _menu_keyboard, build_menu_blocks
from .transport import _send_temporary_rich


async def _profile_for_update(update: Update, service: SpyGameService) -> Profile:
    user = update.effective_user
    return await service.get_profile(
        user_id=user.id,
        username=user.username,
        display_name=_display_name(user),
    )


async def _send_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = _service(context)
    chat = update.effective_chat
    profile = await _profile_for_update(update, service)
    if getattr(chat, "type", None) in {"group", "supergroup"}:
        # Persist membership right away so the Mini App opened from the bot
        # menu gets the full cabinet without waiting for the next tick.
        await service.touch_member_now(
            chat.id, update.effective_user.id, title=getattr(chat, "title", None)
        )
    status = await service.get_chat_status(chat.id)
    webapp = context.bot_data.get("spy_webapp")
    launch_url = webapp.launch_url(chat.id) if webapp is not None else None
    now = datetime.now(timezone.utc)
    achievements = await service.get_achievements(update.effective_user.id)
    blocks = build_menu_blocks(profile, status, now)
    archive_line = (
        f"🎖 Заслуги: {achievements['unlocked']}/{achievements['total']} · "
        f"Новые: {achievements['new_count']}"
    )
    if achievements["title"]:
        archive_line += f"\nТитул: {achievements['title']}"
    blocks.append({"type": "paragraph", "text": archive_line})
    await _send_temporary_rich(
        context,
        update.effective_chat,
        blocks,
        fallback_text=(
            "🕵️ Spy Clicker\n"
            f"Агентов: {profile.total_agents}\n"
            f"Активность: {status.activity_score:.1f}\n"
            "Сигнал может прийти на пике, по инерции или случайно.\n" + archive_line
        ),
        reply_markup=_menu_keyboard(profile, launch_url),
    )


@pause
async def spy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_chat is None:
        return
    await _send_menu(update, context)
