from __future__ import annotations

import json
import queue
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from .config import (
    APP_TITLE,
    BRIDGE_HOST,
    BRIDGE_PORT,
    CONFIG_PATH,
    DEFAULT_ENGINE_TIME,
    DEFAULT_MULTIPV,
    find_stockfish,
)
from .controllers.analysis_controller import AnalysisController
from .controllers.dom_controller import DomController
from .core.models import AnalysisResult, DomSnapshot
from .ui.main_window import MainWindow


class ChessMoveReader(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("720x760")
        self.minsize(620, 650)

        self._closing = False
        self.ui_queue: queue.Queue[tuple] = queue.Queue()
        self.history_limit = 100
        self.history: list[AnalysisResult] = []

        cfg = self._load_config()
        self.engine_time = float(cfg.get("engine_time", DEFAULT_ENGINE_TIME))
        self.multipv = int(cfg.get("multipv", DEFAULT_MULTIPV))
        self.side_var = tk.StringVar(value=cfg.get("side", "both"))
        self.auto_var = tk.BooleanVar(value=bool(cfg.get("auto", True)))

        self.current_snapshot = DomSnapshot()
        self.current_result: AnalysisResult | None = None

        self.dom = DomController(
            BRIDGE_HOST,
            BRIDGE_PORT,
            self._on_dom_snapshot,
        )
        self.analysis = AnalysisController(
            find_stockfish(),
            self._analysis_event,
            lambda result: self.post_ui(self._on_analysis_result, result),
        )

        self.window = MainWindow(
            self,
            {
                "toggle_bridge": self.toggle_bridge,
                "copy_script": self.copy_script,
                "reanalyze": self.reanalyze,
                "clear": self.clear,
                "side_var": self.side_var,
                "auto_var": self.auto_var,
                "side_changed": self.on_side_changed,
                "auto_changed": self.on_auto_changed,
            },
        )
        self.window.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(30, self._drain_ui_queue)

        self._set_status(
            f"Stockfish: {find_stockfish() or 'KHÔNG TÌM THẤY'}"
        )

    # ---------------------------------------------------------
    # UI dispatch
    # ---------------------------------------------------------

    def post_ui(self, fn, *args):
        if self._closing:
            return
        self.ui_queue.put((fn, args))

    def _drain_ui_queue(self):
        if self._closing:
            return
        for _ in range(200):
            try:
                fn, args = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception as exc:
                print(f"[UI] callback error: {exc}")
        if not self._closing:
            self.after(30, self._drain_ui_queue)

    # ---------------------------------------------------------
    # Bridge
    # ---------------------------------------------------------

    def toggle_bridge(self):
        try:
            if self.dom.bridge.running:
                self.dom.stop()
                self.window.bridge_button.configure(text="Khởi động DOM Bridge")
                self._set_status("DOM Bridge đã dừng")
            else:
                self.dom.start()
                self.window.bridge_button.configure(text="Dừng DOM Bridge")
                self._set_status(
                    f"DOM Bridge đang chạy tại {self.dom.address()} — paste bridge/chess_dom_reader.js vào Console."
                )
        except OSError as exc:
            messagebox.showerror(
                "DOM Bridge",
                f"Không mở được {self.dom.address()}:\n{exc}\n\n" 
                "Port có thể đang được tiến trình khác sử dụng.",
            )

    def copy_script(self):
        path = Path(__file__).resolve().parent.parent / "bridge" / "chess_dom_reader.js"
        try:
            text = path.read_text(encoding="utf-8")
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            self._set_status("Đã copy chess_dom_reader.js. Paste vào Console của tab Chess.com rồi Enter.")
        except Exception as exc:
            messagebox.showerror("Copy JavaScript", str(exc))

    # ---------------------------------------------------------
    # DOM events
    # ---------------------------------------------------------

    def _on_dom_snapshot(self, snapshot: DomSnapshot):
        self.post_ui(self._handle_dom_snapshot, snapshot)

    def _handle_dom_snapshot(self, snapshot: DomSnapshot):
        self.current_snapshot = snapshot
        self.window.moves_text.delete("1.0", "end")
        self.window.moves_text.insert("1.0", snapshot.text)

        if snapshot.error:
            self._set_status(f"DOM: {snapshot.error}")
            return

        self._set_status(
            f"DOM OK • {len(snapshot.rows)} dòng • {sum(bool(r.white) + bool(r.black) for r in snapshot.rows)} ply"
        )

        if self.auto_var.get():
            self.analysis.on_snapshot(snapshot)

    # ---------------------------------------------------------
    # Analysis events
    # ---------------------------------------------------------

    def _on_analysis_result(self, result: AnalysisResult):
        self.history.append(result)
        self.history = self.history[-self.history_limit :]
        self._refresh_history()

    def _handle_analysis_result(self, result: AnalysisResult):
        self.current_result = result
        side = "Trắng" if result.mover == "white" else "Đen"
        self.window.best_move_var.set(result.best_move)
        self.window.mover_var.set(f"Bên đi: {side}")
        self.window.eval_var.set(f"Eval: {result.evaluation}")
        self.window.depth_var.set(f"Depth: {result.depth}")
        self.window.pv_var.set(
            "PV: " + (" ".join(result.pv) if result.pv else "—")
        )
        self._set_status(
            f"Stockfish xong • {side} • best {result.best_move} • {result.evaluation}"
        )

    def _handle_engine_event(self, kind: str, payload: dict):
        if kind == "result":
            result = payload["result"]
            self._handle_analysis_result(result)
            return
        if kind == "started":
            side = "Trắng" if payload.get("mover") == "white" else "Đen"
            self._set_status(
                f"Đang phân tích nước tiếp theo cho {side}..."
            )
            return
        if kind == "depth":
            self._set_status(
                f"Đang phân tích... depth {payload.get('depth', '?')}"
            )
            return
        if kind == "ready" or kind == "status":
            self._set_status(payload.get("message") or f"Stockfish sẵn sàng: {payload.get('engine_path', '')}")
            return
        if kind == "queue":
            side = "Trắng" if payload.get("mover") == "white" else "Đen"
            self._set_status(
                f"Đã nhận DOM • chuẩn bị phân tích cho {side}..."
            )
            return
        if kind == "filtered":
            return
        if kind == "parse_error":
            self._set_status(f"DOM parse lỗi: {payload.get('message', '')}")
            return
        if kind == "dom_error":
            self._set_status(payload.get("message", "DOM error"))
            return
        if kind == "error":
            self._set_status(
                f"Stockfish lỗi: {payload.get('message', '')}"
            )
            return

    def _analysis_emit_adapter(self, kind: str, payload: dict):
        self.post_ui(self._handle_engine_event, kind, payload)

    def _analysis_event(self, kind: str, payload: dict):
        self.post_ui(self._handle_engine_result_event, kind, payload)

    def _handle_engine_result_event(self, kind: str, payload: dict):
        if kind == "result":
            result = payload["result"]
            self._handle_analysis_result(result)
            return
        if kind == "started":
            side = "Trắng" if payload.get("mover") == "white" else "Đen"
            self._set_status(f"Đang phân tích nước tiếp theo cho {side}...")
            return
        if kind == "depth":
            self._set_status(f"Đang phân tích... depth {payload.get('depth', '?')}")
            return
        if kind in {"ready", "status"}:
            self._set_status(payload.get("message") or f"Stockfish sẵn sàng: {payload.get('engine_path', '')}")
            return
        if kind == "queue":
            side = "Trắng" if payload.get("mover") == "white" else "Đen"
            self._set_status(f"Đã nhận DOM • chuẩn bị phân tích cho {side}...")
            return
        if kind == "filtered":
            return
        if kind == "parse_error":
            self._set_status(f"DOM parse lỗi: {payload.get('message', '')}")
            return
        if kind == "dom_error":
            self._set_status(payload.get("message", "DOM error"))
            return
        if kind == "error":
            self._set_status(f"Stockfish lỗi: {payload.get('message', '')}")
            return

    # ---------------------------------------------------------
    # Settings
    # ---------------------------------------------------------

    def on_side_changed(self):
        side = self.side_var.get()
        self.analysis.set_side(side)
        self._save_config()

    def on_auto_changed(self):
        enabled = bool(self.auto_var.get())
        self.analysis.set_enabled(enabled)
        self._save_config()
        self._set_status("Tự động phân tích: BẬT" if enabled else "Tự động phân tích: TẮT")

    # ---------------------------------------------------------
    # Commands
    # ---------------------------------------------------------

    def reanalyze(self):
        self.analysis.clear()
        if self.current_snapshot.text and self.auto_var.get():
            self.analysis.on_snapshot(self.current_snapshot)
        else:
            self._set_status("Đã reset phân tích")

    def clear(self):
        self.analysis.clear()
        self.current_snapshot = DomSnapshot()
        self.current_result = None
        self.history.clear()
        self.window.moves_text.delete("1.0", "end")
        self.window.best_move_var.set("—")
        self.window.eval_var.set("Eval: —")
        self.window.depth_var.set("Depth: —")
        self.window.mover_var.set("Bên đi: —")
        self.window.pv_var.set("PV: —")
        self._refresh_history()
        self._set_status("Đã xóa")

    def _refresh_history(self):
        for item in self.window.history.get_children():
            self.window.history.delete(item)
        for result in reversed(self.history):
            self.window.history.insert(
                "",
                "end",
                values=(
                    result.move_number,
                    "Trắng" if result.mover == "white" else "Đen",
                    result.best_move,
                    result.evaluation,
                    result.depth,
                ),
            )

    # ---------------------------------------------------------
    # Status/config
    # ---------------------------------------------------------

    def _set_status(self, text: str):
        try:
            self.window.status_var.set(str(text))
        except tk.TclError:
            pass

    def _load_config(self):
        try:
            return json.loads(Path(CONFIG_PATH).read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_config(self):
        data = {
            "side": self.side_var.get(),
            "auto": bool(self.auto_var.get()),
            "engine_time": float(self.engine_time.get()),
            "multipv": self.multipv,
        }
        try:
            Path(CONFIG_PATH).write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ---------------------------------------------------------
    # Close
    # ---------------------------------------------------------

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        try:
            self.dom.stop()
        except Exception:
            pass
        try:
            self.analysis.shutdown()
        except Exception as exc:
            print(f"[ENGINE] shutdown: {exc}")
        try:
            self._save_config()
        except Exception:
            pass
        self.destroy()

