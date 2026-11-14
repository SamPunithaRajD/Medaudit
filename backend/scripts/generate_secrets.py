"""Print freshly generated secrets for .env (AES-256 key, session secret, audit-log HMAC key).

    python scripts/generate_secrets.py >> .env      # then edit as needed
"""
from __future__ import annotations

import base64
import os
import secrets


def main() -> None:
    print(f"ENCRYPTION_KEYS=v1:{base64.urlsafe_b64encode(os.urandom(32)).decode()}")
    print("ENCRYPTION_ACTIVE_KEY_VERSION=v1")
    print(f"SESSION_SECRET={secrets.token_urlsafe(48)}")
    print(f"AUDIT_LOG_HMAC_KEY={secrets.token_urlsafe(48)}")


if __name__ == "__main__":
    main()
