from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration
API = "/api/v1"


def book(client, event_id, quantity=1, key=None):
    return client.post(
        f"{API}/reservations",
        json={"event_id": event_id, "quantity": quantity},
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def test_seed_and_restart_preserve_dataset(client_factory):
    client = client_factory("sample")
    before = client.get(f"{API}/dataset/summary").json()
    assert before["counts"] == {
        "artists": 14,
        "venues": 10,
        "series": 3,
        "events": 36,
        "reservations": 60,
    }
    assert before["postgres_major"] == 17 and before["postgis_major_minor"] == "3.6"
    venues = client.get(f"{API}/venues?page_size=100").json()["data"]
    mapped_venues = [venue for venue in venues if venue["latitude"] is not None]
    assert len(mapped_venues) == 9
    assert all(venue["longitude"] is not None for venue in mapped_venues)
    assert len(client.get(f"{API}/venues/geojson").json()["features"]) == len(mapped_venues)
    events = client.get(f"{API}/events?page_size=100&include=venue").json()["data"]
    assert all(event["venue_id"] in {venue["id"] for venue in venues} for event in events)
    assert (
        sum(
            datetime.fromisoformat(e["starts_at"].replace("Z", "+00:00"))
            < datetime.fromisoformat(before["reference_time"].replace("Z", "+00:00"))
            for e in events
        )
        == 12
    )
    assert {e["status"] for e in events} == {"draft", "published", "cancelled", "archived"}
    assert any(e["available_tickets"] == 0 for e in events)
    assert any(e["available_tickets"] == 1 for e in events)
    client.patch(f"{API}/events/{events[0]['id']}", json={"title": "User change"})
    restarted = client_factory("sample", client.app.state.database.settings.database_url)
    after = restarted.get(f"{API}/dataset/summary").json()
    assert after == before
    assert restarted.get(f"{API}/events/{events[0]['id']}").json()["title"] == "User change"
    with restarted.app.state.database.engine.connect() as conn:
        index = conn.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname='venues_location_gix'")
        )
    assert "gist" in index.lower()


def test_empty_mode_never_seeds_on_restart(client_factory):
    client = client_factory()
    summary = client.get(f"{API}/dataset/summary").json()
    assert not any(summary["counts"].values())
    restarted = client_factory("sample", client.app.state.database.settings.database_url)
    assert restarted.get(f"{API}/dataset/summary").json() == summary


def test_crud_and_reference_protection(client, catalog, event):
    assert (
        client.get(f"{API}/events/{event['id']}?include=artist,venue").json()["venue"]["id"]
        == catalog["venue"]["id"]
    )
    for resource, key in (("artists", "artist"), ("venues", "venue"), ("series", "series")):
        identifier = catalog[key]["id"]
        assert client.delete(f"{API}/{resource}/{identifier}").status_code == 409
        response = client.patch(f"{API}/{resource}/{identifier}", json={"name": "Changed"})
        assert response.status_code == 200 and response.json()["name"] == "Changed"
    assert (
        client.patch(
            f"{API}/events/{event['id']}", json={"price_rub": 0, "description": None}
        ).status_code
        == 200
    )
    assert client.delete(f"{API}/events/{event['id']}").status_code == 204
    assert client.get(f"{API}/events/{event['id']}").status_code == 404
    for resource, key in (("artists", "artist"), ("venues", "venue"), ("series", "series")):
        assert client.delete(f"{API}/{resource}/{catalog[key]['id']}").status_code == 204


@pytest.mark.parametrize(
    "fields",
    [
        {"price_rub": -1},
        {"price_rub": True},
        {"ticket_quota": 101},
        {"age_rating": 7},
        {"starts_at": "2027-03-05T22:00:00+03:00"},
        {"starts_at": "2027-03-05T19:00:00"},
        {"venue_id": str(uuid4())},
        {"title": " "},
        {"title": None},
        {"tags": None},
        {"unknown": "value"},
    ],
)
def test_event_validation(client, event, fields):
    response = client.patch(f"{API}/events/{event['id']}", json=fields)
    assert response.status_code in (404, 422), response.text
    assert response.json()["error"]["message"].isascii()


@pytest.mark.parametrize(
    "coordinates",
    [
        {"latitude": 55},
        {"latitude": None},
        {"latitude": 91, "longitude": 37},
        {"latitude": 55, "longitude": 181},
        {"latitude": None, "longitude": 37},
    ],
)
def test_coordinate_validation(client, catalog, coordinates):
    assert (
        client.patch(f"{API}/venues/{catalog['venue']['id']}", json=coordinates).status_code == 422
    )


def test_filters_pagination_and_literal_search(client, event_fields):
    for price, title, age, status in (
        (0, "A 100%", 0, "published"),
        (200, "B 100x", 6, "draft"),
        (1000, "C", 18, "published"),
    ):
        assert (
            client.post(
                f"{API}/events",
                json=event_fields
                | {"price_rub": price, "title": title, "age_rating": age, "status": status},
            ).status_code
            == 201
        )
    assert client.get(f"{API}/events", params={"q": "100%"}).json()["meta"]["total"] == 1
    response = client.get(
        f"{API}/events",
        params={
            "price_min": 0,
            "price_max": 200,
            "age_max": 6,
            "genre_code": "concert",
            "tickets_min": 10,
            "starts_from": "2027-03-05T19:00:00+03:00",
            "starts_to": "2027-03-06T00:00:00+03:00",
            "sort": "-price_rub",
            "page_size": 1,
        },
    ).json()
    assert response["meta"] == {"page": 1, "page_size": 1, "total": 2}
    assert response["data"][0]["price_rub"] == 200
    assert (
        client.get(f"{API}/events", params={"starts_to": event_fields["starts_at"]}).json()["meta"][
            "total"
        ]
        == 0
    )
    assert client.get(f"{API}/events?page=100").json()["data"] == []
    assert client.get(f"{API}/events?sort=unknown").status_code == 400
    assert client.get(f"{API}/events?price_min=10&price_max=0").status_code == 400


def test_booking_snapshot_cancellation_and_invariants(client, event, catalog):
    key = str(uuid4())
    reservation = book(client, event["id"], 3, key)
    assert reservation.status_code == 201, reservation.text
    assert book(client, event["id"], 3, key).json() == reservation.json()
    assert book(client, event["id"], 2, key).status_code == 409
    assert client.patch(f"{API}/events/{event['id']}", json={"price_rub": 2000}).status_code == 200
    assert (
        client.get(f"{API}/reservations/{reservation.json()['id']}").json()["total_price_rub"]
        == 3000
    )
    assert client.patch(f"{API}/events/{event['id']}", json={"ticket_quota": 2}).status_code == 409
    assert (
        client.patch(f"{API}/venues/{catalog['venue']['id']}", json={"capacity": 9}).status_code
        == 409
    )
    assert client.delete(f"{API}/events/{event['id']}").status_code == 409
    assert book(client, event["id"], 8).json()["error"]["code"] == "INSUFFICIENT_TICKETS"
    assert book(client, event["id"], 7).status_code == 201
    assert book(client, event["id"]).status_code == 409
    identifier = reservation.json()["id"]
    cancelled = client.patch(f"{API}/reservations/{identifier}", json={"status": "cancelled"})
    assert (
        client.patch(f"{API}/reservations/{identifier}", json={"status": "cancelled"}).json()
        == cancelled.json()
    )
    assert client.get(f"{API}/events/{event['id']}").json()["available_tickets"] == 3
    assert (
        client.patch(f"{API}/reservations/{identifier}", json={"status": "confirmed"}).status_code
        == 422
    )
    assert (
        client.patch(f"{API}/events/{event['id']}", json={"status": "cancelled"}).status_code == 200
    )
    assert client.get(f"{API}/reservations?status=confirmed").json()["meta"]["total"] == 0
    assert client.get(f"{API}/events/{event['id']}").json()["available_tickets"] == 10
    assert book(client, event["id"]).json()["error"]["code"] == "EVENT_NOT_BOOKABLE"


def test_concurrent_last_ticket(client, event):
    assert client.patch(f"{API}/events/{event['id']}", json={"ticket_quota": 1}).status_code == 200
    barrier = Barrier(8)

    def reserve(_):
        barrier.wait()
        return book(client, event["id"]).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(reserve, range(8)))
    assert statuses.count(201) == 1 and statuses.count(409) == 7
    assert client.get(f"{API}/events/{event['id']}").json()["sold_tickets"] == 1


def test_concurrent_same_idempotency_key(client, event):
    key = str(uuid4())
    barrier = Barrier(6)

    def reserve(_):
        barrier.wait()
        response = book(client, event["id"], key=key)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert len(set(pool.map(reserve, range(6)))) == 1
    assert client.get(f"{API}/events/{event['id']}").json()["sold_tickets"] == 1


def test_bulk_preview_atomicity_and_replay(client, event, event_fields):
    payload = {
        "dry_run": True,
        "operations": [
            {"op": "create", "fields": event_fields | {"title": "Bulk created"}},
            {"op": "update", "id": event["id"], "fields": {"price_rub": 500}},
            {"op": "archive", "id": event["id"]},
        ],
    }
    before = client.get(f"{API}/dataset/summary").json()["counts"]
    preview = client.post(f"{API}/events/bulk", json=payload)
    assert preview.status_code == 200 and not preview.json()["errors"], preview.text
    assert client.get(f"{API}/dataset/summary").json()["counts"] == before
    assert client.get(f"{API}/events/{event['id']}").json()["price_rub"] == 1000
    failed = {
        "dry_run": False,
        "operations": payload["operations"]
        + [
            {"op": "update", "id": event["id"], "fields": {"ticket_quota": 1000}},
            {"op": "archive", "id": str(uuid4())},
        ],
    }
    failure = client.post(f"{API}/events/bulk", json=failed, headers={"Idempotency-Key": "failed"})
    assert failure.status_code == 422, failure.text
    assert [e["operation_index"] for e in failure.json()["error"]["details"]["errors"]] == [3, 4]
    assert client.get(f"{API}/dataset/summary").json()["counts"] == before
    assert client.get(f"{API}/events/{event['id']}").json()["status"] == "published"
    payload["dry_run"] = False
    headers = {"Idempotency-Key": "bulk-success"}
    commit = client.post(f"{API}/events/bulk", json=payload, headers=headers)
    assert commit.status_code == 200, commit.text
    assert client.post(f"{API}/events/bulk", json=payload, headers=headers).json() == commit.json()
    assert client.get(f"{API}/dataset/summary").json()["counts"]["events"] == 2
    assert client.get(f"{API}/events/{event['id']}").json()["status"] == "archived"


def test_error_format_and_openapi(client, event):
    assert (
        client.post(
            f"{API}/reservations", json={"event_id": event["id"], "quantity": 1}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{API}/events", content="{", headers={"Content-Type": "application/json"}
        ).status_code
        == 400
    )
    assert client.get(f"{API}/events/not-a-uuid").status_code == 400
    assert client.get(f"{API}/events?unexpected=true").status_code == 400
    spec = client.get("/openapi.json").json()
    assert spec["openapi"] == "3.1.0"
    assert f"{API}/events/nearby" in spec["paths"]
    assert {"latitude", "longitude", "radius_m"} <= {
        p["name"] for p in spec["paths"][f"{API}/events/nearby"]["get"]["parameters"]
    }
    assert "ErrorResponse" in spec["components"]["schemas"]
    response = client.options(
        f"{API}/events",
        headers={
            "Origin": "http://testserver",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Idempotency-Key",
        },
    )
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "HTTP_ERROR"
    assert not any(name.startswith("access-control-") for name in response.headers)


def test_bulk_cancellation_preview_rolls_back_reservations(client, event):
    reservation = book(client, event["id"], 2).json()
    payload = {
        "dry_run": True,
        "operations": [
            {"op": "update", "id": event["id"], "fields": {"status": "cancelled"}},
        ],
    }
    response = client.post(f"{API}/events/bulk", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["changes"][0]["event"]["sold_tickets"] == 0
    assert client.get(f"{API}/events/{event['id']}").json()["sold_tickets"] == 2
    assert client.get(f"{API}/reservations/{reservation['id']}").json()["status"] == "confirmed"
    payload["dry_run"] = False
    payload["operations"].append({"op": "archive", "id": str(uuid4())})
    response = client.post(
        f"{API}/events/bulk", json=payload, headers={"Idempotency-Key": "rollback-cancel"}
    )
    assert response.status_code == 422
    assert client.get(f"{API}/events/{event['id']}").json()["sold_tickets"] == 2
    assert client.get(f"{API}/reservations/{reservation['id']}").json()["status"] == "confirmed"


def test_bulk_validation_limits_and_idempotency_conflict(client, event):
    payload = {
        "dry_run": False,
        "operations": [
            {"op": "update", "id": event["id"], "fields": {"price_rub": 900}},
        ],
    }
    assert client.post(f"{API}/events/bulk", json=payload).status_code == 400
    headers = {"Idempotency-Key": "repeat-bulk"}
    assert client.post(f"{API}/events/bulk", json=payload, headers=headers).status_code == 200
    payload["operations"][0]["fields"]["price_rub"] = 800
    assert client.post(f"{API}/events/bulk", json=payload, headers=headers).status_code == 409
    payload["operations"][0]["fields"]["unexpected"] = 1
    response = client.post(f"{API}/events/bulk", json=payload, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["details"]["errors"][0]["operation_index"] == 0
    assert (
        client.post(
            f"{API}/events/bulk", json={"operations": [{"op": "archive", "id": event["id"]}] * 201}
        ).status_code
        == 422
    )


def test_null_values_and_nonfinite_coordinates(client, catalog):
    identifier = catalog["artist"]["id"]
    response = client.patch(
        f"{API}/artists/{identifier}",
        json={
            "description": None,
            "primary_genre_code": None,
            "image_url": "https://example.com/artist.png",
        },
    )
    assert response.status_code == 200 and response.json()["description"] is None
    assert (
        client.patch(f"{API}/artists/{identifier}", json={"image_url": "invalid"}).status_code
        == 422
    )
    response = client.patch(
        f"{API}/venues/{catalog['venue']['id']}",
        content='{"latitude": NaN, "longitude": 37}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422 and response.json()["error"]["message"].isascii()
    response = client.patch(f"{API}/venues/{catalog['venue']['id']}", json={"city": "Other city"})
    assert response.status_code == 422
    assert all(item["message"].isascii() for item in response.json()["error"]["details"]["errors"])
