"""Composition root for Spy Clicker persistence.

Concrete SQL operations live in persistence/. All repositories use the SQLite
connection supplied by the service, so multi-step settlement stays atomic.
The explicit method aliases preserve existing SpyRepository callers.
"""
from __future__ import annotations

from collections.abc import Callable
from .death_mission_repository import DeathMissionRepository
from .rewards import RewardResolver
from .scheduler import ActivityPolicy, RandomSource
from .settings import SpySettings
from .persistence.base import RepositoryComponent, RepositoryContext, _new_event_id
from .persistence.economy import EconomyRepository
from .persistence.lifecycle import LifecycleRepository
from .persistence.scheduling import SchedulingRepository
from .persistence.recruitment import RecruitmentRepository
from .persistence.dead_drop import DeadDropRepository
from .persistence.death_operation import DeathOperationRepository
from .persistence.intercept import InterceptRepository
from .persistence.find_mole import FindMoleRepository
from .persistence.cooperative import CooperativeRepository
from .persistence.chase import ChaseRepository
from .persistence.contacts import ContactsRepository
from .persistence.progression import ProgressionRepository


class SpyRepository(RepositoryComponent):
    def __init__(
        self,
        settings: SpySettings,
        activity_policy: ActivityPolicy,
        rng: RandomSource,
        reward_resolver: RewardResolver,
        event_id_factory: Callable[[], str] = _new_event_id,
    ) -> None:
        super().__init__(
            RepositoryContext(
                settings, activity_policy, rng, reward_resolver, event_id_factory
            )
        )
        self.economy = EconomyRepository(self.context)
        self.death_mission = DeathMissionRepository(self.economy)
        self.lifecycle = LifecycleRepository(
            self.context, death_mission=self.death_mission
        )
        self.scheduling = SchedulingRepository(self.context, lifecycle=self.lifecycle)
        self.recruitment = RecruitmentRepository(
            self.context, economy=self.economy, lifecycle=self.lifecycle
        )
        self.dead_drop = DeadDropRepository(
            self.context, economy=self.economy, lifecycle=self.lifecycle
        )
        self.death_operation = DeathOperationRepository(
            self.context, economy=self.economy, lifecycle=self.lifecycle
        )
        self.intercept = InterceptRepository(
            self.context, economy=self.economy, lifecycle=self.lifecycle
        )
        self.find_mole = FindMoleRepository(
            self.context, economy=self.economy, lifecycle=self.lifecycle
        )
        self.cooperative = CooperativeRepository(
            self.context, economy=self.economy, lifecycle=self.lifecycle
        )
        self.chase = ChaseRepository(
            self.context, economy=self.economy, lifecycle=self.lifecycle
        )
        self.contacts = ContactsRepository(
            self.context, economy=self.economy, lifecycle=self.lifecycle
        )
        self.progression = ProgressionRepository(self.context, economy=self.economy)

        # Backwards-compatible entry points; new code may use named repositories.
        self.reconcile = self.lifecycle.reconcile
        self.enable_chat = self.lifecycle.enable_chat
        self.disable_chat = self.lifecycle.disable_chat
        self.set_activity_profile = self.scheduling.set_activity_profile
        self.prepare_tick = self.scheduling.prepare_tick
        self.spawn_due = self.scheduling.spawn_due
        self.manual_spawn = self.scheduling.manual_spawn
        self.attach_message = self.lifecycle.attach_message
        self.cancel_publication = self.lifecycle.cancel_publication
        self.claim = self.recruitment.claim
        self.get_recruitment_progress = self.recruitment.get_recruitment_progress
        self.claim_dead_drop = self.dead_drop.claim_dead_drop
        self.start_dead_drop_game = self.dead_drop.start_dead_drop_game
        self.get_dead_drop_game = self.dead_drop.get_dead_drop_game
        self.guess_dead_drop_game = self.dead_drop.guess_dead_drop_game
        self.run_death_operation = self.death_operation.run_death_operation
        self.answer_intercept = self.intercept.answer_intercept
        self.start_intercept_game = self.intercept.start_intercept_game
        self.get_intercept_game = self.intercept.get_intercept_game
        self.finish_intercept_game = self.intercept.finish_intercept_game
        self.html5_event_type = self.lifecycle.html5_event_type
        self.html5_game_type = self.lifecycle.html5_game_type
        self.start_find_mole_game = self.find_mole.start_find_mole_game
        self.get_find_mole_game = self.find_mole.get_find_mole_game
        self.accuse_find_mole_game = self.find_mole.accuse_find_mole_game
        self.accuse_find_mole_event = self.find_mole.accuse_find_mole_event
        self.contribute_cooperative = self.cooperative.contribute_cooperative
        self.advance_chase = self.chase.advance_chase
        self.exchange_with_handler = self.contacts.exchange_with_handler
        self.interact_with_npc = self.contacts.interact_with_npc
        self.exchange_with_contact = self.contacts.exchange_with_contact
        self.increase_reputation = self.progression.increase_reputation
        self.found_agency = self.progression.found_agency
        self.ensure_user_and_profile = self.economy.ensure_user_and_profile
        self.get_agents = self.economy.get_agents
        self.get_inventory = self.economy.get_inventory
        self.get_leaderboard = self.economy.get_leaderboard
        self.equip_item = self.economy.equip_item
        self.unequip_item = self.economy.unequip_item
        self.get_chat_status = self.lifecycle.get_chat_status
