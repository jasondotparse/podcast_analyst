"""PostgreSQL migrations and the embedded LadybugDB graph."""

from pathlib import Path

import psycopg
import real_ladybug as ladybug


HERE = Path(__file__).resolve().parent


async def migrate_postgres(url: str) -> list[str]:
    """Apply pending numbered migrations, each in its own transaction."""
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                           "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        cursor = await conn.execute("SELECT version FROM schema_migrations")
        applied = {version for (version,) in await cursor.fetchall()}
        pending = [path for path in sorted((HERE / "migrations").glob("*.sql")) if path.stem not in applied]
        for path in pending:
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
        return [path.stem for path in pending]


def open_graph(path: Path) -> ladybug.Database:
    """Open the graph (exclusively locked by this process) and ensure its schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # The default buffer pool is 80% of RAM; the 4 GiB host also runs Postgres and Codex.
    database = ladybug.Database(str(path), buffer_pool_size=256 * 1024 * 1024)
    ladybug.Connection(database).execute((HERE / "graph_schema.cypher").read_text())
    return database
