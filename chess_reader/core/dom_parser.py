from __future__ import annotations

import re
from collections.abc import Iterable

from .models import DomSnapshot, MoveRow

RESULT_TOKENS = {"1-0", "0-1", "1/2-1/2", "*"}
MOVE_NO_RE = re.compile(r"^(\d+)\.{1,3}$")


def normalize_san(token: str) -> str:
    token = (token or "").strip()
    token = token.replace("–", "-").replace("—", "-")
    token = token.replace("×", "x")
    token = token.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    token = token.replace("o-o-o", "O-O-O").replace("o-o", "O-O")
    return token.rstrip("!?+#").strip() if token not in ("+", "#") else token


def rows_to_text(rows: Iterable[MoveRow]) -> str:
    out: list[str] = []
    for row in rows:
        if row.white:
            out.append(f"{row.number}. {row.white}")
        if row.black:
            out.append(row.black)
    return " ".join(out)


def snapshot_from_payload(payload: dict) -> DomSnapshot:
    rows_raw = payload.get("rows") or []
    rows: list[MoveRow] = []

    for item in rows_raw:
        try:
            number = int(item.get("number"))
            if number <= 0:
                continue
            rows.append(
                MoveRow(
                    number=number,
                    white=normalize_san(str(item.get("white") or "")),
                    black=normalize_san(str(item.get("black") or "")),
                )
            )
        except (AttributeError, TypeError, ValueError):
            continue

    rows.sort(key=lambda x: x.number)
    text = rows_to_text(rows)

    return DomSnapshot(
        rows=tuple(rows),
        text=text,
        board_id=payload.get("boardId"),
        url=payload.get("url"),
        error=payload.get("error"),
        timestamp=payload.get("timestamp"),
    )
