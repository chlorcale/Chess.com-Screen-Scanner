from __future__ import annotations

import re
from dataclasses import dataclass

import chess

from .models import DomSnapshot


SAN_TOKEN_RE = re.compile(
    r"^(?:O-O-O|O-O|[KQRBN][a-h1-8x+#=\-]*|[a-h][1-8](?:=[QRBN])?[+#]?|[a-h]x[a-h][1-8](?:=[QRBN])?[+#]?)$"
)


@dataclass(frozen=True)
class PositionState:
    board: chess.Board
    plies: int
    mover: str
    last_move_number: int


def build_board(snapshot: DomSnapshot) -> PositionState:
    board = chess.Board()
    plies = 0
    last_move_number = 0

    for row in snapshot.rows:
        if row.white:
            _push_san(board, row.white, snapshot, row.number, "white")
            plies += 1
        if row.black:
            _push_san(board, row.black, snapshot, row.number, "black")
            plies += 1
        last_move_number = row.number

    mover = "white" if board.turn == chess.WHITE else "black"
    return PositionState(
        board=board,
        plies=plies,
        mover=mover,
        last_move_number=last_move_number,
    )


def _push_san(
    board: chess.Board,
    token: str,
    snapshot: DomSnapshot,
    move_number: int,
    mover: str,
) -> chess.Move:
    san = token.strip()
    if not san:
        raise ValueError("SAN rỗng")

    try:
        move = board.parse_san(san)
    except ValueError as exc:
        # Một vài ký hiệu UI có thể còn suffix !?; bỏ suffix rồi thử lại.
        cleaned = re.sub(r"[!?]+$", "", san)
        if cleaned != san:
            try:
                move = board.parse_san(cleaned)
                san = cleaned
            except ValueError:
                raise ValueError(
                    f"SAN không hợp lệ ở nước {move_number} ({mover}): {san}\n"
                    f"FEN: {board.fen()}\n"
                    f"Danh sách DOM: {snapshot.text}"
                ) from exc
        else:
            raise ValueError(
                f"SAN không hợp lệ ở nước {move_number} ({mover}): {san}\n"
                f"FEN: {board.fen()}\n"
                f"Danh sách DOM: {snapshot.text}"
            ) from exc

    board.push(move)
    return move


def side_to_move(board: chess.Board) -> str:
    return "white" if board.turn == chess.WHITE else "black"


def pretty_eval(info_score: chess.engine.PovScore) -> str:
    score = info_score.white()
    if score.is_mate():
        mate = score.mate()
        return f"M{abs(mate)} {'Trắng' if mate and mate > 0 else 'Đen'}"
    value = score.score(mate_score=100000)
    return f"{value / 100:+.2f}"


def pv_to_san(board: chess.Board, pv: list[chess.Move]) -> tuple[str, ...]:
    work = board.copy(stack=False)
    result: list[str] = []
    for move in pv:
        try:
            result.append(work.san(move))
            work.push(move)
        except ValueError:
            break
    return tuple(result)
