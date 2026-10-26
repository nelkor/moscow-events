import pytest
from sqlalchemy import text

from app.tests.test_api import API, book

pytestmark = pytest.mark.integration


def test_geodesic_radius_boundary_and_coordinate_order(client, catalog, event_fields):
    center = {"latitude": catalog["venue"]["latitude"], "longitude": catalog["venue"]["longitude"]}
    venue_fields = {
        key: catalog["venue"][key]
        for key in (
            "name",
            "city",
            "district_code",
            "street",
            "house",
            "formatted_address",
            "capacity",
        )
    }
    identifiers = {}
    for name, distance in (("center", 0), ("inside", 500), ("boundary", 1000), ("outside", 1001)):
        with client.app.state.database.engine.connect() as connection:
            location = (
                connection.execute(
                    text("""
                SELECT ST_Y(p::geometry) AS latitude, ST_X(p::geometry) AS longitude
                FROM (SELECT ST_Project(ST_SetSRID(ST_MakePoint(:longitude, :latitude),4326)::geography,
                      :distance, 0.0) AS p) projected
            """),
                    center | {"distance": distance},
                )
                .mappings()
                .one()
            )
        venue = client.post(
            f"{API}/venues", json=venue_fields | dict(location) | {"name": name}
        ).json()
        response = client.post(
            f"{API}/events", json=event_fields | {"venue_id": venue["id"], "title": name}
        )
        assert response.status_code == 201, response.text
        identifiers[name] = response.json()["id"]
    missing = client.post(
        f"{API}/venues", json=venue_fields | {"name": "Missing coordinates"}
    ).json()
    client.post(
        f"{API}/events", json=event_fields | {"venue_id": missing["id"], "title": "No coordinates"}
    )
    swapped = client.post(
        f"{API}/venues",
        json=venue_fields | {"latitude": center["longitude"], "longitude": center["latitude"]},
    ).json()
    client.post(
        f"{API}/events",
        json=event_fields | {"venue_id": swapped["id"], "title": "Swapped coordinates"},
    )
    client.post(f"{API}/events", json=event_fields | {"status": "draft", "title": "Draft"})
    params = center | {"radius_m": 1000}
    response = client.get(f"{API}/events/nearby", params=params)
    assert response.status_code == 200, response.text
    found = response.json()
    assert [e["id"] for e in found["data"]] == [
        identifiers[name] for name in ("center", "inside", "boundary")
    ]
    assert [e["distance_m"] for e in found["data"]] == pytest.approx([0, 500, 1000], abs=0.00001)
    page = client.get(f"{API}/events/nearby", params=params | {"page": 2, "page_size": 1}).json()
    assert page["meta"]["total"] == 3 and page["data"][0]["title"] == "inside"
    assert (
        client.get(f"{API}/events/nearby", params=params | {"status": "draft"}).json()["meta"][
            "total"
        ]
        == 1
    )
    assert client.get(f"{API}/events").json()["meta"]["total"] == 7
    assert client.get(f"{API}/events/nearby", params=params | {"radius_m": 0}).status_code == 400
    assert (
        client.get(f"{API}/events/nearby", params=params | {"latitude": "NaN"}).status_code == 400
    )


def test_geojson_bbox_limits_and_null_coordinates(client, catalog, event):
    response = client.get(f"{API}/events/geojson", params={"bbox": "37.61,55.75,37.63,55.77"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["type"] == "FeatureCollection" and len(data["features"]) == 1
    assert data["features"][0]["geometry"] == {
        "type": "Point",
        "coordinates": [catalog["venue"]["longitude"], catalog["venue"]["latitude"]],
    }
    assert client.get(f"{API}/venues/geojson?bbox=0,0,1,1").json()["features"] == []
    for bbox in ("NaN,0,1,1", "0,1,2", "2,0,1,2", "0,-91,1,2"):
        assert client.get(f"{API}/venues/geojson", params={"bbox": bbox}).status_code == 400
    assert client.get(f"{API}/events/geojson?limit=1001").status_code == 400
    assert (
        client.patch(
            f"{API}/venues/{catalog['venue']['id']}", json={"latitude": None, "longitude": None}
        ).status_code
        == 200
    )
    assert client.get(f"{API}/events/geojson").json()["features"] == []
    assert client.get(f"{API}/venues?has_coordinates=false").json()["meta"]["total"] == 1


def test_no_implicit_future_filter(client, event_fields):
    response = client.post(
        f"{API}/events",
        json=event_fields
        | {"starts_at": "2010-01-01T19:00:00+03:00", "ends_at": "2010-01-01T21:00:00+03:00"},
    )
    assert response.status_code == 201
    nearby = client.get(
        f"{API}/events/nearby?latitude=55.760236&longitude=37.618498&radius_m=1"
    ).json()
    assert nearby["meta"]["total"] == 1


def test_analytics_weighted_occupancy_and_snapshot_revenue(client, event_fields):
    events = []
    for i, (price, quota, genre) in enumerate(
        ((100, 10, "concert"), (300, 20, "concert"), (1100, 100, "theatre"))
    ):
        response = client.post(
            f"{API}/events",
            json=event_fields
            | {
                "title": f"Analytics {i}",
                "price_rub": price,
                "ticket_quota": quota,
                "genre_code": genre,
            },
        )
        assert response.status_code == 201
        events.append(response.json())
    assert book(client, events[0]["id"], 2).status_code == 201
    assert book(client, events[1]["id"], 3).status_code == 201
    cancelled = book(client, events[2]["id"], 4).json()
    client.patch(f"{API}/reservations/{cancelled['id']}", json={"status": "cancelled"})
    client.patch(f"{API}/events/{events[1]['id']}", json={"price_rub": 600})
    response = client.get(f"{API}/analytics/events")
    assert response.status_code == 200, response.text
    metrics = response.json()["data"][0]
    assert metrics["event_count"] == 3
    assert metrics["avg_price_rub"] == metrics["median_price_rub"] == 600
    assert metrics["min_price_rub"] == 100 and metrics["max_price_rub"] == 1100
    assert metrics["ticket_quota"] == 130 and metrics["sold_tickets"] == 5
    assert metrics["occupancy_percent"] == pytest.approx(5 / 130 * 100)
    assert client.get(f"{API}/analytics/reservations").json()["data"] == [
        {
            "group_key": "all",
            "group_label": "Все",
            "reservation_count": 2,
            "sold_tickets": 5,
            "revenue_rub": 1100,
            "cancelled_count": 1,
        }
    ]
    concert = client.get(
        f"{API}/analytics/events?genre_code=concert&metrics=median_price_rub,occupancy_percent"
    ).json()["data"][0]
    assert concert["median_price_rub"] == 350 and concert["occupancy_percent"] == pytest.approx(
        5 / 30 * 100
    )
    assert set(concert) == {"group_key", "group_label", "median_price_rub", "occupancy_percent"}
    assert client.get(f"{API}/analytics/events?metrics=unknown").status_code == 400
    for grouping in (
        "genre",
        "district",
        "venue",
        "artist",
        "age_rating",
        "month",
        "weekday",
        "hour",
    ):
        result = client.get(f"{API}/analytics/events", params={"group_by": grouping})
        assert result.status_code == 200 and result.json()["data"], result.text
    for grouping in ("genre", "district", "venue", "artist", "day", "week", "month"):
        result = client.get(f"{API}/analytics/reservations", params={"group_by": grouping})
        assert result.status_code == 200 and result.json()["data"], result.text


def test_analytics_empty_result(client):
    result = client.get(f"{API}/analytics/events").json()["data"][0]
    assert result["event_count"] == result["ticket_quota"] == result["sold_tickets"] == 0
    assert (
        result["avg_price_rub"] is None
        and result["median_price_rub"] is None
        and result["occupancy_percent"] is None
    )
    assert client.get(f"{API}/analytics/events?group_by=genre").json()["data"] == []
    reservations = client.get(f"{API}/analytics/reservations").json()["data"][0]
    assert (
        reservations["revenue_rub"]
        == reservations["sold_tickets"]
        == reservations["reservation_count"]
        == reservations["cancelled_count"]
        == 0
    )


def test_analytics_moscow_time_and_booking_window(client, event_fields):
    event = client.post(
        f"{API}/events",
        json=event_fields
        | {"starts_at": "2027-02-28T22:30:00Z", "ends_at": "2027-03-01T00:30:00Z"},
    ).json()
    assert (
        client.get(f"{API}/analytics/events?group_by=month").json()["data"][0]["group_key"]
        == "2027-03-01"
    )
    assert (
        client.get(f"{API}/analytics/events?group_by=weekday").json()["data"][0]["group_key"] == "1"
    )
    assert client.get(f"{API}/analytics/events?group_by=hour").json()["data"][0]["group_key"] == "1"
    reservation = book(client, event["id"]).json()
    with client.app.state.database.engine.begin() as connection:
        connection.execute(
            text("UPDATE reservations SET booked_at='2027-02-28T22:30:00Z' WHERE id=:id"),
            {"id": reservation["id"]},
        )
    response = client.get(
        f"{API}/analytics/reservations?interval=day&booked_from=2027-03-01T00:00:00%2B03:00&booked_to=2027-03-02T00:00:00%2B03:00"
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["group_key"] == "2027-03-01"
    assert (
        client.get(f"{API}/analytics/reservations?interval=week").json()["data"][0]["group_key"]
        == "2027-03-01"
    )
    assert (
        client.get(f"{API}/analytics/reservations?booked_to=2027-02-28T22:30:00Z").json()["data"][
            0
        ]["reservation_count"]
        == 0
    )
    assert (
        client.get(f"{API}/analytics/reservations?interval=day&group_by=genre").status_code == 400
    )
