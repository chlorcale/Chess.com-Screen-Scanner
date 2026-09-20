from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class SettingsPanel(ttk.Frame):
    def __init__(self, parent, side_var: tk.StringVar, auto_var: tk.BooleanVar, on_side, on_auto):
        super().__init__(parent)

        ttk.Label(self, text="Phân tích nước đi:").grid(row=0, column=0, sticky="w", padx=(0, 8))

        for col, (value, text) in enumerate((
            ("white", "Trắng"),
            ("black", "Đen"),
            ("both", "Cả hai"),
        ), start=1):
            ttk.Radiobutton(
                self,
                text=text,
                value=value,
                variable=side_var,
                command=on_side,
            ).grid(row=0, column=col, sticky="w", padx=4)

        ttk.Checkbutton(
            self,
            text="Tự động",
            variable=auto_var,
            command=on_auto,
        ).grid(row=0, column=4, sticky="w", padx=(12, 0))
