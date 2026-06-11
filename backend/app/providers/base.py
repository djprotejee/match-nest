from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..models import Event


class EventProvider(ABC):
    @abstractmethod
    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[Event]:
        raise NotImplementedError
