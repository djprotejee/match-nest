from __future__ import annotations

import os

from .base import EventProvider
from .f1_jolpica import JolpicaF1Provider
from .football_data import FootballDataProvider
from .pandascore import PandaScoreCS2Provider
from ..models import Event
from ..seed import demo_events


def configured_providers() -> list[EventProvider]:
    return [
        JolpicaF1Provider(),
        FootballDataProvider(),
        PandaScoreCS2Provider(),
    ]


def fetch_events() -> list[Event]:
    # Real providers are the default. Demo data is only a local development
    # fallback and must be explicitly enabled through MATCHNEST_ALLOW_DEMO_EVENTS.
    events: list[Event] = []
    errors: list[str] = []
    for provider in configured_providers():
        try:
            events.extend(provider.fetch())
        except Exception as exc:
            errors.append(f"{provider.__class__.__name__}: {exc}")

    if not events and os.getenv("MATCHNEST_ALLOW_DEMO_EVENTS") == "1":
        return demo_events()
    return events
