"""Read-only listings of what the database holds, for the UI's left-hand cabinet."""

from .clients.postgres_client import PostgresClient


RECENT_INGESTIONS = 20


class CatalogService:
    def __init__(self, db: PostgresClient):
        self.db = db

    async def theses(self) -> list[dict]:
        return await self.db.list_theses()

    async def podcasts(self) -> list[dict]:
        return await self.db.list_watched_podcasts()

    async def subscriptions(self) -> list[dict]:
        return await self.db.list_subscriptions()

    async def ingestions(self) -> list[dict]:
        return await self.db.recent_ingestions(RECENT_INGESTIONS)
