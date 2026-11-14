"""Re-encrypt stored documents, OCR text, reports and identifiers under ENCRYPTION_ACTIVE_KEY_VERSION.

Procedure: add the new key to ENCRYPTION_KEYS (keep the old one), set ENCRYPTION_ACTIVE_KEY_VERSION
to the new version, run this script, verify, then remove the old key.

    python scripts/rotate_keys.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models import AssistantMessage, AuditReport, Document, OCRResult, PatientCase  # noqa: E402
from app.security.encryption import get_key_ring, parse_header, rotate_blob  # noqa: E402
from app.services import storage  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ring = get_key_ring()
    files = rows = 0
    with SessionLocal() as db:
        for doc in db.scalars(select(Document).where(Document.storage_key != "pending")):
            path = storage._path(doc.storage_key)
            if not path.exists():
                continue
            blob = path.read_bytes()
            if parse_header(blob)[0] != ring.active_version:
                files += 1
                if not args.dry_run:
                    path.write_bytes(rotate_blob(blob, doc.id.encode()))
                    doc.key_version = ring.active_version
        # Encrypted columns are re-encrypted with the active key simply by re-assigning them.
        for model, cols in ((PatientCase, ("patient_name", "patient_identifier")), (OCRResult, ("text", "words_json")),
                            (AuditReport, ("content",)), (AssistantMessage, ("content",))):
            for obj in db.scalars(select(model)).yield_per(200):
                for c in cols:
                    v = getattr(obj, c)
                    if v is not None:
                        flag_modified(obj, c)  # force re-encryption under the active key on flush
                        rows += 1
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    print(f"{'Would rotate' if args.dry_run else 'Rotated'} {files} files and {rows} encrypted column values "
          f"to key version {ring.active_version}")


if __name__ == "__main__":
    main()
