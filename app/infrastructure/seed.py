from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, insert, select

from app.domain.catalogs import GENRES, SCHEMA_VERSION, SEED_VERSION
from app.domain.entities import Artist, Event, Reservation, Series, Venue
from app.infrastructure.repository import SqlRepository
from app.infrastructure.tables import TABLES, dataset_metadata

REFERENCE_TIME = datetime.fromisoformat("2027-03-01T00:00:00+03:00")
CREATED_AT = datetime.fromisoformat("2027-01-01T00:00:00+03:00")

# Координаты сверены по картам зданий; контрольная точка — Театральная площадь, 1.
# Источники адресов: сайты Третьяковской галереи, Планетария, ВДНХ и карточки Яндекс Карт.
SAMPLE_VENUES = (
    ("Театральный зал", "CAO", "Театральная площадь", "1", 55.760236, 37.618498),
    ("Галерейный зал", "CAO", "Лаврушинский переулок", "10", 55.741333, 37.620555),
    ("Новая галерея", "CAO", "Крымский Вал", "10", 55.735439, 37.606337),
    ("Зал планетария", "CAO", "Садовая-Кудринская улица", "5, стр. 1", 55.761411, 37.583661),
    ("Океанический зал", "SVAO", "проспект Мира", "119, стр. 23", 55.832969, 37.618523),
    ("Научный зал", "UZAO", "улица Вавилова", "57", 55.690602, 37.561687),
    ("Зал культуры", "UAO", "Восточная улица", "4, корп. 1", 55.714492, 37.657959),
    ("Космический зал", "SVAO", "проспект Мира", "111", 55.822752, 37.639752),
    ("Исторический зал", "ZAO", "площадь Победы", "3", 55.731665, 37.504645),
    ("Черновая площадка", "CAO", "Таганская улица", "1", None, None),
)


def sample_id(resource, index):
    return uuid5(NAMESPACE_URL, f"moscow-events/{SEED_VERSION}/{resource}/{index}")


def initialize_dataset(session, mode):
    if session.scalar(select(dataset_metadata.c.dataset_id)) is not None:
        return
    populated = any(
        session.scalar(select(func.count()).select_from(table)) for table in TABLES.values()
    )
    kind = "student_created" if populated or mode == "empty" else "sample"
    session.execute(
        insert(dataset_metadata).values(
            singleton=1,
            schema_version=SCHEMA_VERSION,
            dataset_id=uuid4(),
            dataset_kind=kind,
            sample_seed_version=SEED_VERSION if kind == "sample" else None,
            reference_time=REFERENCE_TIME,
            created_at=CREATED_AT,
            description="Small demonstration dataset" if kind == "sample" else "User dataset",
        )
    )
    if kind != "sample":
        return
    repo = SqlRepository(session)

    def audit(resource, index):
        return {
            "id": sample_id(resource, index),
            "created_at": CREATED_AT,
            "updated_at": CREATED_AT,
        }

    genres = list(GENRES)
    names = (
        "Северное сияние",
        "Северные сияния",
        "Голоса города",
        "Малая сцена",
        "Вечерний квартет",
        "Открытая наука",
        "Студия «Холст»",
        "Речной ансамбль",
        "Новое кино",
        "Городские экскурсоводы",
        "Комедийная гостиная",
        "Московский ансамбль",
        "Мастерская историй",
        "Ночной оркестр",
    )
    for i, name in enumerate(names):
        repo.save(
            "artists",
            Artist(
                **audit("artists", i),
                name=name,
                primary_genre_code=genres[i % len(genres)],
                description=None if i == 0 else "Demonstration artist",
            ),
        )
    for i, (name, district, street, house, lat, lon) in enumerate(SAMPLE_VENUES):
        repo.save(
            "venues",
            Venue(
                **audit("venues", i),
                name=name,
                city="Москва",
                district_code=district,
                street=street,
                house=house,
                formatted_address=f"Москва, {street}, {house}",
                latitude=lat,
                longitude=lon,
                capacity=500 + i * 50,
            ),
        )
    for i in range(3):
        repo.save("series", Series(**audit("series", i), name=f"Городские вечера {i + 1}"))
    sample_events = []
    for i in range(36):
        offset = i - 12 if i < 12 else i - 11
        start = REFERENCE_TIME + timedelta(days=offset, hours=19)
        artist_index, venue_index = i % len(names), i % len(SAMPLE_VENUES)
        if i == 7:
            # Намеренный конфликт расписаний для учебного поиска качества данных.
            start = sample_events[6].starts_at + timedelta(minutes=30)
            artist_index, venue_index = 6, 6
        event = Event(
            **audit("events", i),
            title=f"Городское событие {i + 1:02d}",
            description=None if i % 7 == 0 else "Demonstration event",
            artist_id=sample_id("artists", artist_index),
            venue_id=sample_id("venues", venue_index),
            series_id=sample_id("series", i // 3) if i < 9 else None,
            genre_code=genres[i % len(genres)],
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            price_rub=0 if i == 2 else 15000 if i == 8 else (i % 8 + 1) * 400,
            age_rating=(0, 6, 12, 16, 18)[i % 5],
            ticket_quota=10 if i == 0 else 11 if i == 1 else 80 + i * 5,
            status="cancelled"
            if i in (3, 15)
            else "draft"
            if i in (4, 16)
            else "archived"
            if i in (5, 17)
            else "published",
            tags=["sample", "family" if i % 5 == 0 else "evening"],
        )
        sample_events.append(event)
        repo.save("events", event)
    targets = [0, 1, 2, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 18, 19]
    for index in range(60):
        event = sample_events[targets[index // 4]]
        part = index % 4
        booked_at = event.starts_at - timedelta(days=10 - part, hours=part)
        cancelled = event.status == "cancelled" or (index >= 8 and part == 3)
        quantity = (2, 3, 1, 4)[part]
        unit_price = max(0, event.price_rub - (part % 2) * 100)
        repo.save(
            "reservations",
            Reservation(
                **{
                    **audit("reservations", index),
                    "created_at": booked_at,
                    "updated_at": booked_at,
                },
                event_id=event.id,
                quantity=quantity,
                unit_price_rub=unit_price,
                total_price_rub=quantity * unit_price,
                status="cancelled" if cancelled else "confirmed",
                booked_at=booked_at,
                cancelled_at=booked_at + timedelta(hours=1) if cancelled else None,
            ),
        )
