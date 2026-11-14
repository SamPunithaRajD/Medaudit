# Security architecture

These controls reduce risk; they do not by themselves make a deployment compliant with HIPAA, NABH, ICMR,
DPDP or any other framework. Compliance requires organisational policies, risk assessment, contracts,
training and audits.

| Area | Implementation | Code |
|---|---|---|
| Local AI | Ollama only; `LOCAL_ONLY_AI=true` refuses endpoints that do not resolve to loopback/private/link-local addresses (plus `LOCAL_AI_ALLOWED_HOSTS`). No external LLM client exists. | `ai_engine/ollama/client.py` |
| Passwords | Argon2id (m=19 MiB, t=2, p=1), rehash on parameter change, policy (≥12 chars, 3 classes, not containing account name), lockout 15 min after 5 failures, timing-equalised unknown users | `security/authentication.py` |
| Sessions | Random 256-bit token; only an HMAC (SESSION_SECRET) stored; absolute TTL + idle timeout; logout/role change/disable/password change revoke; HttpOnly + SameSite cookie, `Secure` required outside development | same + `api/v1/auth.py` |
| CSRF | SameSite=Lax cookie + rejection of unsafe cookie-authenticated requests whose `Origin` is not allow-listed | `main.py` |
| RBAC | Permission map per role, enforced server-side on every route | `security/access_control.py` |
| Case-level access | Non-admins see only cases they are assigned to (uploader auto-assigned); inaccessible → 404 | same |
| Committee separation | Only committee members/admin can approve cases or record committee decisions | routers |
| Encryption at rest | AES-256-GCM, 96-bit random nonce per operation, AAD binding (document id / context), tag verification, versioned key ring from env (`ENCRYPTION_KEYS`), rotation script. Encrypts originals, page text & word boxes, reports, assistant messages, patient name/identifier | `security/encryption.py`, `models/types.py`, `scripts/rotate_keys.py` |
| Integrity | SHA-256 of originals re-checked before processing and decryption tag on every read (tamper → 503 `DOCUMENT_INTEGRITY`) | `services/processing_service.py`, `api/v1/documents.py` |
| Audit log | Append-only (ORM guard + PostgreSQL trigger blocking UPDATE/DELETE/TRUNCATE); HMAC hash chain (`AUDIT_LOG_HMAC_KEY`), `/admin/audit-logs/verify`; records login/logout/failures, denied authorisation, case views/updates, page text/preview views, downloads, finding decisions, preventability decisions, report views/exports, recommendation changes, admin actions; never patient data | `security/audit_logging.py` |
| Upload safety | Size caps, signature checks, ZIP traversal/bomb/symlink/nesting defences, page and pixel limits (decompression bombs) | `document_intelligence/ingestion/` |
| Input validation | Pydantic models with `extra="forbid"`; bounded lengths; JSON error envelope without stack traces | `schemas/requests.py`, `main.py` |
| Rate limiting | Login (per IP and per account), upload and assistant per user | `security/rate_limit.py` |
| Headers | nosniff, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, CSP `default-src 'none'` on API, `Cache-Control: no-store`, HSTS when secure cookies are on | `main.py` |
| CORS | Exact allowlist (`FRONTEND_ORIGINS`), credentials allowed only for those origins | `main.py` |
| Logging | Redaction filter (tokens, passwords, long numbers); no request bodies or document text logged | `core/logging.py` |
| De-identification | Masking of names, identifiers (UHID/MRN/IP no./ABHA), phones, emails, addresses; keyed pseudonyms; analytics never include identifiers and suppress small cells | `security/anonymization.py`, `analytics/service.py` |
| Prompt injection | Document text fenced and treated as data; no tool use; outputs evidence-verified | `ai_engine/prompts`, `ai_engine/validators` |

## Key management

1. `python scripts/generate_secrets.py` → put values in `.env` (or a secrets manager / OS-protected env). Never commit.
2. Back up encryption keys separately from database backups; losing a key makes its data unrecoverable.
3. Rotation: add `v2` to `ENCRYPTION_KEYS`, set `ENCRYPTION_ACTIVE_KEY_VERSION=v2`, run `scripts/rotate_keys.py`,
   verify, then remove `v1`.

## HTTPS deployment

Terminate TLS at a reverse proxy (IIS/ARR, nginx, Caddy) and proxy to uvicorn on localhost. Set
`SESSION_COOKIE_SECURE=true`, `ENFORCE_HTTPS=true`, `TRUSTED_HOSTS`, `APP_ENV=production` (disables `/docs`).
Example nginx:

```nginx
server {
  listen 443 ssl http2;
  server_name medaudit.hospital.local;
  ssl_certificate     /etc/ssl/medaudit.crt;
  ssl_certificate_key /etc/ssl/medaudit.key;
  client_max_body_size 110m;
  location /api/ { proxy_pass http://127.0.0.1:8000; proxy_set_header X-Forwarded-Proto https;
                   proxy_set_header Host $host; proxy_read_timeout 120s; }
  location / { root /srv/medaudit/dist; try_files $uri /index.html; }
}
```

Serving the React build from the same origin as `/api` avoids cross-site cookies entirely.

## Residual risks / to do before production

Malware scanning of uploads (integrate ClamAV or an EDR hook in `case_service.create_case_from_upload`);
shared rate-limit store for multi-process deployments; MFA/SSO (e.g. hospital IdP); retention and deletion
policy enforcement; database role separation and TLS; penetration testing.
