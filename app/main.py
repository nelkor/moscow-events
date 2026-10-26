import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.domain.errors import DomainError
from app.infrastructure.config import Settings
from app.infrastructure.database import Database
from app.presentation.api import router
from app.presentation.errors import install_error_handlers
from app.presentation.responses import ERROR_RESPONSES, HealthOut

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    database = Database(settings)

    @asynccontextmanager
    async def lifespan(application):
        application.state.ready = False

        async def initialize():
            try:
                await asyncio.to_thread(database.initialize)
                application.state.ready = True
            except (SQLAlchemyError, DomainError, OSError):
                logger.warning("Database initialization is pending")

        async def retry():
            while not application.state.ready:
                await asyncio.sleep(settings.retry_seconds)
                await initialize()

        await initialize()
        task = asyncio.create_task(retry())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            application.state.ready = False
            database.engine.dispose()

    application = FastAPI(
        title="Moscow Events API", version=__version__, lifespan=lifespan, responses=ERROR_RESPONSES
    )
    application.state.database = database
    application.state.ready = False
    install_error_handlers(application)
    application.include_router(router)

    @application.get("/health/live", response_model=HealthOut, tags=["health"])
    def live():
        return {"status": "alive"}

    @application.get("/health/ready", response_model=HealthOut, tags=["health"])
    def ready():
        if not application.state.ready:
            raise DomainError("NOT_READY", "Database initialization is pending", 503)
        database.ready()
        return {"status": "ready"}

    return application
