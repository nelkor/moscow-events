from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.application.ports import UnitOfWork
from app.application.services import CatalogService, ReservationService
from app.domain.catalogs import AGE_RATINGS, DISTRICTS, GENRES
from app.domain.errors import DomainError
from app.presentation import responses as out
from app.presentation import schemas as schema


def get_uow(request: Request):
    if not request.app.state.ready:
        raise DomainError("NOT_READY", "Database is not ready", 503)
    with request.app.state.database.uow() as uow:
        yield uow


Uow = Annotated[UnitOfWork, Depends(get_uow)]
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=200)
]

router = APIRouter(prefix="/api/v1", responses=out.ERROR_RESPONSES)


@router.get("/meta", response_model=out.MetaOut, tags=["metadata"])
def metadata(uow: Uow):
    return uow.queries.metadata()


@router.get("/dataset/summary", response_model=out.SummaryOut, tags=["metadata"])
def dataset_summary(uow: Uow):
    return uow.queries.summary()


@router.get("/meta/genres", response_model=out.CatalogOut, tags=["metadata"])
def genres():
    return {"data": [{"code": code, "name": name} for code, name in GENRES.items()]}


@router.get("/meta/districts", response_model=out.CatalogOut, tags=["metadata"])
def districts():
    return {"data": [{"code": code, "name": name} for code, name in DISTRICTS.items()]}


@router.get("/meta/age-ratings", response_model=out.CatalogOut, tags=["metadata"])
def age_ratings():
    return {"data": [{"code": age, "name": f"{age}+"} for age in AGE_RATINGS]}


@router.get(
    "/events/nearby",
    response_model=out.Page[out.EventOut],
    response_model_exclude_unset=True,
    tags=["events"],
)
def nearby(query: Annotated[schema.Nearby, Query()], uow: Uow):
    return uow.queries.listing("events", schema.query_fields(query))


@router.get("/events/geojson", response_model=out.FeatureCollection, tags=["events"])
def events_geojson(query: Annotated[schema.EventMap, Query()], uow: Uow):
    return uow.queries.geojson("events", schema.query_fields(query))


@router.get("/venues/geojson", response_model=out.FeatureCollection, tags=["venues"])
def venues_geojson(query: Annotated[schema.VenueMap, Query()], uow: Uow):
    return uow.queries.geojson("venues", schema.query_fields(query))


@router.post(
    "/events/bulk", response_model=out.BulkOut, response_model_exclude_unset=True, tags=["events"]
)
def bulk(payload: schema.BulkRequest, uow: Uow, key: IdempotencyKey = None):
    operations = []
    for operation in payload.operations:
        item = operation.model_dump()
        if isinstance(operation, schema.BulkUpdate):
            item["fields"] = schema.body_fields(operation.fields, exclude_unset=True)
        operations.append(item)
    result = CatalogService(uow).bulk({"dry_run": payload.dry_run, "operations": operations}, key)
    uow.commit()
    return result


@router.get(
    "/analytics/events",
    response_model=out.AnalyticsOut,
    response_model_exclude_unset=True,
    tags=["analytics"],
)
def event_analytics(query: Annotated[schema.EventAnalytics, Query()], uow: Uow):
    return uow.queries.analytics("events", schema.query_fields(query))


@router.get(
    "/analytics/reservations",
    response_model=out.AnalyticsOut,
    response_model_exclude_unset=True,
    tags=["analytics"],
)
def reservation_analytics(query: Annotated[schema.ReservationAnalytics, Query()], uow: Uow):
    return uow.queries.analytics("reservations", schema.query_fields(query))


def register_crud(resource, create_schema, patch_schema, list_schema, response_schema):
    def listing(query, uow: Uow):
        return uow.queries.listing(resource, schema.query_fields(query))

    listing.__annotations__["query"] = Annotated[list_schema, Query()]

    def read(identifier: UUID, uow: Uow):
        return uow.queries.detail(resource, identifier)

    def read_event(identifier: UUID, query: Annotated[schema.IncludeQuery, Query()], uow: Uow):
        return uow.queries.detail(resource, identifier, query.include)

    def create(payload, uow: Uow):
        entity = CatalogService(uow).create(resource, schema.body_fields(payload))
        result = uow.queries.detail(resource, entity.id)
        uow.commit()
        return result

    create.__annotations__["payload"] = create_schema

    def patch(identifier: UUID, payload, uow: Uow):
        CatalogService(uow).update(
            resource, identifier, schema.body_fields(payload, exclude_unset=True)
        )
        result = uow.queries.detail(resource, identifier)
        uow.commit()
        return result

    patch.__annotations__["payload"] = patch_schema

    def remove(identifier: UUID, uow: Uow):
        CatalogService(uow).delete(resource, identifier)
        uow.commit()
        return Response(status_code=204)

    common = {"tags": [resource], "response_model_exclude_unset": True}
    router.add_api_route(
        f"/{resource}",
        listing,
        methods=["GET"],
        response_model=out.Page[response_schema],
        operation_id=f"list_{resource}",
        **common,
    )
    router.add_api_route(
        f"/{resource}/{{identifier}}",
        read_event if resource == "events" else read,
        methods=["GET"],
        response_model=response_schema,
        operation_id=f"get_{resource}",
        **common,
    )
    router.add_api_route(
        f"/{resource}",
        create,
        methods=["POST"],
        status_code=201,
        response_model=response_schema,
        operation_id=f"create_{resource}",
        **common,
    )
    router.add_api_route(
        f"/{resource}/{{identifier}}",
        patch,
        methods=["PATCH"],
        response_model=response_schema,
        operation_id=f"update_{resource}",
        **common,
    )
    router.add_api_route(
        f"/{resource}/{{identifier}}",
        remove,
        methods=["DELETE"],
        status_code=204,
        operation_id=f"delete_{resource}",
        **common,
    )


register_crud("artists", schema.ArtistCreate, schema.ArtistPatch, schema.ArtistList, out.ArtistOut)
register_crud("venues", schema.VenueCreate, schema.VenuePatch, schema.VenueList, out.VenueOut)
register_crud("series", schema.SeriesCreate, schema.SeriesPatch, schema.SeriesList, out.SeriesOut)
register_crud("events", schema.EventCreate, schema.EventPatch, schema.EventList, out.EventOut)


@router.get("/reservations", response_model=out.Page[out.ReservationOut], tags=["reservations"])
def reservations(query: Annotated[schema.ReservationList, Query()], uow: Uow):
    return uow.queries.listing("reservations", schema.query_fields(query))


@router.get("/reservations/{identifier}", response_model=out.ReservationOut, tags=["reservations"])
def reservation(identifier: UUID, uow: Uow):
    return uow.queries.detail("reservations", identifier)


@router.post(
    "/reservations", response_model=out.ReservationOut, status_code=201, tags=["reservations"]
)
def book(payload: schema.ReservationCreate, uow: Uow, key: IdempotencyKey = None):
    if key is None:
        raise DomainError("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required", 400)
    result = ReservationService(uow).create(payload.model_dump(), key)
    uow.commit()
    return result


@router.patch(
    "/reservations/{identifier}", response_model=out.ReservationOut, tags=["reservations"]
)
def cancel_reservation(identifier: UUID, payload: schema.ReservationPatch, uow: Uow):
    result = ReservationService(uow).cancel(identifier)
    uow.commit()
    return result
