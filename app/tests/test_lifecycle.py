import os
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.infrastructure.config import Settings
from app.main import create_app
from app.tests.test_api import API, book


@pytest.mark.integration
def test_user_records_prevent_seed(database_factory, client_factory):
    url = database_factory()
    client = client_factory(database_url=url)
    client.post(f"{API}/artists", json={"name": "Independent artist"})
    with client.app.state.database.engine.begin() as connection:
        connection.execute(text("DELETE FROM dataset_metadata"))
    restarted = client_factory("sample", database_url=url)
    assert restarted.get(f"{API}/dataset/summary").json()["counts"] == {
        "artists": 1,
        "venues": 0,
        "series": 0,
        "events": 0,
        "reservations": 0,
    }


@pytest.mark.integration
def test_dump_restore_preserves_data_and_idempotency(
    client, client_factory, database_factory, event
):
    container = os.getenv("TEST_POSTGRES_CONTAINER")
    if not container:
        pytest.skip("TEST_POSTGRES_CONTAINER is required for the pg_dump/pg_restore test")
    url = make_url(client.app.state.database.settings.database_url)
    key = str(uuid4())
    reservation = book(client, event["id"], key=key).json()
    before = client.get(f"{API}/dataset/summary").json()
    dump = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "pg_dump",
            "-U",
            url.username,
            "-d",
            url.database,
            "-Fc",
            "--no-owner",
            "--no-acl",
        ],
        check=True,
        capture_output=True,
    ).stdout
    assert dump.startswith(b"PGDMP")
    target_url = database_factory()
    target = make_url(target_url)
    # Проверяется восстановление и в БД с уже существующим расширением PostGIS.
    with psycopg.connect(
        **target.translate_connect_args(username="user", database="dbname"), autocommit=True
    ) as connection:
        connection.execute("CREATE EXTENSION postgis")
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "pg_restore",
            "-U",
            target.username,
            "-d",
            target.database,
            "--no-owner",
            "--no-acl",
            "--single-transaction",
            "--exit-on-error",
        ],
        input=dump,
        check=True,
        capture_output=True,
    )
    restored = client_factory("sample", database_url=target_url)
    assert restored.get(f"{API}/dataset/summary").json() == before
    assert book(restored, event["id"], key=key).json() == reservation
    assert (
        restored.get(
            f"{API}/events/nearby?latitude=55.760236&longitude=37.618498&radius_m=1"
        ).json()["meta"]["total"]
        == 1
    )
    assert restored.get(f"{API}/events/{event['id']}").json()["sold_tickets"] == 1


def test_database_outage_does_not_break_liveness():
    application = create_app(
        Settings(
            pg_host="127.0.0.1",
            pg_port=1,
            pg_db_name="unavailable",
            pg_user_name="nobody",
            pg_user_password="unavailable",
            retry_seconds=0.01,
        )
    )
    with TestClient(application) as client:
        assert client.get("/health/live").status_code == 200
        for path in ("/health/ready", f"{API}/events"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json()["error"]["message"].isascii()
            assert "postgresql" not in response.text


@pytest.mark.integration
def test_ready_checks_schema_compatibility(client):
    with client.app.state.database.engine.begin() as connection:
        connection.execute(text("UPDATE dataset_metadata SET schema_version='incompatible'"))
    response = client.get("/health/ready")
    assert response.status_code == 503 and response.json()["error"]["code"] == "NOT_READY"


@pytest.mark.integration
def test_startup_recovers_after_temporary_database_failure(
    database_factory, settings_factory, monkeypatch
):
    application = create_app(
        settings_factory(database_factory(), seed_mode="empty", retry_seconds=0.1)
    )
    database = application.state.database
    initialize = database.initialize
    attempts = 0

    def flaky_initialize():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError("", {}, RuntimeError("Database unavailable"))
        initialize()

    monkeypatch.setattr(database, "initialize", flaky_initialize)
    with TestClient(application) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        deadline = monotonic() + 5
        while not application.state.ready and monotonic() < deadline:
            sleep(0.01)
        assert client.get("/health/ready").status_code == 200
        assert attempts == 2
