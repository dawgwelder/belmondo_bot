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
    "DISABLED": "Игра отключена. Зарезервированный отряд возвращён.",
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
    return ", ".join(f"{a['emoji']} {a['name']} ×{a['amount']}" for a in bundle) or "нет"


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
        alarm = "без тревоги" if action.get("complication_alarm") == 0 else f"{ALARM} +1"
        details.append(f"⚠️ осложнение {action['risk']}%: базовый урон {action['damage']}, {alarm}")
    if action.get("specialist"):
        details.append(f"специалист: риск {action['base_risk']}% → {action['risk']}%, 1 заряд")
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
                f"▫️ {action['label']} — {action['description']}" f" · финал {view['odds']}% → {action['odds']}%"
            )
        elif not action["enabled"]:
            lines.append(f"▪️ {action['label']} — недоступно: нужно {INTEL} {action['cost']}")
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


def bonuses(payload):
    return payload.get("bonuses") or [
        dict(id=key, name=name, locked=False, description="") for key, name in BONUS_LABELS.items()
    ]


def bonus_name(payload):
    return next((b["name"] for b in bonuses(payload) if b["id"] == payload["bonus"]), payload["bonus"])


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
            if check := result.get("check"):
                lines.append(f"Бросок: {check['roll']}/100 · для успеха нужно больше {check['risk']}.")
        mission = payload.get("mission") or {}
        if mission.get("log"):
            lines += ["Последние решения:"] + ["• " + line for line in mission["log"][-3:]]
        if payload.get("progress"):
            lines.append("Архив: контрольных точек — " + str(payload["progress"].get("checkpoint", 0)))
        return "\n".join(lines)
    if status not in {"preview", "armed", "in_run"}:
        return "Операция недоступна. Откройте её из сообщения бота."
    if status in {"preview", "armed"}:
        rules = payload["rules"]
        lines = [
            "СМЕРТЕЛЬНАЯ ОПЕРАЦИЯ",
            "На кону вся доступная сеть:\n" + bundle_text(payload["stake"]),
        ]
        if payload.get("victory") is not None and (status == "preview" or payload.get("mode") != "all_in"):
            lines.append("✓ При победе вернётся: " + bundle_text(payload["victory"]))
            lines.extend(
                f"{agent['emoji']} {agent['name']}: {agent['ability']}, −{agent['reduction']} п.п., 1 заряд."
                for agent in payload.get("specialists", [])
            )
        if status == "preview":
            lines.append(
                f"🎲 All-in: мгновенный исход, успех {rules['all_in_percent']}%. "
                f"При успехе сеть ×{rules['multiplier']} и Tier 3 ×1.\n"
                + (
                    "🕵️ Личная миссия: на кону вся доступная сеть. Победа возвращает её "
                    "и по одному агенту за каждые пять одного типа.\n"
                    if rules.get("version") == "roguelite_v4"
                    else f"🕵️ Личная миссия: 5 узлов и финальный объект. Победа: сеть ×{rules['multiplier']}.\n"
                )
                + "Доступные бонусы зависят от состава ставки и показаны перед стартом.\n"
                "Эвакуация после узла 3 вернёт половину каждого типа (округление "
                "вниз). При гибели ставка теряется. Закрытие окна не останавливает миссию."
            )
            tactic = selected_tactic(payload, nav)
            if tactic:
                lines.append(f"Тактика: {tactic_line(tactic)}\nВыберите бонус за полное прохождение:")
                lines.append(
                    "\n".join(f"{'🔒 ' if b['locked'] else ''}{b['name']}: {b['description']}" for b in bonuses(payload))
                )
            elif nav == "ui_m":
                lines.append(
                    "Выберите стартовую тактику:\n" + "\n".join("• " + tactic_line(t) for t in payload["tactics"])
                )
        else:
            mode = "Мгновенный all-in" if payload["mode"] == "all_in" else "Личная миссия"
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
                    + bonus_name(payload),
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
    if view.get("raid_hint"):
        header.append(view["raid_hint"])
    for specialist in view.get("specialists", []):
        header.append(
            f"{specialist['emoji']} {specialist['name']}: " + ("1 заряд" if specialist["ready"] else "заряд потрачен")
        )
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
            "🪂 " + bundle_text(payload["extraction"]) + " (гарантированно).\n" + continuation_text(payload)
        )
        return "\n\n".join(lines)
    if nav == "askabandon":
        lines.append("Сдаться и потерять всю ставку?")
        return "\n\n".join(lines)
    if view["phase"] == "challenge":
        challenge = view["challenge"]
        cells = [
            "⚡"
            if cell == challenge["path"][-1]
            else "❌"
            if cell in challenge["blocked"]
            else "🏁"
            if cell == challenge["goal"]
            else "🟩"
            if cell in challenge["path"]
            else "⬜"
            for cell in range(16)
        ]
        lines.append("\n".join(" ".join(cells[i : i + 4]) for i in range(0, 16, 4)))
        lines.append(
            f"Проведите сигнал ⚡ к 🏁 соседними клетками, обходя ❌. Ходов: {challenge['moves_left']}. "
            "Успех: +1 разведданное. Пропуск без штрафа."
        )
    else:
        lines.append("Варианты:\n" + "\n".join(option_lines(view)))
    if view["phase"] == "module":
        lines.append(
            "Проценты оценивают только финал сейчас. Польза модуля на оставшихся узлах и ранняя эвакуация в них не учтены."
        )
    if view["checkpoint"]:
        lines.append(
            "🪂 Эвакуация вернёт: "
            + bundle_text(payload["extraction"])
            + " (гарантированно).\n"
            + continuation_text(payload)
        )
    else:
        lines.append(f"Эвакуация откроется после узла {view['checkpoint_node']}.")
    return "\n\n".join(lines)


def continuation_text(payload):
    view = payload["mission"]
    reward = "При победе: " + (
        bundle_text(payload["victory"])
        if payload.get("victory") is not None
        else f"сеть ×{payload['rules']['multiplier']}"
    )
    reward += ". " if payload["bonus"] == "none" else " и выбранный бонус. "
    if view["phase"] == "boss":
        return reward + f"Шанс пройти оставшиеся фазы финала при лучших решениях: {view['odds']}%."
    return reward + (
        "Шанс всего оставшегося маршрута не рассчитан. "
        f"Оценка {view['odds']}% относится только к финалу с текущими ресурсами; "
        "комнаты впереди изменят их."
    )


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
            for bonus in bonuses(payload):
                if not bonus["locked"]:
                    bonus_code = {"tier3": "3", "tier4": "4", "none": "n"}[bonus["id"]]
                    rows += [button(f"Бонус: {bonus['name']}", f"m{bonus_code}{code}")]
            rows += [button("← К тактикам", "ui_m")]
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
        rows += [[InlineKeyboardButton("Открыть выбор для себя", callback_data=f"spy:deathmenu:{event_id}")]]
    return InlineKeyboardMarkup(rows) if rows else None


def decode(code):
    if code == "a":
        return "arm", dict(mode="all_in", tactic="balanced", bonus="tier3")
    if len(code) == 3 and code[0] == "m" and code[1] in "34n" and code[2] in "bsa":
        return "arm", dict(
            mode="mission",
            tactic={"b": "balanced", "s": "stealth", "a": "assault"}[code[2]],
            bonus="none" if code[1] == "n" else "tier" + code[1],
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
        rows = await service.database.read(service.repository.death_mission.pending_results)
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
            label = "@" + row["username"].lstrip("@") if row["username"] else "Скрытый агент"
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
