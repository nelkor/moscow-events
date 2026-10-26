import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy.engine import make_url

from app.infrastructure.config import Settings
from app.main import create_app


@pytest.fixture
def database_factory():
    if any(not os.getenv(name) for name in ("PG_DB_NAME", "PG_USER_NAME", "PG_USER_PASSWORD")):
        pytest.skip("PG_DB_NAME, PG_USER_NAME and PG_USER_PASSWORD are required for integration tests")
    url = Settings().database_url.set(drivername="postgresql")
    connect_args = url.translate_connect_args(username="user", database="dbname")
    created = []

    def create_database():
        name = "test_events_" + uuid4().hex
        with psycopg.connect(**connect_args, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name))
            )
        created.append(name)
        return url.set(database=name)

    yield create_database
    with psycopg.connect(**connect_args, autocommit=True) as connection:
        for name in created:
            # Удаляются только БД с уникальными именами, созданные этой фикстурой.
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def settings_factory():
    def create_settings(database_url, **overrides):
        url = make_url(database_url)
        return Settings(
            pg_host=url.host,
            pg_port=url.port or 5432,
            pg_db_name=url.database,
            pg_user_name=url.username,
            pg_user_password=url.password,
            **overrides,
        )

    return create_settings


@pytest.fixture
def client_factory(database_factory, settings_factory):
    clients = []

    def create_client(mode="empty", database_url=None):
        application = create_app(
            settings_factory(database_url or database_factory(), seed_mode=mode)
        )
        client = TestClient(application)
        client.__enter__()
        assert client.get("/health/ready").status_code == 200
        clients.append(client)
        return client

    yield create_client
    for client in reversed(clients):
        client.__exit__(None, None, None)


@pytest.fixture
def client(client_factory):
    return client_factory()


@pytest.fixture
def catalog(client):
    artist = client.post("/api/v1/artists", json={"name": "Test artist"}).json()
    venue = client.post(
        "/api/v1/venues",
        json={
            "name": "Test venue",
            "city": "Москва",
            "district_code": "CAO",
            "street": "Театральная площадь",
            "house": "1",
            "formatted_address": "Москва, Театральная площадь, 1",
            "capacity": 100,
            "latitude": 55.760236,
            "longitude": 37.618498,
        },
    ).json()
    series = client.post("/api/v1/series", json={"name": "Test series"}).json()
    return {"artist": artist, "venue": venue, "series": series}


@pytest.fixture
def event_fields(catalog):
    return {
        "title": "Test event",
        "artist_id": catalog["artist"]["id"],
        "venue_id": catalog["venue"]["id"],
        "series_id": catalog["series"]["id"],
        "genre_code": "concert",
        "starts_at": "2027-03-05T19:00:00+03:00",
        "ends_at": "2027-03-05T21:00:00+03:00",
        "price_rub": 1000,
        "age_rating": 12,
        "ticket_quota": 10,
        "status": "published",
    }


@pytest.fixture
def event(client, event_fields):
    response = client.post("/api/v1/events", json=event_fields)
    assert response.status_code == 201, response.text
    return response.json()
