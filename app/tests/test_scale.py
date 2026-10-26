from time import perf_counter

import pytest
from sqlalchemy import text

from app.tests.test_api import API

pytestmark = pytest.mark.integration


def test_queries_on_target_dataset(client, catalog):
    # Объёмные данные существуют только в отдельной временной БД этого теста.
    with client.app.state.database.engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO venues(id,name,city,district_code,street,house,formatted_address,location,capacity,created_at,updated_at)
            SELECT gen_random_uuid(), 'Scale venue ' || n, 'Москва', 'CAO', 'Театральная площадь', '1',
                   'Москва, Театральная площадь, 1',
                   ST_SetSRID(ST_MakePoint(37.618498 + (n % 100) * 0.001,55.760236 + (n % 20) * 0.001),4326)::geography,
                   100, now(), now()
            FROM generate_series(1,1999) n
        """)
        )
        connection.execute(
            text("""
            INSERT INTO events(id,title,artist_id,venue_id,genre_code,starts_at,ends_at,price_rub,age_rating,ticket_quota,status,tags,created_at,updated_at)
            SELECT gen_random_uuid(), 'Scale event ' || n, :artist, v.id, 'concert',
                   '2027-03-01T19:00:00+03:00'::timestamptz + (n % 60) * interval '1 day',
                   '2027-03-01T21:00:00+03:00'::timestamptz + (n % 60) * interval '1 day',
                   100 + n % 1000, 12, 20, 'published', '{}', now(), now()
            FROM generate_series(1,10000) n
            JOIN (SELECT id,row_number() OVER (ORDER BY id) rn FROM venues) v ON v.rn = (n - 1) % 2000 + 1
        """),
            {"artist": catalog["artist"]["id"]},
        )
        connection.execute(
            text("""
            INSERT INTO reservations(id,event_id,quantity,unit_price_rub,total_price_rub,status,booked_at,created_at,updated_at)
            SELECT gen_random_uuid(), e.id, 1, e.price_rub, e.price_rub, 'confirmed',
                   e.starts_at - n * interval '1 day', now(), now()
            FROM events e CROSS JOIN generate_series(1,10) n
        """)
        )
        connection.execute(text("ANALYZE"))
    expected_counts = {
        "artists": 1,
        "venues": 2000,
        "series": 1,
        "events": 10000,
        "reservations": 100000,
    }
    assert client.get(f"{API}/dataset/summary").json()["counts"] == expected_counts
    requests = (
        (
            f"{API}/events",
            {"price_min": 500, "tickets_min": 10, "sort": "starts_at", "include": "venue,artist"},
        ),
        (f"{API}/events/nearby", {"latitude": 55.760236, "longitude": 37.618498, "radius_m": 3000}),
        (f"{API}/analytics/events", {"group_by": "venue"}),
        (f"{API}/analytics/reservations", {"group_by": "week"}),
    )
    for path, parameters in requests:
        response = client.get(path, params=parameters)
        assert response.status_code == 200, response.text
        start = perf_counter()
        response = client.get(path, params=parameters)
        elapsed = perf_counter() - start
        assert response.status_code == 200 and response.json()["data"], response.text
        # Это защита от грубой регрессии; ориентиры ТЗ измеряются отдельно.
        assert elapsed < 10, (path, elapsed)
        print(f"{path}: {elapsed * 1000:.1f} ms")
    total = client.get(f"{API}/analytics/events").json()["data"][0]
    assert total["event_count"] == 10000 and total["sold_tickets"] == 100000
    assert total["occupancy_percent"] == 50
