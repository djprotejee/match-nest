from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Event


class EventProvider(ABC):
    @abstractmethod
    def fetch(self) -> list[Event]:
        raise NotImplementedError

