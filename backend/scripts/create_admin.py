"""Bootstrap a hospital administrator (or staff account) from the server console.

    python scripts/create_admin.py --login-id ADM001 --email admin@hospital.local --name "System Admin"

There is no public admin registration page. This command must be run by someone with shell access to
the server. The password is read interactively (never echoed, never logged); for unattended setup it
can be supplied via the MEDAUDIT_ADMIN_PASSWORD environment variable, which should then be unset.
Re-running for an existing account requires --reset, which also revokes its sessions.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, or_, select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.exceptions import ValidationFailed  # noqa: E402
from app.models import RoleModel, User  # noqa: E402
from app.security.audit_logging import record_event  # noqa: E402
from app.security.authentication import (hash_password, normalize_login_id, revoke_all_sessions,  # noqa: E402
                                         validate_password_strength)
from app.services.accounts import valid_login_id  # noqa: E402
from app.utils.ids import utcnow  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--login-id", required=True, help="sign-in ID, e.g. ADM001")
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", default="Administrator")
    ap.add_argument("--role", default="admin", choices=["admin", "nurse", "auditor", "committee_member"],
                    help="doctors are provisioned in the admin portal and activate themselves")
    ap.add_argument("--department", default=None)
    ap.add_argument("--reset", action="store_true", help="reset password/role of an existing account")
    args = ap.parse_args()
    login_id = valid_login_id(args.login_id)
    if not login_id:
        sys.exit("Invalid --login-id (3–30 letters, digits or hyphens)")
    email = args.email.strip().lower()
    env_pw = os.environ.get("MEDAUDIT_ADMIN_PASSWORD")
    password = env_pw or getpass.getpass("Password: ")
    if not env_pw and getpass.getpass("Repeat password: ") != password:
        sys.exit("Passwords do not match")
    try:
        validate_password_strength(password, email, login_id)
    except ValidationFailed as exc:
        sys.exit("Password rejected: " + "; ".join(d["issue"] for d in exc.details))
    with SessionLocal() as db:
        if db.get(RoleModel, args.role) is None:
            sys.exit("Roles table is empty: run `alembic upgrade head` first")
        user = db.scalar(select(User).where(or_(User.login_id == normalize_login_id(login_id),
                                                func.lower(User.email) == email)))
        now = utcnow()
        if user is None:
            user = User(login_id=login_id, email=email, display_name=args.name, role=args.role, department=args.department,
                        password_hash=hash_password(password), status="active", is_activated=True, activated_at=now)
            db.add(user)
            action = "created"
        else:
            if not args.reset:
                sys.exit(f"An account with this ID or email already exists ({user.login_id or user.email}). "
                         "Use --reset to reset it.")
            user.password_hash, user.role, user.status = hash_password(password), args.role, "active"
            user.login_id, user.is_activated, user.failed_login_count, user.locked_until = login_id, True, 0, None
            revoke_all_sessions(db, user.id, reason="cli_reset")
            action = "reset"
        db.flush()
        record_event(db, f"admin.cli_user_{action}", target_user_id=user.id, resource_type="user", resource_id=user.id,
                     details={"role": args.role})
        print(f"Account {login_id} ({email}) {action} with role {args.role}.")


if __name__ == "__main__":
    main()
