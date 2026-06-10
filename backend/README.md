# MatchNest Backend

Python API for the MatchNest personal sports calendar.

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
- `GET /entities`
- `GET /events?range=today|week|month`
- `GET /timeline?range=today|week|month`
- `GET /calendar/{year}/{month}`
- `GET /widget/next`
- `PUT /follows/{entity_id}`
- `PUT /settings/f1-sessions`

## Real data providers

MatchNest uses real providers by default.

- Formula 1: Jolpica Ergast-compatible API, no token required.
- Football: football-data.org, set `FOOTBALL_DATA_TOKEN`.
- CS2: PandaScore, set `PANDASCORE_TOKEN`.

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
