"""Shared Russian copy and Telegram fallback for Death Mission.

Telegram has no HUD, so the message text carries the same structured data the
HTML5 client renders: resources, finale odds, both branches of every action
and the last move with its deltas. Navigation codes (``ui_*``, ``ask*``) only
change the rendered keyboard; they never reach the repository.
"""

import asyncio
import logging
from datetime import timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from .death_mission import signed
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
TACTIC_CODES = {"balanced": "b", "stealth": "s", "assault": "a"}
BONUS_LABELS = {"tier3": "Два агента Tier 3", "tier4": "Один агент Tier 4"}
HP, INTEL, ALARM = "❤️", "🧠", "🚨"


def is_navigation(code):
    return code in {"askextract", "askabandon"} or code.startswith("ui_")


def bundle_text(bundle):
    return (
        ", ".join(f"{a['emoji']} {a['name']} ×{a['amount']}" for a in bundle) or "нет"
    )


def resources(view):
    return (
        f"{HP} Состояние {view['hp']}/{view['max_hp']} · "
        f"{INTEL} Разведданные {view['intel']}/{view['max_intel']} · "
        f"{ALARM} Тревога {view['alarm']}/{view['raid_alarm']}"
    )


def branch_text(branch):
    text = f"{HP}{branch['hp']} {INTEL}{branch['intel']} {ALARM}{branch['alarm']}"
    notes = []
    if branch["dead"]:
        notes.append("☠️ гибель")
    if branch["raid"]:
        notes.append("облава")
    if branch["passport"]:
        notes.append("пропуск")
    if branch["absorbed"]:
        notes.append(f"защита {branch['absorbed']}")
    if branch["medic"]:
        notes.append("медик +1")
    return text + (" " + ", ".join(notes) if notes else "")


def action_text(action):
    """Base effects on one line; both previewed branches on the next."""
    if "cost" not in action:
        return action.get("description", "")
    details = []
    if action["cost"]:
        details.append(f"{INTEL} −{action['cost']}")
    for key, glyph in (("hp", HP), ("intel", INTEL), ("alarm", ALARM)):
        if action[key]:
            details.append(f"{glyph} {signed(action[key])}")
    if action["risk"]:
        details.append(
            f"⚠️ осложнение {action['risk']}%: {HP} −{action['damage']}, {ALARM} +1"
        )
    line = " · ".join(details) or "без изменения ресурсов"
    preview = action.get("preview")
    if preview:
        line += "\n   → " + branch_text(preview["success"])
        if preview["failure"]:
            line += " | осложнение → " + branch_text(preview["failure"])
    return line


def option_lines(view):
    lines = []
    for action in view["actions"]:
        odds = "" if action.get("odds") is None else f" · финал {action['odds']}%"
        if view["phase"] == "room":
            lines.append(f"▫️ {action['label']} — " + " / ".join(action["options"]))
        elif view["phase"] == "module":
            lines.append(
                f"▫️ {action['label']} — {action['description']}"
                f" · финал {view['odds']}% → {action['odds']}%"
            )
        elif not action["enabled"]:
            lines.append(
                f"▪️ {action['label']} — недоступно: нужно {INTEL} {action['cost']}"
            )
        else:
            lines.append(f"▫️ {action['label']}{odds}\n   {action_text(action)}")
    return lines


def tactic_line(tactic):
    extra = []
    if tactic.get("risk_modifier"):
        extra.append(f"риск {signed(tactic['risk_modifier'])} п.п.")
    if tactic.get("shield"):
        extra.append("первый урон −1")
    text = f"{tactic['name']}: {HP}{tactic['hp']} {INTEL}{tactic['intel']}"
    if extra:
        text += " · " + ", ".join(extra)
    if tactic.get("locked"):
        text = f"🔒 {text} — {tactic.get('unlock') or 'закрыто'}"
    return text


def selected_tactic(payload, nav):
    if nav and nav.startswith("ui_t"):
        wanted = {v: k for k, v in TACTIC_CODES.items()}.get(nav[4:])
        return next((t for t in payload["tactics"] if t["id"] == wanted), None)
    return None


def text(payload, nav=None):
    status = payload["status"]
    if status in TERMINAL:
        result = payload.get("result", {})
        lines = [OUTCOMES[status]]
        if result:
            lines += [
                "Возвращено: " + bundle_text(result["returned"]),
                "Бонус: " + bundle_text(result["bonus"]),
            ]
        mission = payload.get("mission") or {}
        if mission.get("log"):
            lines += ["Последние решения:"] + [
                "• " + line for line in mission["log"][-3:]
            ]
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
            "На кону вся доступная сеть:\n" + bundle_text(payload["stake"]),
        ]
        if status == "preview":
            lines.append(
                f"🎲 All-in: мгновенный исход, успех {rules['all_in_percent']}%. "
                f"При успехе сеть ×{rules['multiplier']} и Tier 3 ×1.\n"
                f"🕵️ Личная миссия: 5 узлов и финальный объект. Победа: сеть "
                f"×{rules['multiplier']} и выбранный бонус — Tier 3 ×2 или Tier 4 ×1.\n"
                "Эвакуация после узла 3 вернёт половину каждого типа (округление "
                "вниз). При гибели ставка теряется. Закрытие окна не останавливает миссию."
            )
            tactic = selected_tactic(payload, nav)
            if tactic:
                lines.append(
                    f"Тактика: {tactic_line(tactic)}\nВыберите бонус за полное прохождение:"
                )
            elif nav == "ui_m":
                lines.append(
                    "Выберите стартовую тактику:\n"
                    + "\n".join("• " + tactic_line(t) for t in payload["tactics"])
                )
        else:
            mode = (
                "Мгновенный all-in" if payload["mode"] == "all_in" else "Личная миссия"
            )
            lines += [
                mode,
                "Подтверждение отправляет сеть на задание. Назад вернуть ставку нельзя.",
            ]
            if payload["mode"] == "mission":
                tactic = next(
                    (t for t in payload["tactics"] if t["id"] == payload["tactic"]),
                    None,
                )
                lines += [
                    ("Тактика: " + tactic_line(tactic) + "\n" if tactic else "")
                    + "Бонус финала: "
                    + BONUS_LABELS.get(payload["bonus"], payload["bonus"]),
                    f"Срок: {rules['seconds'] // 60} мин. На таймауте — половина ставки, "
                    "если эвакуация открыта; иначе 0.",
                ]
        lines.append("🪂 Эвакуация вернёт: " + bundle_text(payload["extraction"]))
        return "\n\n".join(lines)
    return mission_text(payload, nav)


def mission_text(payload, nav=None):
    view = payload["mission"]
    header = [
        view["title"],
        f"Узел {min(6, view['node'] + 1)}/6 · Финал: {view['boss']}",
        resources(view),
        "Модули: " + (", ".join(view["module_names"]) or "нет"),
    ]
    if view.get("odds") is not None:
        header.append(
            f"🎯 Шанс пройти финал при текущих ресурсах: {view['odds']}%"
            + ("" if view["phase"] == "boss" else " (если войти сейчас)")
        )
    lines = ["\n".join(header)]
    if view["log"]:
        lines.append("Последний ход:\n• " + view["log"][-1])
    if nav == "askextract":
        lines.append(
            "Завершить миссию и вернуть указанный состав?\n"
            "🪂 " + bundle_text(payload["extraction"])
        )
        return "\n\n".join(lines)
    if nav == "askabandon":
        lines.append("Сдаться и потерять всю ставку?")
        return "\n\n".join(lines)
    lines.append("Варианты:\n" + "\n".join(option_lines(view)))
    if view["checkpoint"]:
        lines.append(
            "🪂 Эвакуация вернёт: "
            + bundle_text(payload["extraction"])
            + f" (гарантированно). Продолжение: ~{view['odds']}% на ×"
            f"{payload['rules']['multiplier']} и бонус."
        )
    else:
        lines.append(f"Эвакуация откроется после узла {view['checkpoint_node']}.")
    return "\n\n".join(lines)


def keyboard(payload, run_id, event_id=None, nav=None):
    revision = payload["revision"]

    def button(label, code):
        data = f"spy:mission:{run_id}.{revision}.{code}"
        if len(data.encode()) > 64:
            raise ValueError("mission callback exceeds Telegram limit")
        return [InlineKeyboardButton(label, callback_data=data)]

    rows = []
    status = payload["status"]
    if status == "preview":
        tactic = selected_tactic(payload, nav)
        if tactic:
            code = TACTIC_CODES[tactic["id"]]
            rows += [
                button(f"Бонус: {BONUS_LABELS['tier3']}", f"m3{code}"),
                button(f"Бонус: {BONUS_LABELS['tier4']}", f"m4{code}"),
                button("← К тактикам", "ui_m"),
            ]
        elif nav == "ui_m":
            for t in payload["tactics"]:
                if not t.get("locked"):
                    rows += [
                        button(
                            f"{t['name']} · {HP}{t['hp']} {INTEL}{t['intel']}",
                            f"ui_t{TACTIC_CODES[t['id']]}",
                        )
                    ]
            rows += [button("← Назад", "ui_root")]
        else:
            rows += [
                button("🎲 All-in — без личного прохождения", "a"),
                button("🕵️ Личная миссия — выбрать тактику", "ui_m"),
            ]
    elif status == "armed":
        rows += [
            button("Подтвердить ставку и начать", "commit"),
            button("Назад к выбору", "back"),
        ]
    elif status == "in_run":
        view = payload["mission"]
        if nav == "askextract" and view["checkpoint"]:
            rows += [
                button("Подтвердить эвакуацию", "extract"),
                button("Продолжить миссию", "ui_root"),
            ]
        elif nav == "askabandon":
            rows += [
                button("Подтвердить: сдаться", "abandon"),
                button("Продолжить миссию", "ui_root"),
            ]
        else:
            for action in view["actions"]:
                if not action.get("enabled", True):
                    continue
                label = action["label"]
                if view["phase"] in {"action", "boss", "module"} and action.get("odds") is not None:
                    label += f" · {action['odds']}%"
                rows += [button(label, "do_" + action["id"])]
            if view["checkpoint"]:
                rows += [button("🪂 Эвакуироваться", "askextract")]
            else:
                rows += [button("Сдаться", "askabandon")]
    if event_id and status in {"preview", "armed"}:
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
