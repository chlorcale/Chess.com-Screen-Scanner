from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MoveRow:
    number: int
    white: str = ""
    black: str = ""


@dataclass(frozen=True)
class DomSnapshot:
    rows: tuple[MoveRow, ...] = ()
    text: str = ""
    board_id: str | None = None
    url: str | None = None
    error: str | None = None
    timestamp: int | None = None


@dataclass(frozen=True)
class AnalysisResult:
    key: str
    move_number: int
    mover: str
    best_move: str
    best_uci: str
    evaluation: str
    depth: int | str
    pv: tuple[str, ...] = ()
    source_text: str = ""
    fen: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
