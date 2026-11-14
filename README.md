# MedAudit AI 2.0

Clinical quality intelligence frontend. All bundled records are fictional. AI-like outputs are pre-authored demonstration examples; the frontend does not perform OCR or clinical inference in mock mode. Committee members own final mortality audit decisions. Backend authorization is required in any connected deployment.

## Web app

```powershell
npm install
npm run dev
```

Open http://localhost:5173. Use `VITE_USE_MOCK_DATA=true` (default) for the standalone synthetic workspace. Set `VITE_API_BASE_URL` and `VITE_USE_MOCK_DATA=false` to connect the proposed FastAPI service. Copy `.env.example` to `.env.local` to configure locally. Never put credentials or encryption keys in `VITE_*` variables.

Production bundle:

```powershell
npm run build
npm run preview
```

PDF report export uses the browser print dialog; choose “Save as PDF”. PDF uploads preview with the browser PDF viewer. ZIP archives can be inspected locally in mock mode. Mock processing simulates stages and does not claim OCR or model analysis ran.

## Mobile foundation

The Expo React Native client has bottom-tab screens for Overview, Timeline, Review and Settings.

```powershell
cd apps/mobile
npm install
npx expo start
```

Android and iOS JS bundle checks were run with `npx expo export --platform android --no-bytecode` and the matching iOS command. Native app signing/device builds remain environment-specific.

## Backend interface

The frontend service adapters and proposed schemas are documented in [documentation/frontend-backend-contract.md](documentation/frontend-backend-contract.md). These endpoints are interface proposals; this project contains no backend.
