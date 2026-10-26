import copy
import math
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    create_model,
    field_validator,
    model_validator,
)

Name = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
Title = Annotated[str, Field(min_length=1, max_length=250, pattern=r"\S")]
PositiveInt = Annotated[int, Field(strict=True, gt=0, le=2147483647)]
Price = Annotated[int, Field(strict=True, ge=0, le=2147483647)]
EventStatus = Literal["draft", "published", "cancelled", "archived"]
ReservationStatus = Literal["confirmed", "cancelled"]
Genre = Literal[
    "concert",
    "theatre",
    "standup",
    "lecture",
    "exhibition",
    "festival",
    "cinema",
    "workshop",
    "excursion",
]
District = Literal[
    "CAO", "SAO", "SVAO", "VAO", "UVAO", "UAO", "UZAO", "ZAO", "SZAO", "ZELAO", "TAO", "NAO"
]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ArtistCreate(Body):
    name: Name
    description: str | None = None
    primary_genre_code: Genre | None = None
    image_url: HttpUrl | None = None


class VenueCreate(Body):
    name: Name
    city: Literal["Москва"]
    district_code: District
    street: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
    house: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]
    formatted_address: Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]
    latitude: Annotated[float, Field(ge=-90, le=90)] | None = None
    longitude: Annotated[float, Field(ge=-180, le=180)] | None = None
    capacity: PositiveInt
    description: str | None = None


class SeriesCreate(Body):
    name: Name
    description: str | None = None


class EventCreate(Body):
    title: Title
    description: str | None = None
    artist_id: UUID
    venue_id: UUID
    series_id: UUID | None = None
    genre_code: Genre
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    price_rub: Price
    age_rating: Annotated[int, Field(strict=True, json_schema_extra={"enum": [0, 6, 12, 16, 18]})]
    ticket_quota: PositiveInt
    status: EventStatus
    tags: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=100
    )


class PatchBody(Body):
    @model_validator(mode="after")
    def coordinate_pair(self) -> Self:
        if ("latitude" in self.model_fields_set) != ("longitude" in self.model_fields_set):
            raise ValueError("Coordinates must be provided together")
        return self


def patch_model(name, source):
    fields = {}
    for field_name, info in source.model_fields.items():
        optional_info = copy.deepcopy(info)
        optional_info.default = None
        optional_info.default_factory = None
        fields[field_name] = (info.annotation, optional_info)
    return create_model(name, __base__=PatchBody, **fields)


ArtistPatch = patch_model("ArtistPatch", ArtistCreate)
VenuePatch = patch_model("VenuePatch", VenueCreate)
SeriesPatch = patch_model("SeriesPatch", SeriesCreate)
EventPatch = patch_model("EventPatch", EventCreate)


class ReservationCreate(Body):
    event_id: UUID
    quantity: PositiveInt


class ReservationPatch(Body):
    status: Literal["cancelled"]


class BulkCreate(Body):
    op: Literal["create"]
    fields: EventCreate


class BulkUpdate(Body):
    op: Literal["update"]
    id: UUID
    fields: EventPatch


class BulkArchive(Body):
    op: Literal["archive"]
    id: UUID


class BulkRequest(Body):
    dry_run: bool = True
    operations: list[
        Annotated[BulkCreate | BulkUpdate | BulkArchive, Field(discriminator="op")]
    ] = Field(min_length=1, max_length=200)


class QueryModel(Body):
    @model_validator(mode="after")
    def check_ranges(self) -> Self:
        for low, high in (
            ("starts_from", "starts_to"),
            ("booked_from", "booked_to"),
            ("price_min", "price_max"),
        ):
            left, right = getattr(self, low, None), getattr(self, high, None)
            if left is not None and right is not None and left > right:
                raise ValueError(f"{low} must not exceed {high}")
        return self


class Pagination(QueryModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=100)


class ArtistList(Pagination):
    q: str | None = Field(None, max_length=250)
    primary_genre_code: Genre | None = None
    sort: Literal["name", "-name", "created_at", "-created_at", "updated_at", "-updated_at"] = (
        "name"
    )


class VenueFilters(QueryModel):
    q: str | None = Field(None, max_length=250)
    district_code: District | None = None
    capacity_min: int | None = Field(None, ge=1)
    has_coordinates: bool | None = None


class VenueList(VenueFilters, Pagination):
    sort: Literal[
        "name",
        "-name",
        "capacity",
        "-capacity",
        "created_at",
        "-created_at",
        "updated_at",
        "-updated_at",
    ] = "name"


class SeriesList(Pagination):
    q: str | None = Field(None, max_length=250)
    sort: Literal["name", "-name", "created_at", "-created_at", "updated_at", "-updated_at"] = (
        "name"
    )


class EventFilters(QueryModel):
    q: str | None = Field(None, max_length=250)
    artist_id: UUID | None = None
    venue_id: UUID | None = None
    series_id: UUID | None = None
    genre_code: Genre | None = None
    district_code: District | None = None
    status: EventStatus | None = None
    starts_from: AwareDatetime | None = None
    starts_to: AwareDatetime | None = None
    price_min: int | None = Field(None, ge=0)
    price_max: int | None = Field(None, ge=0)
    age_max: int | None = Field(None, ge=0, le=150)
    tickets_min: int | None = Field(None, ge=0)


class IncludeQuery(QueryModel):
    include: str = ""

    @field_validator("include")
    @classmethod
    def valid_include(cls, value):
        if value and not set(value.split(",")) <= {"venue", "artist", "availability"}:
            raise ValueError("Unknown include field")
        return value


class EventList(EventFilters, Pagination, IncludeQuery):
    sort: Literal[
        "starts_at",
        "-starts_at",
        "price_rub",
        "-price_rub",
        "title",
        "-title",
        "created_at",
        "-created_at",
    ] = "starts_at"


class Nearby(EventList):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: int = Field(ge=1, le=100000)
    status: EventStatus | None = "published"


class MapQuery(QueryModel):
    bbox: str | None = None
    limit: int = Field(1000, ge=1, le=1000)

    @field_validator("bbox")
    @classmethod
    def valid_bbox(cls, value):
        if value is None:
            return value
        try:
            west, south, east, north = map(float, value.split(","))
        except ValueError:
            raise ValueError("Bbox must contain west,south,east,north") from None
        if not all(math.isfinite(x) for x in (west, south, east, north)) or not (
            -180 <= west <= east <= 180 and -90 <= south <= north <= 90
        ):
            raise ValueError("Bbox coordinates are out of range or reversed")
        return value


class EventMap(EventFilters, MapQuery):
    status: EventStatus | None = "published"


class VenueMap(VenueFilters, MapQuery):
    pass


class ReservationList(Pagination):
    event_id: UUID | None = None
    status: ReservationStatus | None = None
    booked_from: AwareDatetime | None = None
    booked_to: AwareDatetime | None = None
    sort: Literal[
        "booked_at",
        "-booked_at",
        "created_at",
        "-created_at",
        "quantity",
        "-quantity",
        "total_price_rub",
        "-total_price_rub",
    ] = "booked_at"


class MetricsQuery(QueryModel):
    metrics: str | None = None

    @field_validator("metrics")
    @classmethod
    def valid_metrics(cls, value):
        from app.presentation.responses import EVENT_METRICS, RESERVATION_METRICS

        allowed = EVENT_METRICS if cls.__name__ == "EventAnalytics" else RESERVATION_METRICS
        if value is not None and (not value or not set(value.split(",")) <= set(allowed)):
            raise ValueError("Unknown metric")
        return value


class EventAnalytics(EventFilters, MetricsQuery):
    group_by: Literal[
        "none", "genre", "district", "venue", "artist", "age_rating", "month", "weekday", "hour"
    ] = "none"


class ReservationAnalytics(EventFilters, MetricsQuery):
    booked_from: AwareDatetime | None = None
    booked_to: AwareDatetime | None = None
    interval: Literal["day", "week", "month"] | None = None
    group_by: Literal["none", "genre", "venue", "artist", "district", "day", "week", "month"] = (
        "none"
    )


def body_fields(body, *, exclude_unset=False):
    fields = body.model_dump(exclude_unset=exclude_unset)
    if fields.get("image_url") is not None:
        fields["image_url"] = str(fields["image_url"])
    return fields


def query_fields(query):
    fields = query.model_dump(exclude_none=True)
    if fields.get("bbox"):
        fields["bbox"] = tuple(map(float, fields["bbox"].split(",")))
    return fields
