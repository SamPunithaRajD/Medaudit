"""MedAudit AI 2.0 — FastAPI application."""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1 import (admin, analytics, assistant, audit, auth, cases, clinical_timeline, doctor, documents,
                        preventability,
                        protocols, recommendations, reviews, system)
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger, request_id_var

log = get_logger("medaudit")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _error(status: int, code: str, message: str, details=None, headers=None) -> JSONResponse:
    rid = request_id_var.get()
    body = {"error": {"code": code, "message": message, "details": details or [], "request_id": rid},
            # Compatibility: the current frontend client reads `response.data.message`.
            "message": message}
    return JSONResponse(status_code=status, content=body, headers={**(headers or {}), "X-Request-ID": rid})


def validate_startup() -> None:
    s = get_settings()
    from app.security.encryption import get_key_ring
    get_key_ring()  # fail fast if no AES key configured
    if not s.session_secret or len(s.session_secret) < 32:
        raise RuntimeError("SESSION_SECRET must be set to at least 32 random characters")
    if s.is_production:
        if not s.session_cookie_secure:
            raise RuntimeError("SESSION_COOKIE_SECURE must be true outside development")
        if s.app_debug:
            raise RuntimeError("APP_DEBUG must be false outside development")
    if s.external_llm_enabled:
        log.warning("EXTERNAL_LLM_ENABLED=true has no effect: no external LLM integration exists in this build")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    validate_startup()
    worker = None
    if s.worker_mode == "embedded":
        from app.workers.worker import EmbeddedWorker
        worker = EmbeddedWorker()
        worker.start()
        log.info("Embedded worker started")
    yield
    if worker:
        worker.shutdown()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="MedAudit AI 2.0 API", version="2.0.0", lifespan=lifespan,
                  description="Clinical audit decision-support API. Outputs assist qualified reviewers and never establish "
                              "negligence, causation or final preventability.",
                  docs_url="/docs" if not s.is_production else None, redoc_url=None,
                  openapi_url="/openapi.json" if not s.is_production else None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        rid = rid if (rid.isascii() and 8 <= len(rid) <= 64 and rid.replace("-", "").replace("_", "").isalnum()) else f"req_{secrets.token_hex(8)}"
        token = request_id_var.set(rid)
        try:
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > s.max_upload_bytes + 1024 * 1024:
                return _error(413, "FILE_TOO_LARGE", "Request body too large")
            if request.method in UNSAFE and request.cookies.get(s.session_cookie_name):
                origin = request.headers.get("origin")
                if origin and origin not in s.cors_origins:
                    return _error(403, "CSRF_REJECTED", "Cross-origin request rejected")
            if s.enforce_https and request.url.scheme != "https" and request.headers.get("x-forwarded-proto") != "https":
                return _error(400, "HTTPS_REQUIRED", "HTTPS is required")
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if s.session_cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
                       allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "X-Request-ID", "X-MedAudit-Client",
                                      "X-CSRF-Token"],
                       expose_headers=["X-Request-ID", "Retry-After", "Content-Disposition"], max_age=600)
    if s.trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[h.strip() for h in s.trusted_hosts.split(",") if h.strip()])

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return _error(exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path")),
                    "issue": e.get("msg", "invalid")} for e in exc.errors()[:30]]
        return _error(422, "VALIDATION_ERROR", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHENTICATED", 403: "FORBIDDEN"}
        return _error(exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.error("Unhandled error: %s", type(exc).__name__, exc_info=s.app_debug)
        return _error(500, "INTERNAL_ERROR", "An internal error occurred")

    for module in (system, auth, admin, doctor, cases, documents, clinical_timeline, audit, preventability, recommendations,
                   reviews, analytics, protocols, assistant):
        app.include_router(module.router)
    return app


app = create_app()
