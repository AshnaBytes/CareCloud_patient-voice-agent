"""FastAPI entrypoint: wiring only (routers, error envelope, startup)."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import config
from app.database import Base, SessionLocal, engine
from app.errors import AppError
from app.routes import patients
from app.schemas import format_errors
from app.services import seed_demo_data
from app import vapi

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    if config.SEED_DEMO_DATA:
        with SessionLocal() as db:
            seed_demo_data(db)
    log.info("Startup complete (db=%s)", engine.url.render_as_string(hide_password=True))
    yield


app = FastAPI(title="Patient Registration API", version="1.0.0", lifespan=lifespan)
app.include_router(patients.router)
app.include_router(vapi.router)


def _envelope(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse({"data": None, "error": {"code": code, "message": message, "details": details}},
                        status_code=status)


@app.exception_handler(AppError)
async def _app_error(_: Request, exc: AppError):
    return _envelope(exc.status, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    if any(e.get("type") == "json_invalid" for e in errors):
        return _envelope(400, "invalid_json", "Request body is not valid JSON")
    return _envelope(422, "validation_error", "Validation failed", format_errors(errors))


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException):
    return _envelope(exc.status_code, "http_error", str(exc.detail))


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception):
    log.exception("Unhandled error")
    return _envelope(500, "internal_error", "Internal server error")


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"data": {"status": "ok"}, "error": None}


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "static", "dashboard.html"))
