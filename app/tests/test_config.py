import pytest

from app.infrastructure.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def configure_environment(monkeypatch):
    for name in ("PG_HOST", "PG_PORT", "SEED_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PG_DB_NAME", "events_test")
    monkeypatch.setenv("PG_USER_NAME", "events_user")
    monkeypatch.setenv("PG_USER_PASSWORD", "test-password")


def test_application_connects_using_pg_settings_and_defaults():
    application = create_app()
    engine = application.state.database.engine
    try:
        assert engine.url.host == "db"
        assert engine.url.port == 5432
        assert engine.url.database == "events_test"
        assert engine.url.username == "events_user"
        assert engine.url.password == "test-password"
    finally:
        engine.dispose()


def test_host_and_port_can_be_overridden(monkeypatch):
    monkeypatch.setenv("PG_HOST", "postgres.internal")
    monkeypatch.setenv("PG_PORT", "15432")
    settings = Settings()
    assert settings.database_url.host == "postgres.internal"
    assert settings.database_url.port == 15432


@pytest.mark.parametrize("name", ["PG_DB_NAME", "PG_USER_NAME", "PG_USER_PASSWORD"])
@pytest.mark.parametrize("value", [None, ""])
def test_required_pg_settings_cannot_be_missing_or_empty(monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name) as caught:
        create_app()
    assert "test-password" not in str(caught.value)


@pytest.mark.parametrize("value", ["", "0", "-1", "65536", "not-a-number"])
def test_invalid_pg_port_prevents_startup(monkeypatch, value):
    monkeypatch.setenv("PG_PORT", value)
    with pytest.raises(ValueError, match="PG_PORT"):
        create_app()


@pytest.mark.parametrize("value", ["", " "])
def test_empty_pg_host_prevents_startup(monkeypatch, value):
    monkeypatch.setenv("PG_HOST", value)
    with pytest.raises(ValueError, match="PG_HOST"):
        create_app()


def test_password_special_characters_are_preserved_and_repr_hides_password(monkeypatch):
    password = "p@ss:/?#% +\\'пароль"
    monkeypatch.setenv("PG_USER_PASSWORD", password)
    settings = Settings()
    assert settings.database_url.password == password
    assert password not in repr(settings)
    assert password not in repr(settings.database_url)
