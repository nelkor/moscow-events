import logging

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException

from app.domain.errors import DomainError

logger = logging.getLogger(__name__)


def error_response(status, code, message, details=None):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


def install_error_handlers(app):
    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = []
        status = 422
        for item in exc.errors():
            location = list(item["loc"])
            if location[0] in {"query", "path", "header"} or item["type"] == "json_invalid":
                status = 400
            message = item["msg"] if item["msg"].isascii() else "Invalid value"
            detail = {"location": location, "code": item["type"], "message": message}
            if "operations" in location:
                index = location.index("operations") + 1
                if index < len(location) and isinstance(location[index], int):
                    detail["operation_index"] = location[index]
            errors.append(detail)
        return error_response(
            status, "VALIDATION_ERROR", "Request validation failed", {"errors": errors}
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(exc.status_code, "HTTP_ERROR", str(exc.detail))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        if isinstance(exc, IntegrityError):
            return error_response(409, "INTEGRITY_CONFLICT", "Operation conflicts with stored data")
        if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) in {
            "40001",
            "40P01",
            "55P03",
        }:
            return error_response(
                409, "CONCURRENT_MODIFICATION", "Concurrent modification; retry the request"
            )
        return error_response(503, "DATABASE_UNAVAILABLE", "Database is temporarily unavailable")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("Unhandled application error: %s", type(exc).__name__)
        return error_response(500, "INTERNAL_ERROR", "An internal error occurred")
