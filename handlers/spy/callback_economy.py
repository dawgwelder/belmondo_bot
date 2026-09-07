"""Spy Clicker Telegram callback economy."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from config import logger
from spy_game.models import AgencyStatus, EconomyStatus, EquipmentStatus, NpcStatus
from spy_game.settings import ITEM_TYPES
from .context import _service
from .formatting import (
    _display_name,
    _format_costs,
    _format_drop_reward,
    _format_item_costs,
    _public_label,
)
from .menu import _profile_for_update, _send_menu
from .transport import _send_rich


async def handle_contact(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    recipe_id = category.removeprefix("contact_")
    try:
        result = await service.exchange_with_contact(
            operation_id=query.id,
            recipe_id=recipe_id,
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception(
            "spy_game: permanent contact exchange failed recipe_id=%s",
            recipe_id,
        )
        await query.answer(
            "Центр не провёл сделку. Ресурсы не изменены.",
            show_alert=True,
        )
        return
    if result.status is NpcStatus.SUCCESS:
        await query.answer(
            f"Сделка завершена: {_format_drop_reward(result.reward)}",
            show_alert=True,
        )
    elif result.status is NpcStatus.INSUFFICIENT_RESOURCES:
        requirements = []
        if result.required_agents:
            requirements.append(_format_costs(result.required_agents))
        if result.required_items:
            requirements.append(_format_item_costs(result.required_items))
        await query.answer(
            "Для сделки нужно: " + "; ".join(requirements),
            show_alert=True,
        )
    else:
        await query.answer("Эта сделка недоступна.", show_alert=True)
    return


async def handle_agency(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    chat = update.effective_chat
    service = _service(context)
    if value != "status":
        await query.answer("Некорректный запрос службы.", show_alert=True)
        return
    await query.answer()
    profile = await _profile_for_update(update, service)
    required_reputation = service.settings.agency_reputation_requirement(
        profile.agency_level
    )
    required_agents = service.settings.agency_requirements(profile.agency_level)
    at_cap = profile.agency_level >= service.settings.agency_max_level
    bonus = min(
        profile.agency_level * service.settings.agency_rare_bonus_percent,
        service.settings.agency_max_level * service.settings.agency_rare_bonus_percent,
    )
    blocks = [
        {"type": "paragraph", "text": "🏛 СОБСТВЕННАЯ РАЗВЕДСЛУЖБА"},
        {
            "type": "paragraph",
            "text": (
                f"Текущий уровень: {profile.agency_level}/"
                f"{service.settings.agency_max_level}. "
                f"Постоянный бонус к редкому результату Рекрутера: +{bonus}%."
            ),
        },
        {
            "type": "paragraph",
            "text": (
                "Следующий уровень требует репутацию "
                f"{required_reputation} и: {_format_costs(required_agents)}."
                if not at_cap
                else "Достигнут максимальный уровень службы."
            ),
        },
        {
            "type": "footer",
            "text": (
                "При создании уровня требуемые агенты будут списаны, "
                "а репутация сброшена до 0. Остальные агенты и предметы сохранятся."
            ),
        },
    ]
    markup = None
    if not at_cap:
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Подтвердить создание службы",
                        callback_data=f"spy:agency_found:{profile.agency_level}",
                    )
                ]
            ]
        )
    await _send_rich(
        context,
        chat.id,
        blocks,
        fallback_text=(
            f"Служба уровня {profile.agency_level}. Требуется репутация "
            f"{required_reputation} и {_format_costs(required_agents)}."
        ),
        reply_markup=markup,
    )
    return


async def handle_agency_found(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    try:
        expected_level = int(value)
        if expected_level < 0:
            raise ValueError
    except ValueError:
        await query.answer("Некорректный уровень службы.", show_alert=True)
        return
    try:
        result = await service.found_agency(
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
            expected_agency_level=expected_level,
        )
    except Exception:
        logger.exception("spy_game: agency founding failed user_id=%s", user.id)
        await query.answer(
            "Центр не зарегистрировал службу. Ресурсы не изменены.",
            show_alert=True,
        )
        return
    if result.status is AgencyStatus.SUCCESS:
        await query.answer(
            f"Разведслужба уровня {result.agency_level} создана.",
            show_alert=True,
        )
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.warning("spy_game: agency confirmation keyboard remained")
        await _send_rich(
            context,
            chat.id,
            [
                {"type": "paragraph", "text": "🏛 СЛУЖБА УЧРЕЖДЕНА"},
                {
                    "type": "paragraph",
                    "text": (
                        f"{_public_label(user)} создал разведслужбу уровня "
                        f"{result.agency_level}."
                    ),
                },
            ],
            fallback_text=(
                f"🏛 {_public_label(user)} создал разведслужбу уровня "
                f"{result.agency_level}."
            ),
        )
    elif result.status is AgencyStatus.INSUFFICIENT_RESOURCES:
        await query.answer(
            f"Нужно: репутация {result.required_reputation}; "
            f"{_format_costs(result.required_agents)}.",
            show_alert=True,
        )
    elif result.status is AgencyStatus.STALE:
        await query.answer(
            "Досье изменилось. Откройте условия службы заново.",
            show_alert=True,
        )
    elif result.status is AgencyStatus.MAX_LEVEL:
        await query.answer("Максимальный уровень уже достигнут.", show_alert=True)
    else:
        await query.answer("Создание службы сейчас недоступно.", show_alert=True)
    return


async def handle_equip(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    result = await service.equip_item(
        chat_id=chat.id,
        user_id=user.id,
        item_type=value,
    )
    if result.status is EquipmentStatus.SUCCESS:
        item = ITEM_TYPES[result.item_type]
        await query.answer(
            f"{item.display_name} установлен в слот {result.slot}.",
            show_alert=True,
        )
    elif result.status is EquipmentStatus.NO_FREE_SLOT:
        await query.answer("Все слоты заняты.", show_alert=True)
    elif result.status is EquipmentStatus.ALREADY_EQUIPPED:
        await query.answer("Этот предмет уже экипирован.", show_alert=True)
    else:
        await query.answer("Предмет нельзя экипировать.", show_alert=True)
    return


async def handle_unequip(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    try:
        slot = int(value)
    except ValueError:
        await query.answer("Некорректный слот.", show_alert=True)
        return
    result = await service.unequip_item(
        chat_id=chat.id,
        user_id=user.id,
        slot=slot,
    )
    if result.status is EquipmentStatus.SUCCESS:
        await query.answer("Предмет снят.", show_alert=True)
    else:
        await query.answer("Этот слот уже пуст.", show_alert=True)
    return


async def handle_prestige(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    try:
        expected_reputation = int(value)
        if expected_reputation < 0:
            raise ValueError
    except ValueError:
        await query.answer("Некорректный уровень репутации.", show_alert=True)
        return
    try:
        result = await service.increase_reputation(
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
            expected_reputation=expected_reputation,
        )
    except Exception:
        logger.exception("spy_game: prestige failed user_id=%s", user.id)
        await query.answer(
            "Центр не подтвердил повышение. Попробуйте ещё раз.",
            show_alert=True,
        )
        return
    if result.status is EconomyStatus.SUCCESS:
        await query.answer(
            f"Репутация повышена до {result.reputation}.", show_alert=True
        )
        logger.info(
            "spy_reputation_increased user_id=%s reputation=%s",
            user.id,
            result.reputation,
        )
        await _send_menu(update, context)
    elif result.status is EconomyStatus.INSUFFICIENT_RESOURCES:
        await query.answer(
            "Для повышения нужно: " + _format_costs(result.required),
            show_alert=True,
        )
    elif result.status is EconomyStatus.STALE:
        await query.answer(
            "Досье уже изменилось. Откройте свежее меню.", show_alert=True
        )
    else:
        await query.answer("Повышение сейчас недоступно.", show_alert=True)
    return
