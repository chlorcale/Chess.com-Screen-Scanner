from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "chess_reader_config.json"
STOCKFISH_DIR = APP_DIR / "stockfish"
DEFAULT_STOCKFISH_PATH = STOCKFISH_DIR / "stockfish.exe"

APP_TITLE = "Chess Move Reader — DOM"
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8765
DEFAULT_ENGINE_TIME = 0.50
DEFAULT_MULTIPV = 1
ANALYSIS_HISTORY_LIMIT = 100


def find_stockfish() -> str | None:
    candidates = [
        DEFAULT_STOCKFISH_PATH,
        STOCKFISH_DIR / "stockfish-windows-x86-64-universal.exe",
        APP_DIR / "stockfish.exe",
    ]

    for p in candidates:
        if p.is_file():
            return str(p.resolve())

    for p in STOCKFISH_DIR.glob("*.exe"):
        if "stockfish" in p.name.lower():
            return str(p.resolve())

    return None
