"""Permanent mini-game, independent of event creation and resolution."""
import re

from ..slots import STAKES, rules_payload
from .base import UseCases, utc_now


class SlotsUseCases(UseCases):
    async def slot_state(self, user_id, chat_id):
        return {
            **rules_payload(),
            "storage_key": f"spy-slots:{user_id}:{chat_id}",
            "history": await self.database.read(
                lambda c: self.repository.slots.history(c, user_id)
            ),
        }

    async def spin_slots(
        self,
        *,
        operation_id,
        stake,
        chat_id,
        user_id,
        username=None,
        display_name=None,
        now=None,
    ):
        if not isinstance(operation_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", operation_id
        ):
            raise ValueError("Некорректный идентификатор вращения")
        if type(stake) is not int or stake not in STAKES:
            raise ValueError("Ставка: 1, 3 или 5 осведомителей")
        if not self.chat_is_available(chat_id):
            return {"ok": False, "status": "disabled"}
        return await self.database.transaction(
            lambda c: self.repository.slots.spin(
                c,
                user_id=user_id,
                chat_id=chat_id,
                username=username,
                display_name=display_name,
                operation_id=operation_id,
                stake=stake,
                now=now or utc_now(),
            ),
            immediate=True,
        )
