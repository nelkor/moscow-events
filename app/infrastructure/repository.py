import json
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import cast, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.domain.entities import ENTITIES
from app.domain.errors import DomainError
from app.infrastructure.tables import TABLES, Geography, Geometry, events, idempotency, reservations


def coordinates(table):
    return [
        func.ST_Y(cast(table.c.location, Geometry)).label("latitude"),
        func.ST_X(cast(table.c.location, Geometry)).label("longitude"),
    ]


def projection(table):
    columns = [column for column in table.c if column.name != "location"]
    return columns + coordinates(table) if "location" in table.c else columns


def point(longitude, latitude):
    return cast(func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326), Geography)


class SqlRepository:
    def __init__(self, session):
        self.session = session

    def lock_catalog(self):
        # Единый порядок блокировок исключает взаимные блокировки сложных пакетов.
        self.session.execute(text("SELECT pg_advisory_xact_lock(74120301)"))

    def get(self, resource, identifier, lock=False):
        table = TABLES[resource]
        query = select(*projection(table)).where(table.c.id == identifier)
        if lock:
            query = query.with_for_update(of=table)
        record = self.session.execute(query).mappings().one_or_none()
        return ENTITIES[resource](**record) if record else None

    def save(self, resource, entity):
        table = TABLES[resource]
        fields = asdict(entity)
        if resource == "venues":
            latitude, longitude = fields.pop("latitude"), fields.pop("longitude")
            fields["location"] = point(longitude, latitude) if latitude is not None else None
        query = pg_insert(table).values(**fields)
        self.session.execute(
            query.on_conflict_do_update(
                index_elements=[table.c.id],
                set_={
                    name: getattr(query.excluded, name)
                    for name in fields
                    if name not in {"id", "created_at"}
                },
            )
        )

    def delete(self, resource, identifier):
        table = TABLES[resource]
        self.session.execute(delete(table).where(table.c.id == identifier))

    def sold(self, event_id):
        return self.session.scalar(
            select(func.coalesce(func.sum(reservations.c.quantity), 0)).where(
                reservations.c.event_id == event_id, reservations.c.status == "confirmed"
            )
        )

    def cancel_reservations(self, event_id):
        now = datetime.now(UTC)
        self.session.execute(
            update(reservations)
            .where(reservations.c.event_id == event_id, reservations.c.status == "confirmed")
            .values(status="cancelled", cancelled_at=now, updated_at=now)
        )

    def referenced(self, resource, identifier):
        if resource == "events":
            table, column = reservations, reservations.c.event_id
        else:
            table = events
            column = {
                "artists": events.c.artist_id,
                "venues": events.c.venue_id,
                "series": events.c.series_id,
            }[resource]
        return self.session.scalar(select(select(table.c.id).where(column == identifier).exists()))

    def max_quota(self, venue_id):
        return self.session.scalar(
            select(func.coalesce(func.max(events.c.ticket_quota), 0)).where(
                events.c.venue_id == venue_id
            )
        )

    def idempotency_get(self, scope, key, fingerprint):
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": scope + ":" + key},
        )
        record = (
            self.session.execute(
                select(idempotency).where(idempotency.c.scope == scope, idempotency.c.key == key)
            )
            .mappings()
            .one_or_none()
        )
        if record is None:
            return None
        if record["fingerprint"] != fingerprint:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT", "Idempotency key was used with a different request", 409
            )
        return record["response"]

    def idempotency_save(self, scope, key, fingerprint, response):
        # Снимок ответа хранится в БД и переживает перезапуск и перенос дампа.
        serializable = json.loads(json.dumps(response, default=str))
        self.session.execute(
            insert(idempotency).values(
                scope=scope,
                key=key,
                fingerprint=fingerprint,
                response=serializable,
                created_at=datetime.now(UTC),
            )
        )
