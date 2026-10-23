"""One database upgrade path for both the API and the dedicated worker."""
from sqlalchemy import text
from sqlalchemy.schema import CreateSchema

from app.config import settings
from app.db.session import Base, engine
from app.db.migrations import ensure_tenant_schema


async def initialize_database(database_engine=None):
    import app.models  # Register every table before inspecting legacy schemas.
    database_engine = database_engine or engine
    async with database_engine.begin() as connection:
        if database_engine.dialect.name == "postgresql":
            await connection.execute(text("SELECT pg_advisory_xact_lock(1763000272)"))
            await connection.execute(CreateSchema(settings.DATABASE_SCHEMA, if_not_exists=True))
            schema = settings.DATABASE_SCHEMA
            from app.services.atlas_admission import initialize_admission_schema
            await initialize_admission_schema(connection)
            await connection.execute(text(f'CREATE TABLE IF NOT EXISTS "{schema}".schema_revision (id INTEGER PRIMARY KEY, version VARCHAR(40) NOT NULL)'))
            if await connection.scalar(text(f'SELECT version FROM "{schema}".schema_revision WHERE id=1')) == '2026-10-23-prospecting-v1':
                return
        await connection.run_sync(ensure_tenant_schema)
        await connection.run_sync(Base.metadata.create_all)
        if database_engine.dialect.name == 'postgresql':
            await connection.execute(text(f'INSERT INTO "{schema}".schema_revision (id,version) VALUES (1,\'2026-10-23-prospecting-v1\') ON CONFLICT (id) DO UPDATE SET version=EXCLUDED.version'))
