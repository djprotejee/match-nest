# MatchNest Architecture

## Product shape

MatchNest is a personal sports calendar with a PWA, a future native iPhone app, a future WidgetKit widget, and a small Python backend that normalizes sports data from multiple providers into one event model.

## Backend responsibilities

- Fetch real event data from provider adapters.
- Normalize provider payloads into `Event`.
- Apply user visibility levels: `main`, `starred`, `muted`, `hidden`, `explore`.
- Strip spoiler-sensitive results before responses reach the iOS app or widget.
- Serve timeline, month calendar, and next-widget payload endpoints.

## iOS responsibilities

- Render the timeline, calendar, and settings views.
- Keep the UI compact and spoiler-safe by default.
- Write a compact next-event payload into the shared app group store for WidgetKit.

## Web PWA responsibilities

- Provide the current primary client while native iOS tooling is blocked.
- Render the same timeline, calendar, explore, and settings structure.
- Cache the app shell through a service worker.
- Support Add to Home Screen on iPhone through the web app manifest.
- In production, the PWA is built into `web/dist` and served by FastAPI on the same origin as the API.

## Widget responsibilities

- Read the latest next-event snapshot from the shared app group.
- Render small and medium next-event widgets.
- Stay lightweight. Network sync belongs in the main app or backend, not in the widget.

## Provider strategy

Providers live behind `EventProvider`.

- `JolpicaF1Provider` fetches the current F1 calendar without a token.
- `FootballDataProvider` fetches football fixtures when `FOOTBALL_DATA_TOKEN` is set.
- `PandaScoreCS2Provider` fetches CS2 running, upcoming, and past fixtures when `PANDASCORE_TOKEN` is set.

The backend should not send provider tokens to the iOS app.

## Data safety

Spoiler mode is enforced on the backend response. This prevents accidental leaks in:

- timeline cards
- calendar event lists
- widgets
- future notification payloads
