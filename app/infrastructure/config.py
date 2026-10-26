import os
from dataclasses import dataclass, field

from sqlalchemy.engine import URL


def pg_port_from_env():
    try:
        return int(os.getenv("PG_PORT", "5432"))
    except ValueError:
        raise ValueError("PG_PORT must be an integer between 1 and 65535") from None


@dataclass(frozen=True)
class Settings:
    pg_host: str = field(default_factory=lambda: os.getenv("PG_HOST", "db"))
    pg_port: int = field(default_factory=pg_port_from_env)
    pg_db_name: str = field(default_factory=lambda: os.getenv("PG_DB_NAME", ""))
    pg_user_name: str = field(default_factory=lambda: os.getenv("PG_USER_NAME", ""))
    pg_user_password: str = field(
        default_factory=lambda: os.getenv("PG_USER_PASSWORD", ""), repr=False
    )
    seed_mode: str = field(default_factory=lambda: os.getenv("SEED_MODE", "sample"))
    retry_seconds: float = 2.0

    def __post_init__(self):
        missing = [
            name
            for name, value in (
                ("PG_DB_NAME", self.pg_db_name),
                ("PG_USER_NAME", self.pg_user_name),
                ("PG_USER_PASSWORD", self.pg_user_password),
            )
            if not value
        ]
        if missing:
            raise ValueError("Required PostgreSQL settings are missing or empty: " + ", ".join(missing))
        if not self.pg_host.strip():
            raise ValueError("PG_HOST must not be empty")
        if not 1 <= self.pg_port <= 65535:
            raise ValueError("PG_PORT must be an integer between 1 and 65535")
        if self.seed_mode not in {"sample", "empty"}:
            raise ValueError("SEED_MODE must be sample or empty")

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            host=self.pg_host,
            port=self.pg_port,
            database=self.pg_db_name,
            username=self.pg_user_name,
            password=self.pg_user_password,
        )
