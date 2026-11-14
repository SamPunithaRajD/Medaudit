"""FastAPI dependencies: DB session, authenticated user (server-side session), permissions, pagination."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
import hmac

from app.core.exceptions import Forbidden, Unauthenticated
from app.models import User
from app.security.access_control import CurrentUser, require_permission
from app.security.audit_logging import record_event
from app.security.authentication import csrf_token_for, hash_ip, resolve_session

__all__ = ["get_db", "current_user", "require", "Pagination", "pagination", "paginate", "client_ip"]


def client_ip(request: Request) -> str | None:
    # X-Forwarded-For is only trusted when a reverse proxy is configured to set it (see security docs).
    return request.client.host if request.client else None


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _token(request: Request) -> tuple[str | None, str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), "bearer"
    token = request.cookies.get(get_settings().session_cookie_name)
    return (token, "cookie") if token else (None, "none")


def current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    token, source = _token(request)
    resolved = resolve_session(db, token)
    if resolved is None:
        raise Unauthenticated("Authentication required", code="UNAUTHENTICATED")
    user, sess = resolved
    # CSRF: cookie-authenticated state changes must echo the per-session token in X-CSRF-Token.
    # Bearer-token clients are not vulnerable to CSRF (browsers never attach the header automatically).
    if source == "cookie" and request.method in UNSAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not hmac.compare_digest(sent, csrf_token_for(sess.id)):
            raise Forbidden("Missing or invalid CSRF token", code="CSRF_REJECTED")
    request.state.user_id = user.id
    return CurrentUser(user=user, session_id=sess.id, ip_hash=hash_ip(client_ip(request)))


def require_role(*roles: str) -> Callable[..., CurrentUser]:
    def dep(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)) -> CurrentUser:
        if cu.user.role not in roles:
            record_event(db, "authorization.denied", actor=cu.user, outcome="denied", ip_hash=cu.ip_hash,
                         details={"required_roles": ",".join(roles)})
            raise Forbidden("This area is not available for your account", code="FORBIDDEN")
        return cu
    return dep


def require(permission: str) -> Callable[..., CurrentUser]:
    def dep(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)) -> CurrentUser:
        try:
            require_permission(cu.user, permission)
        except Exception:
            record_event(db, "authorization.denied", actor=cu.user, outcome="denied", ip_hash=cu.ip_hash,
                         details={"permission": permission})
            raise
        return cu
    return dep


@dataclass
class Pagination:
    page: int
    page_size: int


def pagination(page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100)) -> Pagination:
    return Pagination(page, page_size)


def paginate(db: Session, query, p: Pagination, serialize: Callable[[list], list]) -> dict:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.scalars(query.offset((p.page - 1) * p.page_size).limit(p.page_size)).all()
    pages = (total + p.page_size - 1) // p.page_size
    return {"items": serialize(list(rows)),
            "pagination": {"page": p.page, "page_size": p.page_size, "total_items": total, "total_pages": pages,
                           "next_page": p.page + 1 if p.page < pages else None,
                           "previous_page": p.page - 1 if p.page > 1 else None}}


def paginate_list(items: list, p: Pagination) -> dict:
    total = len(items)
    pages = (total + p.page_size - 1) // p.page_size
    start = (p.page - 1) * p.page_size
    return {"items": items[start:start + p.page_size],
            "pagination": {"page": p.page, "page_size": p.page_size, "total_items": total, "total_pages": pages,
                           "next_page": p.page + 1 if p.page < pages else None,
                           "previous_page": p.page - 1 if p.page > 1 else None}}


def user_display(db: Session, user_id: str | None) -> dict | None:
    if not user_id:
        return None
    u = db.get(User, user_id)
    return {"user_id": u.id, "display_name": u.display_name} if u else None
