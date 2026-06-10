# MatchNest

Personal sports calendar for iPhone.

MatchNest tracks followed events across Formula 1, NAVI CS2 and football, with spoiler-safe past results, previous and future calendar months, and an iOS widget for the next event.

## Stack

- Backend: Python, FastAPI
- iOS: Swift, SwiftUI
- Widget: WidgetKit

## MVP scope

- Main feed: Formula 1, NAVI CS2, Ukraine NT, Barcelona
- Starred feed: Ferrari, UCL, FIFA World Cup, UEFA Euro, CS2 Majors, IEM, BLAST
- Timeline ranges: today, week, month
- Calendar: previous and future months
- Spoiler mode: hide past results until revealed
- Widget: next followed event

## Run backend

Windows:

```powershell
cd backend
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload
```

macOS:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

## Run tests

```bash
cd backend
PYTHONPATH=. python -m unittest discover -s tests
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
- Football via football-data.org when `FOOTBALL_DATA_TOKEN` is set. The free plan includes fixtures and delayed schedules.
- CS2 via PandaScore when `PANDASCORE_TOKEN` is set. CS upcoming, past, and running match endpoints are available to all plans.

Demo fallback is disabled unless `MATCHNEST_ALLOW_DEMO_EVENTS=1`.
