from __future__ import annotations

from typing import Protocol

from ..models import Offer


class Provider(Protocol):
    name: str

    def search(self, query: str, limit: int = 20) -> list[Offer]: ...
