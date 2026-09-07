"""Spy Clicker economy use cases and transaction boundaries."""
from __future__ import annotations

import re
from datetime import datetime
from ..models import (
    AgencyResult,
    AgencyStatus,
    AgentHolding,
    ContactExchangeResult,
    EconomyStatus,
    EquipmentResult,
    EquipmentStatus,
    Inventory,
    LeaderboardEntry,
    NpcStatus,
    PrestigeResult,
    Profile,
)
from .base import UseCases, utc_now


class EconomyUseCases(UseCases):
    async def exchange_with_contact(
        self,
        *,
        operation_id: str,
        recipe_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> ContactExchangeResult:
        if not self.chat_is_available(chat_id):
            return ContactExchangeResult(
                NpcStatus.DISABLED,
                operation_id,
                recipe_id,
            )
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id):
            raise ValueError("contact operation ID is invalid")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.contacts.exchange_with_contact(
                connection,
                operation_id,
                recipe_id,
                chat_id,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def increase_reputation(
        self,
        *,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        expected_reputation: int,
        now: datetime | None = None,
    ) -> PrestigeResult:
        if not self.chat_is_available(chat_id):
            return PrestigeResult(EconomyStatus.DISABLED, expected_reputation)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.progression.increase_reputation(
                connection,
                chat_id,
                user_id,
                username,
                display_name,
                expected_reputation,
                current,
            ),
            immediate=True,
        )

    async def found_agency(
        self,
        *,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        expected_agency_level: int,
        now: datetime | None = None,
    ) -> AgencyResult:
        required_reputation = self.settings.agency_reputation_requirement(
            expected_agency_level
        )
        required_agents = self.settings.agency_requirements(expected_agency_level)
        if not self.chat_is_available(chat_id):
            return AgencyResult(
                AgencyStatus.DISABLED,
                expected_agency_level,
                required_reputation,
                required_agents,
            )
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.progression.found_agency(
                connection,
                chat_id,
                user_id,
                username,
                display_name,
                expected_agency_level,
                current,
            ),
            immediate=True,
        )

    async def get_profile(
        self,
        *,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> Profile:
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.economy.ensure_user_and_profile(
                connection,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def get_agents(self, user_id: int) -> tuple[AgentHolding, ...]:
        return await self.database.read(
            lambda connection: self.repository.economy.get_agents(connection, user_id)
        )

    async def get_inventory(self, user_id: int) -> Inventory:
        return await self.database.read(
            lambda connection: self.repository.economy.get_inventory(
                connection, user_id
            )
        )

    async def get_leaderboard(self, limit: int = 10) -> tuple[LeaderboardEntry, ...]:
        bounded_limit = max(1, min(limit, 25))
        return await self.database.read(
            lambda connection: self.repository.economy.get_leaderboard(
                connection,
                bounded_limit,
            )
        )

    async def equip_item(
        self,
        *,
        chat_id: int,
        user_id: int,
        item_type: str,
    ) -> EquipmentResult:
        if not self.chat_is_available(chat_id):
            return EquipmentResult(EquipmentStatus.DISABLED, item_type)
        return await self.database.transaction(
            lambda connection: self.repository.economy.equip_item(
                connection,
                chat_id,
                user_id,
                item_type,
            ),
            immediate=True,
        )

    async def unequip_item(
        self,
        *,
        chat_id: int,
        user_id: int,
        slot: int,
    ) -> EquipmentResult:
        if not self.chat_is_available(chat_id):
            return EquipmentResult(EquipmentStatus.DISABLED, slot=slot)
        return await self.database.transaction(
            lambda connection: self.repository.economy.unequip_item(
                connection,
                chat_id,
                user_id,
                slot,
            ),
            immediate=True,
        )
