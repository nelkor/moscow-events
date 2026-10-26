from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import UserDefinedType


class Geography(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "geography(Point,4326)"


class Geometry(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "geometry"


metadata = MetaData()


def audit():
    return [
        Column("id", Uuid, primary_key=True),
        Column("created_at", DateTime(timezone=True)),
        Column("updated_at", DateTime(timezone=True)),
    ]


artists = Table(
    "artists",
    metadata,
    *audit(),
    Column("name", String(200)),
    Column("description", Text),
    Column("primary_genre_code", String),
    Column("image_url", Text),
)
venues = Table(
    "venues",
    metadata,
    *audit(),
    Column("name", String(200)),
    Column("city", String),
    Column("district_code", String),
    Column("street", String),
    Column("house", String),
    Column("formatted_address", String),
    Column("location", Geography),
    Column("capacity", Integer),
    Column("description", Text),
)
series = Table(
    "series", metadata, *audit(), Column("name", String(200)), Column("description", Text)
)
events = Table(
    "events",
    metadata,
    *audit(),
    Column("title", String(250)),
    Column("description", Text),
    Column("artist_id", Uuid),
    Column("venue_id", Uuid),
    Column("series_id", Uuid),
    Column("genre_code", String),
    Column("starts_at", DateTime(timezone=True)),
    Column("ends_at", DateTime(timezone=True)),
    Column("price_rub", BigInteger),
    Column("age_rating", Integer),
    Column("ticket_quota", Integer),
    Column("status", String),
    Column("tags", ARRAY(Text)),
)
reservations = Table(
    "reservations",
    metadata,
    *audit(),
    Column("event_id", Uuid),
    Column("quantity", Integer),
    Column("unit_price_rub", BigInteger),
    Column("total_price_rub", BigInteger),
    Column("status", String),
    Column("booked_at", DateTime(timezone=True)),
    Column("cancelled_at", DateTime(timezone=True)),
)
dataset_metadata = Table(
    "dataset_metadata",
    metadata,
    Column("singleton", Integer, primary_key=True),
    Column("schema_version", String),
    Column("dataset_id", Uuid),
    Column("dataset_kind", String),
    Column("sample_seed_version", String),
    Column("reference_time", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("source_dataset_id", Uuid),
    Column("description", Text),
)
idempotency = Table(
    "idempotency",
    metadata,
    Column("scope", String, primary_key=True),
    Column("key", String, primary_key=True),
    Column("fingerprint", String),
    Column("response", JSONB),
    Column("created_at", DateTime(timezone=True)),
)
TABLES = {
    "artists": artists,
    "venues": venues,
    "series": series,
    "events": events,
    "reservations": reservations,
}
