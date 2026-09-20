from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .settings_panel import SettingsPanel


class MainWindow(ttk.Frame):
    def __init__(self, parent, callbacks):
        super().__init__(parent, padding=12)
        self.callbacks = callbacks
        self._history_rows: list[str] = []
        self._build()

    def _build(self):
        title = ttk.Frame(self)
        title.pack(fill="x")

        ttk.Label(
            title,
            text="CHESS MOVE READER — DOM",
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")

        self.bridge_button = ttk.Button(
            title,
            text="Khởi động DOM Bridge",
            command=self.callbacks["toggle_bridge"],
        )
        self.bridge_button.pack(side="right")

        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(10, 8))

        ttk.Button(
            controls,
            text="Sao chép JavaScript",
            command=self.callbacks["copy_script"],
        ).pack(side="left")

        ttk.Button(
            controls,
            text="Làm mới phân tích",
            command=self.callbacks["reanalyze"],
        ).pack(side="left", padx=6)

        ttk.Button(
            controls,
            text="Xóa",
            command=self.callbacks["clear"],
        ).pack(side="left")

        settings = SettingsPanel(
            self,
            self.callbacks["side_var"],
            self.callbacks["auto_var"],
            self.callbacks["side_changed"],
            self.callbacks["auto_changed"],
        )
        settings.pack(fill="x", pady=(0, 8))

        status_frame = ttk.LabelFrame(self, text="Trạng thái")
        status_frame.pack(fill="x", pady=(0, 8))

        self.status_var = tk.StringVar(value="Sẵn sàng")
        ttk.Label(
            status_frame,
            textvariable=self.status_var,
            wraplength=500,
        ).pack(fill="x", padx=8, pady=8)

        best_frame = ttk.LabelFrame(self, text="Nước đi tốt nhất tiếp theo")
        best_frame.pack(fill="x", pady=(0, 8))

        self.best_move_var = tk.StringVar(value="—")
        self.eval_var = tk.StringVar(value="Eval: —")
        self.depth_var = tk.StringVar(value="Depth: —")
        self.mover_var = tk.StringVar(value="Bên đi: —")
        self.pv_var = tk.StringVar(value="PV: —")

        ttk.Label(
            best_frame,
            textvariable=self.best_move_var,
            font=("Segoe UI", 26, "bold"),
        ).pack(pady=(10, 2))

        meta = ttk.Frame(best_frame)
        meta.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(meta, textvariable=self.mover_var).pack(side="left")
        ttk.Label(meta, textvariable=self.eval_var).pack(side="left", padx=15)
        ttk.Label(meta, textvariable=self.depth_var).pack(side="left")

        ttk.Label(
            best_frame,
            textvariable=self.pv_var,
            wraplength=500,
        ).pack(fill="x", padx=10, pady=(0, 10))

        split = ttk.Panedwindow(self, orient="vertical")
        split.pack(fill="both", expand=True)

        moves_frame = ttk.LabelFrame(split, text="DOM plaintext")
        history_frame = ttk.LabelFrame(split, text="Lịch sử phân tích")
        split.add(moves_frame, weight=1)
        split.add(history_frame, weight=1)

        self.moves_text = tk.Text(
            moves_frame,
            height=7,
            wrap="word",
            font=("Consolas", 10),
        )
        self.moves_text.pack(fill="both", expand=True, padx=6, pady=6)

        columns = ("move", "side", "best", "eval", "depth")
        self.history = ttk.Treeview(
            history_frame,
            columns=columns,
            show="headings",
            height=8,
        )
        headings = {
            "move": "Nước",
            "side": "Bên",
            "best": "Best",
            "eval": "Eval",
            "depth": "Depth",
        }
        widths = {"move": 60, "side": 60, "best": 80, "eval": 80, "depth": 60}
        for col in columns:
            self.history.heading(col, text=headings[col])
            self.history.column(col, width=widths[col], anchor="center")
        self.history.pack(fill="both", expand=True, padx=6, pady=6)
