# MatchNest Backend

Python API for the MatchNest personal sports calendar.

## Environment

Use only the repository root environment file:

```text
match-nest/.env
```

Copy it from:

```text
match-nest/.env.example
```

Storage mode:

- `DATABASE_URL` empty: local SQLite at `backend/.data/matchnest.sqlite`.
- `DATABASE_URL` set: Postgres, recommended for Render free or any hosted deployment.

## Run

Windows:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload
```

macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Test

```bash
PYTHONPATH=. python -m unittest discover -s tests
```

## Current API

- `GET /health`
- `GET /sources`
- `GET /entities`
- `GET /entities/search?q=barcelona`
- `GET /entities/{entity_id}`
- `POST /entities/custom`
- `PUT /entities/{entity_id}`
- `DELETE /entities/{entity_id}`
- `GET /events?range=today|week|month`
- `GET /timeline?range=today|week|month`
- `GET /calendar/{year}/{month}`
- `GET /widget/next`
- `PUT /follows/{entity_id}`
- `PUT /settings/f1-sessions`
- `POST /auth/register`
- `POST /auth/verify-email`
- `GET /auth/verify-email`
- `POST /auth/login`
- `GET /auth/me`
- `POST /auth/logout`
- `GET /auth/google/start`
- `GET /auth/google/callback`

## Real data providers

MatchNest uses real providers by default.

- Formula 1: Jolpica Ergast-compatible API, no token required.
- Football: ESPN public soccer endpoints, football-data.org, API-Football, and TheSportsDB fallback.
- CS2: PandaScore, set `PANDASCORE_TOKEN`.
- F1 classification details: FastF1 where available, cached under `.data/fastf1-cache`.

Demo events are disabled by default. To allow demo fallback while developing:

```powershell
$env:MATCHNEST_ALLOW_DEMO_EVENTS='1'
```

## Data model

The app is built around `Entity` and `Follow` instead of hardcoded teams.

- `main` - primary feed and default notifications
- `starred` - extra important events
- `muted` - visible without notifications
- `hidden` - excluded
- `explore` - searchable, not followed yet

Entities are stored in SQLite and can include aliases and provider bindings. Seed entities are synced into the database on startup. User-created entities can be edited and deleted by their owner.

Provider binding format:

```text
provider:type=value
```

Supported examples:

```text
api-football:team_id=772
espn:team_id=83
espn:league_slug=uefa.champions
pandascore:team_id=3214
```

## Auth

Email/password auth stores password hashes and sessions in SQLite. Email verification uses SMTP when configured. If SMTP is not configured, development verification links are written to:

```text
backend/.data/dev-emails.log
```

Google login requires:

```text
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/auth/google/callback
APP_PUBLIC_URL=http://127.0.0.1:8000
```
