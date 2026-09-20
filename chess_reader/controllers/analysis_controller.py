from __future__ import annotations

import hashlib
import threading
from typing import Callable

from ..core.chess_logic import build_board, side_to_move
from ..core.models import AnalysisResult, DomSnapshot
from ..services.stockfish import AnalysisJob, StockfishService


class AnalysisController:
    def __init__(
        self,
        engine_path: str | None,
        emit_ui: Callable[[str, dict], None],
        result_callback: Callable[[AnalysisResult], None],
    ):
        self.emit_ui = emit_ui
        self.result_callback = result_callback
        self.analysis_side = "both"
        self.enabled = True
        self.generation = 0
        self.engine = StockfishService(engine_path, self._engine_event)
        self._lock = threading.Lock()
        self._last_key = ""
        self._last_snapshot = DomSnapshot()

    def shutdown(self):
        self.engine.shutdown()

    def clear(self):
        with self._lock:
            self.generation += 1
            self._last_key = ""
            self._last_snapshot = DomSnapshot()
        self.engine.clear()

    def set_side(self, side: str):
        if side not in {"white", "black", "both"}:
            side = "both"
        self.analysis_side = side
        current = self._last_snapshot
        if current.text:
            self.on_snapshot(current)

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.engine.clear()

    def on_snapshot(self, snapshot: DomSnapshot):
        self._last_snapshot = snapshot
        if not self.enabled:
            return
        if snapshot.error:
            self.emit_ui("dom_error", {"message": snapshot.error})
            return
        if not snapshot.rows:
            return

        try:
            state = build_board(snapshot)
        except Exception as exc:
            self.emit_ui("parse_error", {"message": str(exc)})
            return

        mover = side_to_move(state.board)
        if self.analysis_side != "both" and mover != self.analysis_side:
            self.emit_ui(
                "filtered",
                {"mover": mover, "side": self.analysis_side},
            )
            return

        key_material = snapshot.text + "|" + state.board.fen() + "|" + self.analysis_side
        key = hashlib.sha1(key_material.encode("utf-8")).hexdigest()

        with self._lock:
            if key == self._last_key:
                return
            self._last_key = key
            generation = self.generation

        move_number = state.last_move_number + (1 if mover == "white" else 0)
        job = AnalysisJob(
            key=key,
            board=state.board.copy(stack=False),
            move_number=move_number,
            mover=mover,
            seconds=0.5,
            multipv=1,
            source_text=snapshot.text,
            generation=generation,
        )
        self.emit_ui(
            "queue",
            {"mover": mover, "move_number": move_number},
        )
        self.engine.submit(job)

    def _engine_event(self, kind: str, payload: dict):
        if kind == "result":
            lines = payload.get("lines") or []
            if not lines:
                return
            line = lines[0]
            result = AnalysisResult(
                key=payload["key"],
                move_number=int(payload.get("move_number", 0)),
                mover=str(payload.get("mover", "")),
                best_move=str(line.get("best_move", "?")),
                best_uci=str(line.get("best_uci", "")),
                evaluation=str(line.get("evaluation", "?")),
                depth=line.get("depth", "?"),
                pv=tuple(line.get("pv") or ()),
                source_text=str(payload.get("source_text", "")),
                fen=str(payload.get("fen", "")),
                raw=payload,
            )
            self.emit_ui("result", {"result": result})
            # result_callback is retained as an extension hook; callers that
            # touch Tkinter must wrap it with their UI queue.
            if self.result_callback is not None:
                self.result_callback(result)
            return

        self.emit_ui(kind, payload)
