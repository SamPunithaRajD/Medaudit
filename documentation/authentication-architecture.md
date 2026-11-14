# Authentication, accounts and access control

Version 2.0 · 14 November 2026. Covers the Doctor Authentication & Hospital Administration Portal.

## 1. Principles

- Accounts are issued, never self-created. An administrator provisions a Doctor ID; the doctor activates it once with a one-time code.
- The server session is the only proof of identity. Role and permissions come from the database record; values sent by the browser (role, doctor ID, user id) are never trusted.
- Clinical data isolation is enforced in every query (scoped SQL), not in the UI. Inaccessible records return **404**, so their existence is not revealed.
- Administrators manage accounts and hospital configuration. They do **not** get automatic access to clinical records.

## 2. Components

| Concern | Implementation | Code |
|---|---|---|
| Password hashing | Argon2id (m=19 MiB, t=2, p=1), rehash on parameter change | `backend/app/security/authentication.py` |
| Sessions | 256-bit random opaque token in an HttpOnly cookie (`medaudit_session`); only an HMAC of it is stored in `user_sessions`; absolute expiry + idle timeout (idle timeout skipped for "remember me" sessions, which have their own shorter cap in days) | same |
| CSRF | Per-session token `HMAC(SESSION_SECRET, session_id)` returned by `/auth/login`, `/auth/doctor/activate` and `/auth/me`; required as `X-CSRF-Token` on cookie-authenticated POST/PUT/PATCH/DELETE. Also: SameSite cookie, `Origin` allow-list check | `backend/app/api/deps.py`, `backend/app/main.py` |
| One-time codes | 12 chars from an unambiguous alphabet (~60 bits), shown once, stored as HMAC bound to user + purpose, expiring, single-use, invalidated after N wrong attempts | `backend/app/security/account_tokens.py` |
| Rate limiting | Login (per IP + per ID), activation (per IP; per Doctor ID 5 / 15 min), forgot/reset password, password change | `backend/app/security/rate_limit.py` |
| Lockout | After `max_failed_logins` failures → locked for `lockout_minutes` (admin-configurable) | `authenticate()` |
| Enumeration protection | Unknown ID, wrong password, pending activation and locked accounts get the same response. Suspension/inactivity is revealed only after a correct password. Activation and reset failures share one generic message. Forgot-password always returns 202. | `backend/app/api/v1/auth.py` |
| Audit trail | Append-only, HMAC hash-chained `audit_logs` (+ PostgreSQL trigger). Includes `target_user_id`. Codes, passwords and tokens are stripped from details. | `backend/app/security/audit_logging.py` |
| Frontend state | `AuthProvider` calls `/auth/me` on load. CSRF token kept in memory only (never localStorage). Guards: `RequireAuth roles=…`. A 401 from any call returns the user to sign-in. | `src/portal/AuthContext.jsx`, `src/portal/api.js` |

## 3. Roles and permission matrix

| Capability | Admin | Doctor | Nurse / Auditor / Committee |
|---|---|---|---|
| Admin portal (`/api/v1/admin/*`, `/admin`) | ✔ | ✖ (403) | ✖ |
| Provision / edit / suspend / reactivate / deactivate / archive doctors | ✔ | ✖ | ✖ |
| Issue activation and password-reset codes, reset activation | ✔ | ✖ | ✖ |
| Authentication settings, security logs, log-chain verification | ✔ | ✖ | ✖ |
| Doctor workspace (`/api/v1/doctor/*`, `/doctor`) | ✖ (403) | ✔ own data | ✖ |
| Create patients, upload case sheets for own patients | ✖ | ✔ | upload via `/cases/upload` (nurse, auditor) |
| Read a case | only via assignment or emergency access | owner, assignee, or shared patient | assignee |
| Share own patient with another doctor | ✖ | ✔ (owner only) | ✖ |
| Emergency clinical access (time-limited, justified, audited) | ✔ | ✖ | committee member ✔ |
| Assign users to cases | ✔ (no clinical access gained) | ✖ | ✖ |
| Analytics (de-identified aggregates) | ✔ | ✔ | auditor, committee ✔ |
| Change own password, manage own sessions | ✔ | ✔ | ✔ |

Clinical access rule (in `backend/app/security/access_control.py`): a case is accessible if the user has an unexpired `case_assignments` row (uploader/owner, reviewer, observer, emergency) **or** the case belongs to a patient the user owns or that has an active `patient_access` share with them. No role bypasses this.

## 4. Account lifecycle

```
             admin creates Doctor ID
                    │  (activation code issued, shown once)
                    ▼
        ┌──── pending_activation ────┐  resend / reset activation → new code, old revoked
        │           │ doctor activates (ID + registered email + code + password)
        │           ▼
        │        active  ◄──────── reactivate ─────────┐
        │       │      │                               │
        │  suspend     deactivate                      │
        │       ▼      ▼                               │
        │   suspended  deactivated ────────────────────┤
        └──────────────┴───────── archive ──► archived ┘ (cannot sign in; data retained)
```

- Suspension, deactivation, archival, role change and activation reset **revoke all sessions immediately**.
- Archival is a soft state. `patients.owner_user_id` uses `ON DELETE RESTRICT`, so archiving never deletes or orphans clinical data.
- Activation is refused when the ID is unknown, not provisioned by an admin (no doctor profile), already activated, suspended/deactivated/archived, past `valid_until`, the email doesn't match, or the code is wrong, expired, used or revoked.

## 5. Activation workflow

1. The admin opens **Add doctor** (or uses bulk CSV import) → `POST /api/v1/admin/doctors`. The Doctor ID and email are checked for duplicates. The response contains the activation code and link **once**.
2. The admin gives the code to the doctor through a verified hospital channel. No email service is built in (see limitations).
3. The doctor opens `/activate?doctor_id=…` and enters Doctor ID, registered email, code, new password and confirmation → `POST /api/v1/auth/doctor/activate`.
4. On success: password hashed (Argon2id), account `active`, code marked used, default preferences created, a session is started, the event is audited, and the browser is sent to `/doctor`.

## 6. Password recovery

- **Forgot password** (`POST /auth/password/forgot`): identical response whether or not the ID exists. For an active doctor it flags `password_reset_requested_at`, which appears on the admin dashboard and in the directory (gold dot).
- **Admin-initiated reset** (`POST /admin/doctors/{id}/password-reset`): verify identity first, then issue a one-time reset code. Existing sessions end by default.
- **Reset** (`POST /auth/password/reset`): Doctor ID + code + new password. All sessions are revoked and lockout is cleared.

## 7. Emergency clinical access

`POST /api/v1/cases/{case_id}/emergency-access` with `reason` (≥20 characters) and `duration_minutes` (capped by `emergency_access_max_minutes`). Allowed for admin and committee members, and only while `allow_emergency_access` is on. It creates an expiring `case_assignments` row (`assignment_role="emergency"`) and a `clinical.emergency_access` audit event, which is shown on the admin dashboard. Access ends automatically at expiry.

## 8. Secure administrator bootstrap

There is no public admin registration. On the server console:

```powershell
cd backend
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\python scripts\create_admin.py --login-id ADM001 --email admin@hospital.local --name "System Administrator"
```

The password is prompted twice and never echoed or logged. It must meet the policy: ≥12 characters, three character classes, and not containing the email name or the ID. Re-running for an existing account needs `--reset`, which also revokes its sessions. For unattended setup only, `MEDAUDIT_ADMIN_PASSWORD` may be set for the single command and then removed. No default admin or default password exists.

## 9. Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection | — |
| `SESSION_SECRET` | HMAC key for session tokens, CSRF tokens, codes (≥32 chars) | required |
| `AUDIT_LOG_HMAC_KEY` | audit-log chain key | falls back to SESSION_SECRET |
| `SESSION_DURATION` (alias `SESSION_TTL_MINUTES`) | absolute session length, minutes | 480 |
| `SESSION_IDLE_TIMEOUT_MINUTES` | idle timeout for normal sessions | 60 |
| `ACTIVATION_TOKEN_EXPIRY` | initial activation-code validity in hours (then editable in the admin portal) | 72 |
| `APP_BASE_URL` | web app URL used to build activation/reset links | http://localhost:5173 |
| `ALLOWED_ORIGINS` (alias `FRONTEND_ORIGINS`) | CORS + CSRF origin allow-list | http://localhost:5173 |
| `SESSION_COOKIE_SECURE` | `true` with HTTPS (required outside development) | false |
| `PASSWORD_MIN_LENGTH`, `LOGIN_RATE_LIMIT_PER_MINUTE` | policy | 12, 10 |

Hospital-editable policy (`auth_settings` table, admin portal → Authentication settings): code validity, reset-code validity, wrong-code attempts, failed-login lockout, remember-me days, forgot-password requests on/off, emergency access on/off and maximum duration.

## 10. Known limitations

- No email/SMS delivery: codes are handed over by the administrator. Add a mail service before relying on email verification.
- Two-factor authentication / SSO is not implemented (the settings page says so).
- The rate limiter is in-process; use a shared store when running several API processes.
- Notification preferences are stored but no notification channel sends them yet.
