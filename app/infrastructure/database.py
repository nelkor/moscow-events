from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.domain.catalogs import SCHEMA_VERSION
from app.domain.errors import DomainError
from app.infrastructure.queries import SqlQueries
from app.infrastructure.repository import SqlRepository
from app.infrastructure.seed import initialize_dataset


class Database:
    def __init__(self, settings):
        self.settings = settings
        self.engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            hide_parameters=True,
            connect_args={"connect_timeout": 3},
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        # Миграция и seed защищены одной транзакционной блокировкой для всех процессов.
        with self.engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(74120300)"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
                )
            )
            versions = (
                connection.execute(text("SELECT version FROM schema_migrations")).scalars().all()
            )
            if not versions:
                migration = Path(__file__).with_name("migrations").joinpath(f"{SCHEMA_VERSION}.sql")
                connection.exec_driver_sql(migration.read_text(encoding="utf-8"))
                connection.execute(
                    text("INSERT INTO schema_migrations(version) VALUES (:version)"),
                    {"version": SCHEMA_VERSION},
                )
            elif versions != [SCHEMA_VERSION]:
                raise DomainError("SCHEMA_MISMATCH", "Database schema version is incompatible", 503)
            with Session(bind=connection) as session:
                initialize_dataset(session, self.settings.seed_mode)
                session.flush()
                SqlQueries(session).metadata()

    def ready(self):
        with self.sessions() as session:
            version = session.scalar(text("SELECT version FROM schema_migrations"))
            session.scalar(select(text("postgis_version()")))
            SqlQueries(session).metadata()
            if version != SCHEMA_VERSION:
                raise DomainError("SCHEMA_MISMATCH", "Database schema version is incompatible", 503)

    def uow(self):
        return SqlUnitOfWork(self.sessions())


class SqlUnitOfWork:
    def __init__(self, session):
        self.session = session
        self.repository = SqlRepository(session)
        self.queries = SqlQueries(session)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.session.rollback()
        finally:
            self.session.close()

    def commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()

    def savepoint(self):
        return self.session.begin_nested()
