# MatchNest

Personal sports calendar for iPhone.

MatchNest tracks followed events across Formula 1, NAVI CS2 and football, with spoiler-safe past results, previous and future calendar months, and an iOS widget for the next event.

## Stack

- Backend: Python, FastAPI
- Web PWA: React, TypeScript, Vite
- iOS: Swift, SwiftUI
- Widget: WidgetKit

## MVP scope

- Main feed: Formula 1, NAVI CS2, Ukraine NT, Barcelona
- Starred feed: Ferrari, UCL, FIFA World Cup, UEFA Euro, CS2 Majors, IEM, BLAST
- Timeline ranges: today, week, month
- Calendar: previous and future months
- Spoiler mode: hide past results until revealed
- Widget: next followed event
- Accounts: email/password login, email verification, Google login
- Explore: search, add custom teams/tournaments/players, bind provider IDs, move entities between main/starred/explore/hidden

## Local development

Create the root environment file once:

```powershell
cp .env.example .env
```

Run backend from the repository root:

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
.\backend\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

Run the PWA in another terminal:

```powershell
cd web
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

Useful backend checks:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/sources
http://127.0.0.1:8000/docs
```

## Production-like local run

Build the PWA and serve it from FastAPI on one local URL:

```powershell
cd web
npm install
npm run build
cd ..
.\backend\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

```text
http://127.0.0.1:8000
```

## Production hosting

The Docker setup builds the PWA and serves it from FastAPI, so production uses one public URL for both the app and API.

```bash
docker build -t match-nest .
docker run --env-file .env -p 8000:8000 match-nest
```

Open:

```text
http://127.0.0.1:8000
```

For Render, use `render.yaml` and set these environment variables in the Render dashboard:

- `DATABASE_URL`, required on Render free. Use a hosted Postgres connection string.
- `FOOTBALL_DATA_TOKEN`
- `API_FOOTBALL_TOKEN`
- `API_FOOTBALL_ENABLE=1` if your API-Football plan supports the seasons you need.
- `THESPORTSDB_API_KEY` optional. MatchNest defaults to the public key `3` for the Ukraine NT fallback feed.
- `PANDASCORE_TOKEN`
- `GRID_API_TOKEN` optional future CS2 stats provider token.
- `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` for browser and iPhone push notifications.
- `NOTIFICATION_DISPATCH_TOKEN`, a random shared secret for background refresh and notification cron calls.
- `APP_PUBLIC_URL`, for example `https://your-service.onrender.com`.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` for Google login.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` for email verification.
- `MATCHNEST_ALLOW_DEMO_EVENTS=0`

Local development uses SQLite automatically when `DATABASE_URL` is empty:

```text
backend/.data
```

Production on Render free should set `DATABASE_URL` to a hosted Postgres database, for example Neon free Postgres, so accounts, follows, custom entities, manual pins and cached events persist across deploys.

Render free setup:

```text
Render web service: free
DATABASE_URL:       hosted Postgres connection string
Persistent disk:    not needed
```

Google OAuth setup:

```text
Authorized redirect URI: https://your-service.onrender.com/auth/google/callback
GOOGLE_REDIRECT_URI:     https://your-service.onrender.com/auth/google/callback
APP_PUBLIC_URL:          https://your-service.onrender.com
```

For local Google login:

```text
Authorized redirect URI: http://127.0.0.1:8000/auth/google/callback
GOOGLE_REDIRECT_URI:     http://127.0.0.1:8000/auth/google/callback
APP_PUBLIC_URL:          http://127.0.0.1:8000
```

Push notification setup:

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
.\backend\.venv\Scripts\python.exe -m py_vapid --gen
.\backend\.venv\Scripts\python.exe -m py_vapid --applicationServerKey --private-key private_key.pem
```

Use the `Application Server Key` output as `VAPID_PUBLIC_KEY`.

Use the private key body from `private_key.pem` as `VAPID_PRIVATE_KEY`, without these lines:

```text
-----BEGIN PRIVATE KEY-----
-----END PRIVATE KEY-----
```

Join the remaining private-key lines into one line before pasting into Render. Set `VAPID_SUBJECT` to your email in this format:

```text
mailto:you@example.com
```

Do not commit `private_key.pem` or `public_key.pem`.

Background refresh on free hosting:

Render free can sleep when inactive, so MatchNest includes a tiny Cloudflare Worker cron template at:

```text
infra/cloudflare/matchnest-cron-worker.js
```

Create one random token, set the same value in both places:

```text
Render env:      NOTIFICATION_DISPATCH_TOKEN
Cloudflare env:  NOTIFICATION_DISPATCH_TOKEN
```

Set this Cloudflare Worker env too:

```text
MATCHNEST_URL=https://your-render-service.onrender.com
```

Configure the Worker cron trigger:

```text
* * * * *
```

The Worker calls:

```text
/health
/background/refresh
/notifications/dispatch
```

That keeps the free Render service warm most of the time, refreshes followed calendars in the background, and dispatches due push notifications without waiting for the PWA screen to request data.

## Run tests

```powershell
$env:PYTHONPATH='F:\Education\match-nest\backend'
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests
```

## iOS

See `ios/README.md`.

## Mac and iPhone setup

See `docs/SETUP_MAC.md`.

## Architecture

See `docs/ARCHITECTURE.md`.

## Current data

The backend uses real provider adapters:

- Formula 1 via Jolpica Ergast-compatible API.
- Football via ESPN public soccer endpoints for tokenless future fixtures, including followed teams and followed tournaments. football-data.org remains available when `FOOTBALL_DATA_TOKEN` is set, but its free plan can restrict team-level national-team match endpoints. API-Football is optional and disabled by default because its free plan can reject modern/future seasons; set `API_FOOTBALL_ENABLE=1` only for a paid plan or explicit testing. TheSportsDB remains in code as a fallback adapter, but is not used in the normal provider cycle to avoid duplicate fixtures.
- CS2 fixtures/results via PandaScore when `PANDASCORE_TOKEN` is set. CS upcoming, past, and running match endpoints are available to all plans.
- Deep CS2 map/player/team statistics need a dedicated stats provider. `GRID_API_TOKEN` is reserved for GRID Open Access after access is approved.

Demo fallback is disabled unless `MATCHNEST_ALLOW_DEMO_EVENTS=1`.

The only `.env` file used by the backend is the repository root file:

```text
match-nest/.env
```

## Explore and provider bindings

Explore is the home for managing what MatchNest follows.

- Search can return local MatchNest entities and provider candidates.
- Custom entities are saved in SQLite per user.
- Seed entities can be moved between follow levels or hidden.
- Custom entities can be edited or deleted by their owner.
- Provider bindings connect an entity to external IDs.

Binding format in the UI:

```text
provider:type=value
```

Examples:

```text
api-football:team_id=772
espn:team_id=83
pandascore:team_id=3214
espn:league_slug=uefa.champions
```
