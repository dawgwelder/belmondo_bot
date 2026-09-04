"""Shared Russian copy and Telegram fallback for Death Mission."""

import asyncio
import logging
from datetime import timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from .death_mission_repository import TERMINAL, iso


logger = logging.getLogger("Belmondo Logger")
ERRORS = {
    "STALE_STAKE": "Состав изменился. Проверьте новую ставку и подтвердите заново.",
    "STALE_REVISION": "Состояние изменилось в другом окне. Экран обновлён.",
    "IDEMPOTENCY_CONFLICT": "Этот ключ уже использован для другого действия.",
    "RUN_IN_PROGRESS": "Вы уже отправили сеть на операцию в другом чате.",
    "LOST_RACE": "Другой агент уже начал операцию.",
    "INSUFFICIENT_AGENTS": "Нет доступных агентов для ставки.",
    "CONFIRMATION_EXPIRED": "Время подтверждения истекло. Выберите режим заново.",
    "INVALID_ACTION": "Это действие сейчас недоступно.",
    "EXTRACTION_LOCKED": "Эвакуация пока не открыта.",
    "ALREADY_FINISHED": "Операция уже завершена.",
}
OUTCOMES = {
    "won": "Операция выполнена",
    "lost": "Связь потеряна",
    "extracted": "Аварийная эвакуация",
    "timed_out": "Время операции истекло",
    "cancelled_refunded": "Операция отменена. Ставка возвращена",
    "expired": "Вход в операцию закрыт",
    "lost_race": "Операцию занял другой агент",
}


def bundle_text(bundle):
    return (
        ", ".join(f"{a['emoji']} {a['name']} ×{a['amount']}" for a in bundle) or "нет"
    )


def action_text(action):
    if "cost" not in action:
        return action.get("description", "")
    details = []
    if action["cost"]:
        details.append(f"разведданные −{action['cost']}")
    for key, name in (
        ("hp", "состояние"),
        ("intel", "разведданные"),
        ("alarm", "тревога"),
    ):
        if action[key]:
            details.append(f"{name} {action[key]:+d}")
    if action["risk"]:
        details.append(
            f"осложнение {action['risk']}%: урон {action['damage']}, тревога +1"
        )
    return "; ".join(details) or "Без изменения ресурсов"


def text(payload):
    status = payload["status"]
    if status in TERMINAL:
        result = payload.get("result", {})
        lines = [OUTCOMES[status]]
        if result:
            lines += [
                "Возвращено: " + bundle_text(result["returned"]),
                "Бонус: " + bundle_text(result["bonus"]),
            ]
        if payload.get("mission", {}).get("log"):
            lines += ["Последние решения:"] + payload["mission"]["log"][-3:]
        if payload.get("progress"):
            lines.append(
                "Архив: контрольных точек — "
                + str(payload["progress"].get("checkpoint", 0))
            )
        return "\n".join(lines)
    if status not in {"preview", "armed", "in_run"}:
        return "Операция недоступна. Откройте её из сообщения бота."
    if status in {"preview", "armed"}:
        rules = payload["rules"]
        lines = [
            "СМЕРТЕЛЬНАЯ ОПЕРАЦИЯ",
            "На кону вся доступная сеть:",
            bundle_text(payload["stake"]),
        ]
        if status == "preview":
            lines += [
                f"All-in: мгновенный исход, успех {rules['all_in_percent']}%. "
                f"При успехе сеть ×{rules['multiplier']} и Tier 3 ×1.",
                f"Личная миссия: 5 узлов и финальный объект. Победа: сеть ×{rules['multiplier']} "
                "и выбранный бонус — Tier 3 ×2 или Tier 4 ×1.",
                "После узла 3 можно эвакуировать половину каждого типа (округление вниз). "
                "При гибели ставка теряется. Закрытие окна не останавливает миссию.",
            ]
        else:
            mode = (
                "Мгновенный all-in" if payload["mode"] == "all_in" else "Личная миссия"
            )
            lines += [
                mode,
                "Подтверждение отправляет сеть на задание. Назад вернуть ставку нельзя.",
            ]
            if payload["mode"] == "mission":
                lines += [
                    "Бонус финала: "
                    + ("Tier 3 ×2" if payload["bonus"] == "tier3" else "Tier 4 ×1"),
                    f"Срок: {rules['seconds'] // 60} мин. На таймауте — половина ставки, "
                    "если эвакуация открыта; иначе 0.",
                ]
        lines.append("Эвакуация вернёт: " + bundle_text(payload["extraction"]))
        return "\n\n".join(lines)
    mission = payload["mission"]
    lines = [
        mission["title"],
        f"Узел {min(6, mission['node'] + 1)}/6 · "
        f"Состояние {mission['hp']}/6 · Разведданные {mission['intel']}/6 · Тревога {mission['alarm']}/6",
        "Модули: " + (", ".join(mission["module_names"]) or "нет"),
        "Тревога 6: облава, урон 2 и тревога 4. Модули изменяют базовые эффекты ниже.",
    ]
    for action in mission["actions"]:
        lines.append(action["label"] + ": " + action_text(action))
    if mission["checkpoint"]:
        lines.append("Эвакуация: " + bundle_text(payload["extraction"]))
    if mission["log"]:
        lines.append(mission["log"][-1])
    return "\n\n".join(lines)


def keyboard(payload, run_id, event_id=None):
    revision = payload["revision"]

    def button(label, code):
        data = f"spy:mission:{run_id}.{revision}.{code}"
        if len(data.encode()) > 64:
            raise ValueError("mission callback exceeds Telegram limit")
        return [InlineKeyboardButton(label, callback_data=data)]

    rows = []
    if payload["status"] == "preview":
        rows += [button("🎲 All-in — без личного прохождения", "a")]
        for tactic in payload["tactics"]:
            code = tactic["id"][0]
            rows += [
                button(f"Миссия · {tactic['name']} · Tier 3 ×2", f"m3{code}"),
                button(f"Миссия · {tactic['name']} · Tier 4 ×1", f"m4{code}"),
            ]
    elif payload["status"] == "armed":
        rows += [
            button("Подтвердить ставку и начать", "commit"),
            button("Назад к выбору", "back"),
        ]
    elif payload["status"] == "in_run":
        for action in payload["mission"]["actions"]:
            if action.get("enabled", True):
                rows += [button(action["label"], "do_" + action["id"])]
        if payload["mission"]["checkpoint"]:
            rows += [button("Эвакуация: показать подтверждение", "askextract")]
        else:
            rows += [button("Сдаться: показать подтверждение", "askabandon")]
    if event_id and payload["status"] in {"preview", "armed"}:
        rows += [
            [
                InlineKeyboardButton(
                    "Открыть выбор для себя", callback_data=f"spy:deathmenu:{event_id}"
                )
            ]
        ]
    return InlineKeyboardMarkup(rows) if rows else None


def decode(code):
    if code == "a":
        return "arm", dict(mode="all_in", tactic="balanced", bonus="tier3")
    if len(code) == 3 and code[0] == "m" and code[1] in "34" and code[2] in "bsa":
        return "arm", dict(
            mode="mission",
            tactic={"b": "balanced", "s": "stealth", "a": "assault"}[code[2]],
            bonus="tier" + code[1],
        )
    if code.startswith("do_"):
        return "action", dict(id=code[3:])
    return code, {}


def delivery_lock(service):
    if not hasattr(service, "_death_outbox_lock"):
        service._death_outbox_lock = asyncio.Lock()
    return service._death_outbox_lock


async def publish_pending(service, bot):
    # The latest view and Telegram edits are serialized with callback rendering.
    async with delivery_lock(service):
        rows = await service.database.read(
            service.repository.death_mission.pending_results
        )
        for row in rows:
            if row["message_id"] is None:
                continue
            from .service import utc_now

            # Back off failed/deleted messages without starving newer results.
            await service.database.transaction(
                lambda connection, run_id=row["run_id"]: connection.execute(
                    "UPDATE death_mission_outbox SET attempts=attempts+1, next_attempt_at=? WHERE run_id=?",
                    (iso(utc_now() + timedelta(seconds=60)), run_id),
                ),
                immediate=True,
            )
            label = (
                "@" + row["username"].lstrip("@")
                if row["username"]
                else "Скрытый агент"
            )
            try:
                # Idempotent edit survives a crash after Telegram accepted the request.
                await bot.edit_message_text(
                    chat_id=row["chat_id"],
                    message_id=row["message_id"],
                    text=label + "\n" + text(row["payload"]),
                    reply_markup=None,
                )
            except BadRequest as error:
                if "message is not modified" not in str(error).lower():
                    logger.warning("death_result_edit_failed run_id=%s", row["run_id"])
                    continue
            except Exception:
                logger.warning("death_result_edit_failed run_id=%s", row["run_id"])
                continue
            await service.database.transaction(
                lambda connection, run_id=row["run_id"]: connection.execute(
                    "UPDATE death_mission_outbox SET delivered_at=? WHERE run_id=?",
                    (iso(utc_now()), run_id),
                ),
                immediate=True,
            )
