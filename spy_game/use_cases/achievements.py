"""Personal archive actions; identity is supplied by authenticated adapters."""
from .base import UseCases


class AchievementsUseCases(UseCases):
    def __init__(self, context, achievements):
        super().__init__(context)
        self.achievements = achievements

    async def get_achievements(self, user_id):
        def read(connection):
            self.achievements.drain(connection)
            return self.achievements.archive(connection, user_id)

        return await self.database.transaction(read, immediate=True)

    async def select_title(self, user_id, achievement_id):
        if not self.settings.enabled:
            return False
        return await self.database.transaction(
            lambda c: self.achievements.select_title(c, user_id, achievement_id),
            immediate=True,
        )

    async def mark_seen(self, user_id, achievement_ids):
        if not self.settings.enabled:
            return
        await self.database.transaction(
            lambda c: self.achievements.mark_seen(c, user_id, achievement_ids),
            immediate=True,
        )
