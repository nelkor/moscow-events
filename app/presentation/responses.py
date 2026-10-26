from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

EVENT_METRICS = (
    "event_count",
    "avg_price_rub",
    "median_price_rub",
    "min_price_rub",
    "max_price_rub",
    "ticket_quota",
    "sold_tickets",
    "occupancy_percent",
)
RESERVATION_METRICS = ("reservation_count", "sold_tickets", "revenue_rub", "cancelled_count")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


ERROR_RESPONSES = {code: {"model": ErrorResponse} for code in (400, 404, 409, 422, 503)}


class Audit(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime


class ArtistOut(Audit):
    name: str
    description: str | None
    primary_genre_code: str | None
    image_url: str | None


class VenueOut(Audit):
    name: str
    city: str
    district_code: str
    street: str
    house: str
    formatted_address: str
    latitude: float | None
    longitude: float | None
    capacity: int
    description: str | None


class SeriesOut(Audit):
    name: str
    description: str | None


class EventOut(Audit):
    title: str
    description: str | None
    artist_id: UUID
    venue_id: UUID
    series_id: UUID | None
    genre_code: str
    starts_at: datetime
    ends_at: datetime
    price_rub: int
    age_rating: int
    ticket_quota: int
    status: str
    tags: list[str]
    sold_tickets: int
    available_tickets: int
    occupancy_percent: float
    venue: VenueOut | None = None
    artist: ArtistOut | None = None
    distance_m: float | None = None


class ReservationOut(Audit):
    event_id: UUID
    quantity: int
    unit_price_rub: int
    total_price_rub: int
    status: str
    booked_at: datetime
    cancelled_at: datetime | None


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int


class Page[T](BaseModel):
    data: list[T]
    meta: PageMeta


class Point(BaseModel):
    type: Literal["Point"]
    coordinates: tuple[float, float]


class Feature(BaseModel):
    type: Literal["Feature"]
    id: UUID
    geometry: Point
    properties: dict[str, Any]


class FeatureCollection(BaseModel):
    type: Literal["FeatureCollection"]
    features: list[Feature]


class Group(BaseModel):
    group_key: str
    group_label: str
    event_count: int | None = None
    avg_price_rub: float | None = None
    median_price_rub: float | None = None
    min_price_rub: int | None = None
    max_price_rub: int | None = None
    ticket_quota: int | None = None
    sold_tickets: int | None = None
    occupancy_percent: float | None = None
    reservation_count: int | None = None
    revenue_rub: int | None = None
    cancelled_count: int | None = None


class AnalyticsMeta(BaseModel):
    group_by: str
    timezone: str
    metrics: list[str]


class AnalyticsOut(BaseModel):
    data: list[Group]
    meta: AnalyticsMeta


class BulkChange(BaseModel):
    operation_index: int
    op: str
    id: UUID
    event: EventOut


class BulkError(BaseModel):
    operation_index: int
    code: str
    message: str
    details: dict[str, Any]


class BulkOut(BaseModel):
    dry_run: bool
    created: int
    updated: int
    archived: int
    changes: list[BulkChange]
    errors: list[BulkError]
    operation_index_base: Literal[0]


class DatasetOut(BaseModel):
    schema_version: str
    dataset_id: UUID
    dataset_kind: str
    sample_seed_version: str | None
    reference_time: datetime
    created_at: datetime
    source_dataset_id: UUID | None
    description: str | None


class MetaOut(BaseModel):
    backend_version: str
    api_version: str
    schema_version: str
    reference_time: datetime
    dataset: DatasetOut
    features: list[str]
    limits: dict[str, int]
    control_point: dict[str, float]


class SummaryOut(MetaOut):
    counts: dict[str, int]
    postgres_major: int
    postgis_major_minor: str


class CatalogItem(BaseModel):
    code: str | int
    name: str


class CatalogOut(BaseModel):
    data: list[CatalogItem]


class HealthOut(BaseModel):
    status: str
