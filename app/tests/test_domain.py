import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.entities import Event, Venue
from app.domain.errors import DomainError
from app.domain.rules import validate, validate_booking, validate_quota


def sample_event(**overrides):
    now = datetime.now(UTC)
    fields = dict(
        id=uuid4(),
        created_at=now,
        updated_at=now,
        title="Event",
        artist_id=uuid4(),
        venue_id=uuid4(),
        genre_code="concert",
        starts_at=now,
        ends_at=now + timedelta(hours=2),
        price_rub=100,
        age_rating=0,
        ticket_quota=10,
        status="published",
    )
    return Event(**(fields | overrides))


@pytest.mark.parametrize(
    "changes",
    [
        {"price_rub": -1},
        {"ticket_quota": 0},
        {"age_rating": 7},
        {"title": " "},
        {"genre_code": "unknown"},
        {"status": "unknown"},
    ],
)
def test_domain_rejects_invalid_events(changes):
    with pytest.raises(DomainError):
        validate(sample_event(**changes))


def test_sold_tickets_and_capacity_are_invariants():
    event = sample_event()
    venue = Venue(
        id=event.venue_id,
        created_at=event.created_at,
        updated_at=event.updated_at,
        name="Venue",
        city="Москва",
        district_code="CAO",
        street="Street",
        house="1",
        formatted_address="Address",
        capacity=20,
    )
    validate_quota(event, venue, 10)
    with pytest.raises(DomainError, match="confirmed sales"):
        validate_quota(event, venue, 11)
    with pytest.raises(DomainError, match="capacity"):
        validate_quota(replace(event, ticket_quota=21), venue, 0)


def test_last_ticket_and_sold_out():
    event = sample_event()
    validate_booking(event, 1, 9)
    with pytest.raises(DomainError) as caught:
        validate_booking(event, 2, 9)
    assert caught.value.code == "INSUFFICIENT_TICKETS"


def test_dependencies_point_into_the_core():
    root = Path(__file__).resolve().parents[1]
    for layer in ("domain", "application"):
        for path in (root / layer).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                modules = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom)
                    else [n.name for n in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                for module in modules:
                    assert not module.startswith(
                        (
                            "fastapi",
                            "sqlalchemy",
                            "psycopg",
                            "pydantic",
                            "app.infrastructure",
                            "app.presentation",
                        )
                    ), path
