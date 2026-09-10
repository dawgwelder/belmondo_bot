"""Paginated Telegram archive with owner-bound title controls."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .context import _service
from .cleanup import schedule_menu_cleanup


PAGE_SIZE = 6


def archive_page(archive, user_id, page):
    pages = (archive["total"] + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))
    entries = archive["entries"][page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    lines = [
        "🎖 АРХИВ ОСОБЫХ ЗАСЛУГ",
        f"Открыто: {archive['unlocked']}/{archive['total']} · Очки: {archive['points']}",
        f"Титул: {archive['title'] or 'не выбран'}",
        f"Страница {page + 1}/{pages}",
    ]
    buttons = []
    for entry in entries:
        icon = "🆕" if entry["is_new"] else "🎖" if entry["unlocked"] else "🔒"
        progress = (
            ""
            if entry["target"] is None
            else f" · {entry['progress']}/{entry['target']}"
        )
        lines.extend(["", f"{icon} {entry['name']}{progress}", entry["description"]])
        if entry["unlocked"]:
            lines.append(f"Центр: «{entry['note']}»")
            buttons.append(
                [
                    InlineKeyboardButton(
                        ("✓ " if archive["title_id"] == entry["id"] else "Титул: ")
                        + entry["name"],
                        callback_data=f"spy:title:{user_id}_{entry['id']}",
                    )
                ]
            )
    navigation = []
    if page:
        navigation.append(
            InlineKeyboardButton("←", callback_data=f"spy:ach:{user_id}_{page - 1}")
        )
    if page + 1 < pages:
        navigation.append(
            InlineKeyboardButton("→", callback_data=f"spy:ach:{user_id}_{page + 1}")
        )
    if navigation:
        buttons.append(navigation)
    if archive["title_id"]:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Снять титул", callback_data=f"spy:title:{user_id}_none"
                )
            ]
        )
    return "\n".join(lines), InlineKeyboardMarkup(buttons), entries


async def send_archive(update, context, page=0, *, edit=False):
    service = _service(context)
    uid = update.effective_user.id
    archive = await service.get_achievements(uid)
    text, keyboard, entries = archive_page(archive, uid, page)
    if edit:
        # Avoid Telegram's MessageNotModified on repeated callbacks.
        message = update.callback_query.message
        if message.text != text or message.reply_markup != keyboard:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        message = await context.bot.send_message(
            update.effective_chat.id, text, reply_markup=keyboard
        )
    schedule_menu_cleanup(context, update.effective_chat, message.message_id)
    await service.mark_achievements_seen(
        uid, [e["id"] for e in entries if e["unlocked"]]
    )


async def handle_archive(update, context, category, value):
    query = update.callback_query
    service = _service(context)
    if not service.chat_is_available(update.effective_chat.id):
        await query.answer("Игра недоступна в этом чате.", show_alert=True)
        return
    owner, _, argument = value.partition("_")
    if owner != str(update.effective_user.id):
        await query.answer(
            "Это чужое досье. Откройте свой архив через /spy.", show_alert=True
        )
        return
    if category == "title":
        key = None if argument == "none" else argument
        ok = await service.select_achievement_title(update.effective_user.id, key)
        if not ok:
            await query.answer("Этот титул ещё не получен.", show_alert=True)
            return
        archive = await service.get_achievements(update.effective_user.id)
        index = next((i for i, a in enumerate(archive["entries"]) if a["id"] == key), 0)
        page = index // PAGE_SIZE
        await query.answer("Титул обновлён.")
    else:
        if not argument.isdecimal() or len(argument) > 3:
            await query.answer("Неизвестная страница.", show_alert=True)
            return
        page = int(argument)
        await query.answer()
    await send_archive(update, context, page, edit=True)
