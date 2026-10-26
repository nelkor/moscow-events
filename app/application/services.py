import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.ports import UnitOfWork
from app.domain.entities import ENTITIES, Event, Reservation, Venue
from app.domain.errors import DomainError, missing
from app.domain.rules import require, validate, validate_booking, validate_quota


def fingerprint(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


class CatalogService:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow
        self.repo = uow.repository

    def get(self, resource: str, identifier: UUID, lock: bool = False):
        entity = self.repo.get(resource, identifier, lock)
        if entity is None:
            raise missing(resource, identifier)
        return entity

    def check_relations(self, entity):
        validate(entity)
        if isinstance(entity, Event):
            venue = self.get("venues", entity.venue_id, lock=True)
            self.get("artists", entity.artist_id, lock=True)
            if entity.series_id:
                self.get("series", entity.series_id, lock=True)
            validate_quota(entity, venue, self.repo.sold(entity.id))
        if isinstance(entity, Venue):
            require(
                self.repo.max_quota(entity.id) <= entity.capacity,
                "CAPACITY_BELOW_QUOTA",
                "Capacity is below an existing event quota",
                409,
            )

    def create(self, resource: str, fields: dict):
        self.repo.lock_catalog()
        now = datetime.now(UTC)
        entity = ENTITIES[resource](id=uuid4(), created_at=now, updated_at=now, **fields)
        self.check_relations(entity)
        self.repo.save(resource, entity)
        return entity

    def update(self, resource: str, identifier: UUID, fields: dict):
        self.repo.lock_catalog()
        entity = self.get(resource, identifier, lock=True)
        updated = replace(entity, **fields, updated_at=datetime.now(UTC))
        self.check_relations(updated)
        self.repo.save(resource, updated)
        if isinstance(updated, Event) and updated.status == "cancelled":
            self.repo.cancel_reservations(identifier)
        return updated

    def delete(self, resource: str, identifier: UUID):
        self.repo.lock_catalog()
        self.get(resource, identifier, lock=True)
        require(
            not self.repo.referenced(resource, identifier),
            "RESOURCE_IN_USE",
            "Resource is referenced by other records",
            409,
        )
        self.repo.delete(resource, identifier)

    def bulk(self, payload: dict, key: str | None):
        dry_run = payload["dry_run"]
        digest = fingerprint(payload)
        if not dry_run:
            require(bool(key), "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required", 400)
            cached = self.repo.idempotency_get("events/bulk", key, digest)
            if cached is not None:
                return cached
        self.repo.lock_catalog()
        changes, errors = [], []
        counts = {"created": 0, "updated": 0, "archived": 0}
        for index, operation in enumerate(payload["operations"]):
            try:
                # Успешные операции видны следующим операциям даже при предпросмотре.
                with self.uow.savepoint():
                    op = operation["op"]
                    if op == "create":
                        entity = self.create("events", operation["fields"])
                        counter = "created"
                    else:
                        fields = {"status": "archived"} if op == "archive" else operation["fields"]
                        entity = self.update("events", operation["id"], fields)
                        counter = "archived" if op == "archive" else "updated"
                    changes.append(
                        {
                            "operation_index": index,
                            "op": op,
                            "id": str(entity.id),
                            "event": self.uow.queries.detail("events", entity.id),
                        }
                    )
                    counts[counter] += 1
            except DomainError as exc:
                errors.append(
                    {
                        "operation_index": index,
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    }
                )
        result = {
            "dry_run": dry_run,
            **counts,
            "changes": changes,
            "errors": errors,
            "operation_index_base": 0,
        }
        if dry_run:
            self.uow.rollback()
        elif errors:
            raise DomainError(
                "BULK_VALIDATION_FAILED", "Bulk operation validation failed", 422, errors=errors
            )
        else:
            self.repo.idempotency_save("events/bulk", key, digest, result)
        return result


class ReservationService:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow
        self.repo = uow.repository
        self.catalog = CatalogService(uow)

    def create(self, payload: dict, key: str):
        digest = fingerprint(payload)
        cached = self.repo.idempotency_get("reservations", key, digest)
        if cached is not None:
            # Возвращается исходный ответ, в том числе после последующей отмены брони.
            return cached
        event = self.catalog.get("events", payload["event_id"], lock=True)
        quantity = payload["quantity"]
        validate_booking(event, quantity, self.repo.sold(event.id))
        now = datetime.now(UTC)
        entity = Reservation(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            event_id=event.id,
            quantity=quantity,
            unit_price_rub=event.price_rub,
            total_price_rub=quantity * event.price_rub,
            status="confirmed",
            booked_at=now,
        )
        self.repo.save("reservations", entity)
        response = asdict(entity)
        self.repo.idempotency_save("reservations", key, digest, response)
        return response

    def cancel(self, identifier: UUID):
        reservation = self.catalog.get("reservations", identifier)
        # Все изменения остатков блокируют сначала мероприятие, затем бронирование.
        self.catalog.get("events", reservation.event_id, lock=True)
        reservation = self.catalog.get("reservations", identifier, lock=True)
        if reservation.status != "cancelled":
            now = datetime.now(UTC)
            reservation = replace(reservation, status="cancelled", cancelled_at=now, updated_at=now)
            self.repo.save("reservations", reservation)
        return asdict(reservation)
