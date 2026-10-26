from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(kw_only=True)
class Entity:
    id: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(kw_only=True)
class Artist(Entity):
    name: str
    description: str | None = None
    primary_genre_code: str | None = None
    image_url: str | None = None


@dataclass(kw_only=True)
class Venue(Entity):
    name: str
    city: str
    district_code: str
    street: str
    house: str
    formatted_address: str
    capacity: int
    description: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(kw_only=True)
class Series(Entity):
    name: str
    description: str | None = None


@dataclass(kw_only=True)
class Event(Entity):
    title: str
    artist_id: UUID
    venue_id: UUID
    genre_code: str
    starts_at: datetime
    ends_at: datetime
    price_rub: int
    age_rating: int
    ticket_quota: int
    status: str
    description: str | None = None
    series_id: UUID | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class Reservation(Entity):
    event_id: UUID
    quantity: int
    unit_price_rub: int
    total_price_rub: int
    status: str
    booked_at: datetime
    cancelled_at: datetime | None = None


ENTITIES = {
    "artists": Artist,
    "venues": Venue,
    "series": Series,
    "events": Event,
    "reservations": Reservation,
}
