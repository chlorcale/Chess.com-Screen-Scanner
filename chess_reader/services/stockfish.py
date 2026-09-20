from __future__ import annotations

import os
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import chess
import chess.engine


@dataclass(frozen=True)
class AnalysisJob:
    key: str
    board: chess.Board
    move_number: int
    mover: str
    seconds: float
    multipv: int
    source_text: str
    generation: int


class StockfishService:
    """One worker thread owns one Stockfish process; latest live job wins."""

    def __init__(self, engine_path: str | None, emit: Callable[[str, dict], None]):
        self.engine_path = engine_path
        self.emit = emit
        self._cv = threading.Condition()
        self._pending: AnalysisJob | None = None
        self._current: AnalysisJob | None = None
        self._preempt = False
        self._stopping = False
        self._engine: chess.engine.SimpleEngine | None = None
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._thread = threading.Thread(
            target=self._run,
            name="stockfish-worker",
            daemon=True,
        )
        self._thread.start()

    def submit(self, job: AnalysisJob):
        with self._cv:
            self._pending = job
            if self._current is not None:
                self._preempt = True
            self._cv.notify_all()

    def clear(self):
        with self._cv:
            self._pending = None
            self._preempt = True
            self._cv.notify_all()

    def shutdown(self):
        with self._cv:
            self._stopping = True
            self._pending = None
            self._preempt = True
            self._cv.notify_all()

        if self._thread.is_alive():
            self._thread.join(timeout=3.0)

        self._quit_engine()

    def _run(self):
        while True:
            with self._cv:
                while not self._stopping and self._pending is None:
                    self._cv.wait()

                if self._stopping:
                    return

                job = self._pending
                self._pending = None
                self._current = job
                self._preempt = False

            if job is None:
                continue

            try:
                self._process(job)
            except Exception as exc:
                self.emit(
                    "error",
                    {
                        "key": job.key,
                        "message": str(exc),
                        "engine_path": self.engine_path,
                    },
                )
                self._quit_engine()
            finally:
                with self._cv:
                    self._current = None

    def _process(self, job: AnalysisJob):
        cached = self._cache.get(job.key)
        if cached is not None:
            self.emit("result", dict(cached))
            return

        engine = self._ensure_engine()

        self.emit(
            "started",
            {
                "key": job.key,
                "move_number": job.move_number,
                "mover": job.mover,
            },
        )

        limit = chess.engine.Limit(time=max(0.05, job.seconds))

        with engine.analysis(
            job.board,
            limit,
            multipv=max(1, job.multipv),
        ) as analysis:
            best_infos: list[dict] = []
            for info in analysis:
                if self._should_preempt():
                    try:
                        analysis.stop()
                    except Exception:
                        pass
                    return

                best_infos = info if isinstance(info, list) else [info]
                if isinstance(best_infos, list) and best_infos:
                    first = best_infos[0]
                    if "pv" in first:
                        self.emit(
                            "depth",
                            {
                                "key": job.key,
                                "depth": first.get("depth", "?"),
                            },
                        )

            if not best_infos:
                return

        if self._should_preempt():
            return

        lines = []
        for info in best_infos[: max(1, job.multipv)]:
            pv = info.get("pv") or []
            try:
                san_pv = _pv_to_san(job.board, pv)
            except Exception:
                san_pv = ()
            score = info.get("score")
            white_score = score.white() if score is not None else None
            evaluation = _format_score(white_score)
            best_move = san_pv[0] if san_pv else "?"
            best_uci = pv[0].uci() if pv else ""
            lines.append(
                {
                    "best_move": best_move,
                    "best_uci": best_uci,
                    "evaluation": evaluation,
                    "depth": info.get("depth", "?"),
                    "pv": san_pv,
                }
            )

        if not lines:
            return

        payload = {
            "key": job.key,
            "move_number": job.move_number,
            "mover": job.mover,
            "source_text": job.source_text,
            "fen": job.board.fen(),
            "lines": lines,
        }
        self._cache[job.key] = payload
        self._cache.move_to_end(job.key)
        while len(self._cache) > 100:
            self._cache.popitem(last=False)

        self.emit("result", payload)

    def _should_preempt(self) -> bool:
        with self._cv:
            return self._preempt or self._pending is not None or self._stopping

    def _ensure_engine(self) -> chess.engine.SimpleEngine:
        if self._engine is not None:
            return self._engine

        if not self.engine_path:
            raise FileNotFoundError(
                "Không tìm thấy stockfish.exe trong thư mục stockfish."
            )

        path = Path(self.engine_path)
        if not path.is_file():
            raise FileNotFoundError(f"Stockfish không tồn tại: {path}")

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self.emit("status", {"message": f"Khởi động Stockfish: {path}"})
        self._engine = chess.engine.SimpleEngine.popen_uci(
            str(path),
            timeout=10,
            creationflags=creationflags,
        )

        for name, value in (("Threads", 2), ("Hash", 256)):
            try:
                if name in self._engine.options:
                    self._engine.configure({name: value})
            except Exception:
                pass

        self.emit("ready", {"engine_path": str(path)})
        return self._engine

    def _quit_engine(self):
        engine = self._engine
        self._engine = None
        if engine is None:
            return
        try:
            engine.quit()
        except Exception:
            try:
                engine.close()
            except Exception:
                pass


def _pv_to_san(board: chess.Board, pv: list[chess.Move]) -> tuple[str, ...]:
    work = board.copy(stack=False)
    out: list[str] = []
    for move in pv:
        out.append(work.san(move))
        work.push(move)
    return tuple(out)


def _format_score(score: chess.engine.PovScore | None) -> str:
    if score is None:
        return "?"
    if score.is_mate():
        mate = score.mate()
        return f"M{abs(mate)} {'Trắng' if mate and mate > 0 else 'Đen'}"
    centipawn = score.score(mate_score=100000)
    return f"{centipawn / 100:+.2f}"
