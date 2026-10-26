# Московские события

HTTP-сервер московских событий для запуска в контейнере.
Используется в лабораторных работах авторского курса по DevOps.

- [Swagger](https://nelkor.github.io/moscow-events)
- [Документация](docs/README.md)
- [Спецификация OpenAPI](docs/openapi.yml)

Имя образа Docker: `ghcr.io/nelkor/moscow-events`.

Для работы необходим доступ к СУБД PostgreSQL с расширением PostGIS.
Например, образ Docker `postgis/postgis`.

## Переменные окружения

Для обеспечения доступа к СУБД используются переменные:

- `PG_HOST`, по умолчанию `db`;
- `PG_PORT`, по умолчанию `5432`;
- `PG_DB_NAME`, обязательная;
- `PG_USER_NAME`, обязательная;
- `PG_USER_PASSWORD`, обязательная.
