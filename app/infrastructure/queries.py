from sqlalchemy import Float, cast, extract, func, literal, or_, select

from app import __version__
from app.domain.catalogs import DISTRICTS, GENRES, SCHEMA_VERSION
from app.domain.errors import DomainError, missing
from app.infrastructure.repository import point, projection
from app.infrastructure.tables import (
    TABLES,
    Geometry,
    artists,
    dataset_metadata,
    events,
    reservations,
    venues,
)

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


class SqlQueries:
    def __init__(self, session):
        self.session = session
        self.sales = (
            select(reservations.c.event_id, func.sum(reservations.c.quantity).label("sold"))
            .where(reservations.c.status == "confirmed")
            .group_by(reservations.c.event_id)
            .subquery()
        )
        self.sold = func.coalesce(self.sales.c.sold, 0)

    def event_from(self):
        return (
            events.join(venues, events.c.venue_id == venues.c.id)
            .join(artists, events.c.artist_id == artists.c.id)
            .outerjoin(self.sales, self.sales.c.event_id == events.c.id)
        )

    def event_columns(self):
        def nested(table):
            args = []
            for column in projection(table):
                args.extend([cast(literal(str(column.name)), TABLES["venues"].c.name.type), column])
            return func.jsonb_build_object(*args)

        return [
            *events.c,
            self.sold.label("sold_tickets"),
            (events.c.ticket_quota - self.sold).label("available_tickets"),
            (cast(self.sold, Float) / func.nullif(events.c.ticket_quota, 0) * 100).label(
                "occupancy_percent"
            ),
            nested(venues).label("venue"),
            nested(artists).label("artist"),
        ]

    def conditions(self, resource, filters):
        table = TABLES[resource]
        result = []
        for name in (
            "artist_id",
            "venue_id",
            "series_id",
            "genre_code",
            "status",
            "event_id",
            "district_code",
            "primary_genre_code",
        ):
            value = filters.get(name)
            if value is not None:
                column = (
                    venues.c.district_code
                    if name == "district_code" and resource == "events"
                    else table.c[name]
                )
                result.append(column == value)
        if filters.get("q"):
            # Проценты и подчёркивания в пользовательской строке ищутся буквально.
            escaped = filters["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            column = table.c.title if resource == "events" else table.c.name
            result.append(
                or_(
                    column.ilike(f"%{escaped}%", escape="\\"),
                    table.c.description.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        ranges = (
            ("starts_from", "starts_at", ">="),
            ("starts_to", "starts_at", "<"),
            ("price_min", "price_rub", ">="),
            ("price_max", "price_rub", "<="),
            ("age_max", "age_rating", "<="),
            ("capacity_min", "capacity", ">="),
            ("booked_from", "booked_at", ">="),
            ("booked_to", "booked_at", "<"),
        )
        for name, field, operator in ranges:
            if filters.get(name) is not None:
                column = table.c[field]
                result.append(column.op(operator)(filters[name]))
        if filters.get("tickets_min") is not None:
            result.append(events.c.ticket_quota - self.sold >= filters["tickets_min"])
        if filters.get("has_coordinates") is not None:
            result.append(
                venues.c.location.is_not(None)
                if filters["has_coordinates"]
                else venues.c.location.is_(None)
            )
        if filters.get("bbox"):
            west, south, east, north = filters["bbox"]
            location = cast(venues.c.location, Geometry)
            result.extend(
                [func.ST_X(location).between(west, east), func.ST_Y(location).between(south, north)]
            )
        if filters.get("radius_m") is not None:
            center = point(filters["longitude"], filters["latitude"])
            result.append(func.ST_DWithin(venues.c.location, center, filters["radius_m"]))
        return result

    @staticmethod
    def include(record, include):
        result = dict(record)
        selected = set(include.split(","))
        for field in ("venue", "artist"):
            if field not in selected:
                result.pop(field, None)
        return result

    def detail(self, resource, identifier, include=""):
        table = TABLES[resource]
        query = (
            select(*self.event_columns()).select_from(self.event_from())
            if resource == "events"
            else select(*projection(table))
        )
        record = (
            self.session.execute(query.where(table.c.id == identifier)).mappings().one_or_none()
        )
        if record is None:
            raise missing(resource, identifier)
        return self.include(record, include)

    def listing(self, resource, filters):
        table = TABLES[resource]
        if resource == "events":
            query = select(*self.event_columns()).select_from(self.event_from())
        else:
            query = select(*projection(table))
        if filters.get("radius_m") is not None:
            distance = func.ST_Distance(
                venues.c.location, point(filters["longitude"], filters["latitude"])
            )
            query = query.add_columns(distance.label("distance_m"))
            ordering = [distance, events.c.starts_at, events.c.id]
        else:
            default_sort = (
                "starts_at"
                if resource == "events"
                else "booked_at"
                if resource == "reservations"
                else "name"
            )
            sort = filters.get("sort") or default_sort
            ordering = [
                table.c[sort.lstrip("-")].desc() if sort.startswith("-") else table.c[sort],
                table.c.id,
            ]
        query = query.where(*self.conditions(resource, filters))
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        page, size = filters.get("page", 1), filters.get("page_size", 25)
        records = self.session.execute(
            query.order_by(*ordering).offset((page - 1) * size).limit(size)
        ).mappings()
        include = filters.get("include", "")
        if filters.get("radius_m") is not None:
            include = include + ",venue"
        return {
            "data": [self.include(record, include) for record in records],
            "meta": {"page": page, "page_size": size, "total": total},
        }

    def geojson(self, resource, filters):
        table = TABLES[resource]
        if resource == "events":
            query = select(*self.event_columns()).select_from(self.event_from())
        else:
            query = select(*projection(venues))
        query = (
            query.where(venues.c.location.is_not(None), *self.conditions(resource, filters))
            .order_by(table.c.id)
            .limit(filters.get("limit", 1000))
        )
        features = []
        for record in self.session.execute(query).mappings():
            properties = self.include(record, "venue" if resource == "events" else "")
            location = properties["venue"] if resource == "events" else properties
            features.append(
                {
                    "type": "Feature",
                    "id": str(record["id"]),
                    "geometry": {
                        "type": "Point",
                        "coordinates": [location["longitude"], location["latitude"]],
                    },
                    "properties": properties,
                }
            )
        return {"type": "FeatureCollection", "features": features}

    def metadata(self):
        record = self.session.execute(select(dataset_metadata)).mappings().one_or_none()
        if record is None or record["schema_version"] != SCHEMA_VERSION:
            raise DomainError("NOT_READY", "Dataset metadata is not ready", 503)
        dataset = dict(record)
        dataset.pop("singleton")
        return {
            "backend_version": __version__,
            "api_version": "v1",
            "schema_version": SCHEMA_VERSION,
            "reference_time": dataset["reference_time"],
            "dataset": dataset,
            "features": ["crud", "postgis", "geojson", "bulk", "analytics", "idempotency"],
            "limits": {"page_size": 100, "bulk_operations": 200, "geojson_features": 1000},
            "control_point": {"latitude": 55.760236, "longitude": 37.618498, "radius_m": 3000},
        }

    def summary(self):
        meta = self.metadata()
        counts = {
            name: self.session.scalar(select(func.count()).select_from(table))
            for name, table in TABLES.items()
        }
        return {
            **meta,
            "counts": counts,
            "postgres_major": int(
                self.session.scalar(select(func.current_setting("server_version_num")))
            )
            // 10000,
            "postgis_major_minor": ".".join(
                self.session.scalar(select(func.postgis_lib_version())).split(".")[:2]
            ),
        }

    def group_expression(self, group_by, resource):
        groups = {
            "genre": (events.c.genre_code, events.c.genre_code),
            "district": (venues.c.district_code, venues.c.district_code),
            "venue": (cast(venues.c.id, TABLES["venues"].c.name.type), venues.c.name),
            "artist": (cast(artists.c.id, TABLES["artists"].c.name.type), artists.c.name),
            "age_rating": (events.c.age_rating, events.c.age_rating),
        }
        if group_by in groups:
            return groups[group_by]
        if group_by == "none":
            return literal("all"), literal("Все")
        local_time = func.timezone(
            "Europe/Moscow",
            reservations.c.booked_at if resource == "reservations" else events.c.starts_at,
        )
        if group_by in {"day", "week", "month"}:
            value = func.to_char(func.date_trunc(group_by, local_time), "YYYY-MM-DD")
        else:
            value = cast(
                extract("isodow" if group_by == "weekday" else "hour", local_time),
                TABLES["events"].c.age_rating.type,
            )
        return value, value

    def analytics(self, resource, filters):
        is_events = resource == "events"
        group_by = filters.get("group_by", "none")
        if not is_events and filters.get("interval"):
            if group_by not in {"none", filters["interval"]}:
                raise DomainError("INVALID_GROUPING", "Interval must match the time grouping", 400)
            group_by = filters["interval"]
        key, label = self.group_expression(group_by, resource)
        if is_events:
            expressions = {
                "event_count": func.count(events.c.id),
                "avg_price_rub": func.avg(events.c.price_rub),
                "median_price_rub": func.percentile_cont(0.5).within_group(events.c.price_rub),
                "min_price_rub": func.min(events.c.price_rub),
                "max_price_rub": func.max(events.c.price_rub),
                "ticket_quota": func.coalesce(func.sum(events.c.ticket_quota), 0),
                "sold_tickets": func.coalesce(func.sum(self.sold), 0),
                "occupancy_percent": cast(func.sum(self.sold), Float)
                / func.nullif(func.sum(events.c.ticket_quota), 0)
                * 100,
            }
            source = self.event_from()
        else:
            confirmed = reservations.c.status == "confirmed"
            expressions = {
                "reservation_count": func.count(reservations.c.id).filter(confirmed),
                "sold_tickets": func.coalesce(
                    func.sum(reservations.c.quantity).filter(confirmed), 0
                ),
                "revenue_rub": func.coalesce(
                    func.sum(reservations.c.total_price_rub).filter(confirmed), 0
                ),
                "cancelled_count": func.count(reservations.c.id).filter(
                    reservations.c.status == "cancelled"
                ),
            }
            source = self.event_from().join(reservations, reservations.c.event_id == events.c.id)
        metrics = filters.get("metrics") or ",".join(expressions)
        names = metrics.split(",")
        query = select(
            key.label("group_key"),
            label.label("group_label"),
            *(expressions[name].label(name) for name in names),
        ).select_from(source)
        event_filters = {
            name: value
            for name, value in filters.items()
            if name not in {"booked_from", "booked_to"}
        }
        query = query.where(*self.conditions("events", event_filters))
        if not is_events:
            query = query.where(
                *self.conditions(
                    "reservations",
                    {
                        name: filters[name]
                        for name in ("booked_from", "booked_to")
                        if name in filters
                    },
                )
            )
        if group_by != "none":
            query = query.group_by(key, label).order_by(key)
        rows = [dict(row) for row in self.session.execute(query).mappings()]
        for row in rows:
            if group_by == "genre":
                row["group_label"] = GENRES[row["group_key"]]
            elif group_by == "district":
                row["group_label"] = DISTRICTS[row["group_key"]]
            row["group_key"] = str(row["group_key"])
            row["group_label"] = str(row["group_label"])
        return {
            "data": rows,
            "meta": {"group_by": group_by, "timezone": "Europe/Moscow", "metrics": names},
        }
