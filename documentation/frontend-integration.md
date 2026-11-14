# Frontend integration

The React app (`src/`) currently runs on bundled mock data. The backend implements the shapes in
`frontend-backend-contract.md`; switching the UI to live data needs the changes below. No frontend files
were modified by the backend work.

## 1. Environment

`.env.local` (frontend): `VITE_API_BASE_URL=http://localhost:8000` and `VITE_USE_MOCK_DATA=false`.
Backend `.env`: `FRONTEND_ORIGINS=http://localhost:5173` (exact origin, comma-separated for several).

## 2. Required client changes (`src/api/client.js`, `src/api/services.js`)

1. `axios.create({..., withCredentials: true})` so the HttpOnly session cookie is sent.
2. Error interceptor: prefer `err.response?.data?.error?.message` (backend also sends top-level `message`) and
   keep `error.code` / `request_id` for display and support.
3. Add a login screen calling `POST /api/v1/auth/login` and `GET /api/v1/auth/me` (role and `permissions[]`
   from the server replace the demonstrative role selector). On 401 redirect to login.
4. Replace the `localStorage` case-id fallback `MA-2026-0148` with the selected case id (`CASE-…`).
5. Map contract shapes to current view models, e.g.:

| UI mock field | Backend field |
|---|---|
| `case.patient` (string) | `case.patient.display_name` |
| `case.age`, `case.gender` | `case.patient.age_years`, `case.patient.gender` |
| `case.admitted`, `case.outcome` | `case.admitted_at`, `case.outcome_at` (format in the UI; may be null) |
| `case.status: "Under review"` | `case.status` / `case.review_status` enums |
| `case.cause`, `case.location` | `case.cause_as_recorded`, `case.death_location` |
| `case.score`, `case.flags` | no equivalent score per case; use `finding_count` from `GET /cases/{id}` |
| `timeline[].time`, `.type`, `.detail`, `.page` | `occurred_at` (+`occurred_at_precision`), `type`, `description`, `evidence[0].page_number` |
| `findings[].category: "Medical management"` | `category: "medical_management"` |
| `findings[].confidence: "Demo extraction · verify"` | `confidence` is null for rule findings; show `verification_status` and `requires_human_verification` |
| `findings[].page`, `.time`, `.status` | `evidence[].page_number`, `occurred_at`, `review_status` |
| `vitals[]` (hr/systolic/spo2 per row) | `GET /cases/{id}/vitals` — one row per measurement; group by `observed_at` |
| analytics `{total, reviewed}` | `total_cases_in_scope`, `total_deaths_reviewed`, … |

6. Upload: keep `FormData` with field `file`; handle 202 then poll `status_url` with bounded backoff, stopping on
   `completed|failed|cancelled`. Handle 413/415/422 (`error.details`) and 409 `DUPLICATE_UPLOAD`.
7. Page viewer: `<img src="${API}/api/v1/documents/{doc}/pages/{n}/preview">` works with cookies when the API is
   same-site; text via `/pages/{n}/text`; evidence highlight boxes via `include_words=true` (normalised 0–1 coordinates).
8. Report export: `GET /cases/{id}/audit-report/export?format=pdf` (server PDF) in addition to browser print.
9. Untimed events (`occurred_at: null`) must be shown as "time not documented", never placed on a time axis.
10. Render `informational` severity and `verification_status` values listed in backend-api-contract.md §2.

## 3. Same-origin option (recommended for production)

Serve `dist/` and proxy `/api` from one HTTPS origin (see security-architecture.md) — no CORS or cross-site
cookie issues. For Vite dev, a proxy in `vite.config.js` (`server.proxy['/api'] = 'http://localhost:8000'`)
achieves the same; then set `VITE_API_BASE_URL` to empty.

## 4. Mobile app (`apps/mobile`)

Log in with header `X-MedAudit-Client: native` to receive `session.token`; send `Authorization: Bearer <token>`
and store the token in the platform secure store (not AsyncStorage).


## 5. Account portals (implemented, v2.0)

The frontend now includes real authentication and the admin/doctor portals (`src/Root.jsx`, `src/portal/`).
They always call the backend; authentication is never mocked. The older clinical demo pages still follow
`VITE_USE_MOCK_DATA` and are reachable after sign-in (not for administrators).

- **Dev setup:** `npm run dev` proxies `/api` to `http://localhost:8000` (`vite.config.js`, override with
  `VITE_PROXY_TARGET`). Leave `VITE_API_BASE_URL` empty so cookies are first-party.
- **Client:** `src/portal/api.js` uses `withCredentials: true`, keeps the CSRF token in memory and sends
  `X-CSRF-Token` on writes. A 401 sends the user to `/login`.
- **Routes:** `/login`, `/activate`, `/forgot-password`, `/reset-password`, `/unauthorized`, `/account-status`,
  `/admin/*` (admin), `/doctor/*` (doctor), `/*` clinical workspace (any role except admin).
- **Fix applied:** `vite.config.js` no longer sets `optimizeDeps.disabled`. Vite 6 removed that option, and
  without dependency pre-bundling `npm run dev` rendered a blank page (`react` has no default ESM export).
- The fake role switcher in `App.jsx` was replaced by the signed-in user and a sign-out button.
