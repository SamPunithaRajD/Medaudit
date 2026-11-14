from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai_engine.ollama.client import get_ollama
from app.api.deps import current_user, get_db
from app.core.config import get_settings
from app.document_intelligence.ocr.providers import provider_status
from app.security.encryption import EncryptionError, get_key_ring

router = APIRouter(tags=["system"])


@router.get("/health", include_in_schema=True)
def health():
    return {"status": "ok"}


@router.get("/api/v1/system/status")
def system_status(db: Session = Depends(get_db), _=Depends(current_user)):
    s = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database = {"connected": True, "dialect": db.get_bind().dialect.name}
    except Exception as exc:
        database = {"connected": False, "error": type(exc).__name__}
    try:
        ring = get_key_ring()
        encryption = {"configured": True, "algorithm": "AES-256-GCM", "active_key_version": ring.active_version,
                      "key_versions": len(ring.keys)}
    except EncryptionError:
        encryption = {"configured": False}
    return {"app": s.app_name, "environment": s.app_env, "database": database, "encryption": encryption,
            "ocr_providers": provider_status(), "worker_mode": s.worker_mode,
            "local_only_ai": s.local_only_ai, "external_llm_enabled": s.external_llm_enabled}


@router.get("/api/v1/system/ollama-status")
def ollama_status(_=Depends(current_user)):
    st = get_ollama().status()
    st.pop("available_models", None) if not st.get("reachable") else None
    return {**st, "local_only_ai": get_settings().local_only_ai}
