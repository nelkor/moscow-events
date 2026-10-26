import math

from app.domain.catalogs import AGE_RATINGS, DISTRICTS, EVENT_STATUSES, GENRES
from app.domain.entities import Artist, Event, Series, Venue
from app.domain.errors import DomainError


def require(condition: bool, code: str, message: str, status: int = 422, **details):
    if not condition:
        raise DomainError(code, message, status, **details)


def validate(entity):
    if isinstance(entity, (Artist, Venue, Series)):
        require(
            isinstance(entity.name, str) and 1 <= len(entity.name.strip()) <= 200,
            "INVALID_NAME",
            "Name must contain between 1 and 200 characters",
        )
    if isinstance(entity, Artist):
        require(
            entity.primary_genre_code is None or entity.primary_genre_code in GENRES,
            "INVALID_GENRE",
            "Unknown genre code",
        )
    if isinstance(entity, Venue):
        require(entity.city == "Москва", "INVALID_CITY", "City must be Moscow")
        require(entity.district_code in DISTRICTS, "INVALID_DISTRICT", "Unknown district code")
        require(entity.capacity > 0, "INVALID_CAPACITY", "Capacity must be positive")
        for name in ("street", "house", "formatted_address"):
            require(
                bool(getattr(entity, name).strip()), "INVALID_ADDRESS", "Address must not be empty"
            )
        lat, lon = entity.latitude, entity.longitude
        require(
            (lat is None) == (lon is None),
            "INVALID_COORDINATES",
            "Coordinates must be provided together",
        )
        if lat is not None:
            require(
                math.isfinite(lat)
                and math.isfinite(lon)
                and -90 <= lat <= 90
                and -180 <= lon <= 180,
                "INVALID_COORDINATES",
                "Coordinates are out of range",
            )
    if isinstance(entity, Event):
        require(
            1 <= len(entity.title.strip()) <= 250,
            "INVALID_TITLE",
            "Title must contain between 1 and 250 characters",
        )
        require(
            entity.starts_at.utcoffset() is not None and entity.ends_at.utcoffset() is not None,
            "TIMEZONE_REQUIRED",
            "Datetime must include a timezone",
        )
        require(entity.ends_at > entity.starts_at, "INVALID_INTERVAL", "End must be after start")
        require(entity.price_rub >= 0, "INVALID_PRICE", "Price must not be negative")
        require(entity.ticket_quota > 0, "INVALID_QUOTA", "Ticket quota must be positive")
        require(entity.age_rating in AGE_RATINGS, "INVALID_AGE_RATING", "Unknown age rating")
        require(entity.status in EVENT_STATUSES, "INVALID_STATUS", "Unknown event status")
        require(entity.genre_code in GENRES, "INVALID_GENRE", "Unknown genre code")


def validate_quota(event: Event, venue: Venue, sold: int):
    require(
        event.ticket_quota <= venue.capacity,
        "QUOTA_EXCEEDS_CAPACITY",
        "Ticket quota exceeds venue capacity",
    )
    require(
        event.ticket_quota >= sold,
        "QUOTA_BELOW_SALES",
        "Ticket quota is below confirmed sales",
        409,
        sold_tickets=sold,
    )


def validate_booking(event: Event, quantity: int, sold: int):
    require(quantity >= 1, "INVALID_QUANTITY", "Quantity must be positive")
    require(event.status == "published", "EVENT_NOT_BOOKABLE", "Event is not published", 409)
    require(
        quantity <= event.ticket_quota - sold,
        "INSUFFICIENT_TICKETS",
        "Not enough tickets available",
        409,
        event_id=str(event.id),
        requested=quantity,
        available=event.ticket_quota - sold,
    )
