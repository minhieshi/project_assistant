from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    text: str
    metadata: dict
    score: float | None
    channels: tuple[str, ...] = ()
