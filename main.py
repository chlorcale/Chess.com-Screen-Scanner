import os
import re
import shutil as file_shutil
import threading
import time
import tkinter as tk

from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import mss
import pytesseract

from PIL import (
    Image,
    ImageEnhance,
    ImageFilter,
    ImageOps,
    ImageTk,
)


APP_TITLE = "Chess Move Reader"
DEFAULT_INTERVAL = 0.6


# =========================================================
# TESSERACT AUTO DETECTION
# =========================================================

_tesseract = file_shutil.which("tesseract")

if not _tesseract:
    for _candidate in (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ):
        if _candidate.exists():
            _tesseract = str(_candidate)
            break

if _tesseract:
    pytesseract.pytesseract.tesseract_cmd = _tesseract


# =========================================================
# CHESS SAN PARSER
# =========================================================

SAN_RE = re.compile(
    r"^(?:"
    r"O-O-O|O-O|0-0-0|0-0|"
    r"[KQRBN](?:[a-h1-8]{0,2})?x?[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|[a-h]x[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|[a-h][1-8](?:=[QRBN])?[+#]?"
    r")$"
)

OCR_WHITELIST = "KQRBNabcdefgh12345678x+#=O-o.- "


def normalize_ocr(text: str) -> str:
    text = text.replace("\n", " ")
    text = text.replace("—", "-")
    text = text.replace("–", "-")

    text = text.replace("0–0", "O-O")
    text = text.replace("0—0", "O-O")

    text = re.sub(r"\s+", " ", text).strip()

    return text


def extract_san_tokens(text: str):
    text = normalize_ocr(text)

    # Remove move numbers
    text = re.sub(r"\b\d+\.(?:\.\.)?\s*", " ", text)

    # Remove game results
    text = re.sub(
        r"\b(?:1-0|0-1|1/2-1/2|\*)\b",
        " ",
        text,
    )

    raw = re.findall(
        r"[A-Za-z0-9+#=xX\-]+",
        text,
    )

    moves = []

    for token in raw:

        token = token.strip(
            ".,:;[](){}"
        )

        if not token:
            continue

        token = token.replace("X", "x")

        token = token.replace(
            "0-0-0",
            "O-O-O",
        )

        token = token.replace(
            "0-0",
            "O-O",
        )

        # Convert OCR lowercase piece letters
        if token[:1] in "kqrbn":
            token = (
                token[:1].upper()
                + token[1:]
            )

        if SAN_RE.match(token):
            moves.append(token)

    return moves


def score_observation(moves):

    if not moves:
        return -1

    score = len(moves)

    if len(moves) >= 2:
        score += 2

    if all(
        SAN_RE.match(x)
        for x in moves
    ):
        score += 1

    return score


# =========================================================
# MERGE OCR RESULTS
# =========================================================

def merge_moves(
    existing,
    observed,
):

    if not observed:
        return existing

    if not existing:
        return observed[:]

    # OCR currently sees entire history
    if observed[:len(existing)] == existing:
        return observed[:]

    # OCR sees visible tail
    for size in range(
        min(
            len(existing),
            len(observed),
        ),
        0,
        -1,
    ):

        if (
            existing[-size:]
            == observed[:size]
        ):
            return (
                existing
                + observed[size:]
            )

    # Shorter exact suffix
    if len(observed) <= len(existing):

        for size in range(
            len(observed),
            0,
            -1,
        ):

            if (
                existing[-size:]
                == observed[:size]
            ):
                return existing

    # Ignore bad OCR frame
    return existing


# =========================================================
# OCR PREPROCESSING
# =========================================================

def preprocess_variants(
    img: Image.Image,
):

    gray = ImageOps.grayscale(img)

    gray = ImageOps.autocontrast(
        gray
    )

    gray = ImageEnhance.Contrast(
        gray
    ).enhance(2.0)

    # Make chess.com text larger
    gray = gray.resize(
        (
            gray.width * 3,
            gray.height * 3,
        )
    )

    variants = [
        gray
    ]

    # Threshold variants
    for threshold in (
        140,
        180,
        210,
    ):

        bw = gray.point(
            lambda p, t=threshold:
                255
                if p > t
                else 0
        )

        variants.append(bw)

    # Sharpen
    variants.append(
        gray.filter(
            ImageFilter.SHARPEN
        )
    )

    return variants


def ocr_moves(
    img: Image.Image,
):

    best_text = ""
    best_moves = []

    for variant in preprocess_variants(
        img
    ):

        try:

            text = pytesseract.image_to_string(
                variant,
                config=(
                    "--psm 6 "
                    f"-c tessedit_char_whitelist="
                    f"{OCR_WHITELIST}"
                ),
            )

        except Exception:

            continue

        moves = extract_san_tokens(
            text
        )

        if (
            score_observation(moves)
            >
            score_observation(
                best_moves
            )
        ):

            best_text = text
            best_moves = moves

    return (
        best_text,
        best_moves,
    )


# =========================================================
# REGION SELECTOR
# =========================================================

class RegionSelector(
    tk.Toplevel
):

    def __init__(
        self,
        parent,
        screenshot,
        monitor,
        callback,
    ):

        super().__init__(parent)

        self.parent = parent
        self.callback = callback
        self.monitor = monitor
        self.screenshot = screenshot

        self.start = None
        self.rect = None

        self.overrideredirect(True)

        self.attributes(
            "-topmost",
            True,
        )

        self.geometry(
            f"{screenshot.width}"
            f"x{screenshot.height}"
            f"+{monitor['left']}"
            f"+{monitor['top']}"
        )

        self.img = ImageTk.PhotoImage(
            screenshot
        )

        self.canvas = tk.Canvas(
            self,
            width=screenshot.width,
            height=screenshot.height,
            highlightthickness=0,
            cursor="crosshair",
        )

        self.canvas.pack()

        self.canvas.create_image(
            0,
            0,
            image=self.img,
            anchor="nw",
        )

        self.canvas.create_rectangle(
            0,
            0,
            700,
            42,
            fill="white",
            outline="",
        )

        self.canvas.create_text(
            12,
            8,
            anchor="nw",
            text=(
                "Kéo chuột chọn đúng "
                "vùng danh sách nước đi"
                " • ESC để hủy"
            ),
            fill="red",
            font=(
                "Segoe UI",
                14,
                "bold",
            ),
        )

        self.canvas.bind(
            "<ButtonPress-1>",
            self.on_down,
        )

        self.canvas.bind(
            "<B1-Motion>",
            self.on_drag,
        )

        self.canvas.bind(
            "<ButtonRelease-1>",
            self.on_up,
        )

        self.bind(
            "<Escape>",
            self.cancel,
        )

        self.focus_force()
        self.lift()


    def on_down(
        self,
        event,
    ):

        self.start = (
            event.x,
            event.y,
        )

        if self.rect is not None:
            self.canvas.delete(
                self.rect
            )

        self.rect = (
            self.canvas.create_rectangle(
                event.x,
                event.y,
                event.x,
                event.y,
                outline="red",
                width=3,
            )
        )


    def on_drag(
        self,
        event,
    ):

        if self.start is None:
            return

        x1, y1 = self.start
        x2, y2 = (
            event.x,
            event.y,
        )

        if self.rect is not None:

            self.canvas.coords(
                self.rect,
                x1,
                y1,
                x2,
                y2,
            )


    def on_up(
        self,
        event,
    ):

        if self.start is None:
            return

        x1, y1 = self.start
        x2, y2 = (
            event.x,
            event.y,
        )

        left = min(
            x1,
            x2,
        )

        top = min(
            y1,
            y2,
        )

        right = max(
            x1,
            x2,
        )

        bottom = max(
            y1,
            y2,
        )

        if (
            right - left < 30
            or
            bottom - top < 20
        ):

            messagebox.showwarning(
                "Vùng quá nhỏ",
                "Hãy kéo chọn vùng lớn hơn.",
            )

            return

        region = (
            left,
            top,
            right,
            bottom,
        )

        self.callback(
            region
        )


    def cancel(
        self,
        _event=None,
    ):

        self.callback(None)


# =========================================================
# MAIN APPLICATION
# =========================================================

class ChessMoveReader(
    tk.Tk
):

    def __init__(self):

        super().__init__()

        self.title(
            APP_TITLE
        )

        self.geometry(
            "500x540"
        )

        self.minsize(
            400,
            350,
        )

        # ALWAYS ON TOP
        self.attributes(
            "-topmost",
            True,
        )

        self.region = None
        self.region_monitor = None

        self.running = False
        self.worker = None

        self.moves = []

        self.last_ocr = ""

        self.status = tk.StringVar(
            value="Chưa chọn vùng"
        )

        self.interval = (
            tk.DoubleVar(
                value=DEFAULT_INTERVAL
            )
        )

        self.build_ui()

        # Check Tesseract
        try:

            pytesseract.get_tesseract_version()

        except Exception:

            self.after(
                100,
                self.show_tesseract_error,
            )


    # =====================================================
    # UI
    # =====================================================

    def build_ui(
        self
    ):

        outer = ttk.Frame(
            self,
            padding=10,
        )

        outer.pack(
            fill="both",
            expand=True,
        )

        title_row = ttk.Frame(
            outer
        )

        title_row.pack(
            fill="x"
        )

        ttk.Label(
            title_row,
            text="CHESS MOVE READER",
            font=(
                "Segoe UI",
                15,
                "bold",
            ),
        ).pack(
            side="left"
        )

        self.pin_button = (
            ttk.Button(
                title_row,
                text="📌 Always on top",
                command=self.toggle_topmost,
            )
        )

        self.pin_button.pack(
            side="right"
        )

        controls = ttk.Frame(
            outer
        )

        controls.pack(
            fill="x",
            pady=(10, 6),
        )

        ttk.Button(
            controls,
            text="Chọn vùng OCR",
            command=self.select_region,
        ).pack(
            side="left",
            padx=(0, 4),
        )

        ttk.Button(
            controls,
            text="Test OCR",
            command=self.test_ocr,
        ).pack(
            side="left",
            padx=4,
        )

        ttk.Button(
            controls,
            text="▶ Bắt đầu",
            command=self.start_reader,
        ).pack(
            side="left",
            padx=4,
        )

        ttk.Button(
            controls,
            text="■ Dừng",
            command=self.stop_reader,
        ).pack(
            side="left",
            padx=4,
        )

        ttk.Button(
            controls,
            text="Xóa",
            command=self.clear_moves,
        ).pack(
            side="left",
            padx=4,
        )

        settings = ttk.Frame(
            outer
        )

        settings.pack(
            fill="x",
            pady=(2, 6),
        )

        ttk.Label(
            settings,
            text="Chu kỳ:",
        ).pack(
            side="left"
        )

        ttk.Spinbox(
            settings,
            from_=0.3,
            to=5.0,
            increment=0.1,
            textvariable=self.interval,
            width=6,
        ).pack(
            side="left",
            padx=6,
        )

        ttk.Label(
            settings,
            textvariable=self.status,
        ).pack(
            side="right"
        )

        ttk.Label(
            outer,
            text="PLAINTEXT",
            font=(
                "Segoe UI",
                9,
                "bold",
            ),
        ).pack(
            anchor="w"
        )

        text_frame = ttk.Frame(
            outer
        )

        text_frame.pack(
            fill="both",
            expand=True,
        )

        self.output = tk.Text(
            text_frame,
            wrap="word",
            font=(
                "Consolas",
                13,
            ),
            padx=8,
            pady=8,
            undo=False,
        )

        self.output.pack(
            side="left",
            fill="both",
            expand=True,
        )

        scroll = ttk.Scrollbar(
            text_frame,
            orient="vertical",
            command=self.output.yview,
        )

        scroll.pack(
            side="right",
            fill="y",
        )

        self.output.configure(
            yscrollcommand=scroll.set
        )

        bottom = ttk.Frame(
            outer
        )

        bottom.pack(
            fill="x",
            pady=(6, 0),
        )

        ttk.Button(
            bottom,
            text="Copy plaintext",
            command=self.copy_plaintext,
        ).pack(
            side="left"
        )

        ttk.Button(
            bottom,
            text="Lưu .txt",
            command=self.save_txt,
        ).pack(
            side="left",
            padx=6,
        )

        ttk.Label(
            bottom,
            text="OCR màn hình • không engine",
            font=(
                "Segoe UI",
                8,
            ),
        ).pack(
            side="right"
        )


    # =====================================================
    # TESSERACT ERROR
    # =====================================================

    def show_tesseract_error(
        self
    ):

        messagebox.showerror(
            "Thiếu Tesseract OCR",
            (
                "Không tìm thấy tesseract.exe.\n\n"
                "Kiểm tra:\n"
                "C:\\Program Files\\"
                "Tesseract-OCR\\"
                "tesseract.exe\n\n"
                "Sau khi cài, mở lại app."
            ),
        )


    # =====================================================
    # ALWAYS ON TOP
    # =====================================================

    def toggle_topmost(
        self
    ):

        current = bool(
            self.attributes(
                "-topmost"
            )
        )

        self.attributes(
            "-topmost",
            not current,
        )

        self.pin_button.configure(
            text=(
                "📌 Always on top"
                if not current
                else
                "○ Không ghim"
            )
        )


    # =====================================================
    # SCREEN CAPTURE
    # =====================================================

    def get_primary_monitor(
        self
    ):

        with mss.mss() as sct:

            return dict(
                sct.monitors[1]
            )


    def capture_primary_screen(
        self
    ):

        with mss.mss() as sct:

            monitor = dict(
                sct.monitors[1]
            )

            raw = sct.grab(
                monitor
            )

            image = Image.frombytes(
                "RGB",
                raw.size,
                raw.rgb,
            )

        return (
            monitor,
            image,
        )


    # =====================================================
    # REGION SELECTION
    # =====================================================

    def select_region(
        self
    ):

        if self.running:
            self.stop_reader()

        # Hide the app BEFORE screenshot
        self.withdraw()
        self.update()

        time.sleep(
            0.35
        )

        try:

            monitor, screenshot = (
                self.capture_primary_screen()
            )

        except Exception as exc:

            self.deiconify()
            self.lift()

            messagebox.showerror(
                "Lỗi chụp màn hình",
                str(exc),
            )

            return

        selector = RegionSelector(
            self,
            screenshot,
            monitor,
            self.on_region_selected,
        )

        self.region_selector = selector


    def on_region_selected(
        self,
        region,
    ):

        try:

            selector = getattr(
                self,
                "region_selector",
                None,
            )

            if (
                selector is not None
                and
                selector.winfo_exists()
            ):

                selector.destroy()

        except Exception:
            pass

        self.deiconify()

        self.lift()

        if region is None:

            self.status.set(
                "Đã hủy chọn vùng"
            )

            return

        self.region = region

        self.region_monitor = (
            self.get_primary_monitor()
        )

        left, top, right, bottom = (
            region
        )

        self.status.set(
            f"Vùng: "
            f"{right-left}×"
            f"{bottom-top}"
        )


    # =====================================================
    # GET REGION IMAGE
    # =====================================================

    def get_region_image(
        self
    ):

        if not self.region:

            raise RuntimeError(
                "Chưa chọn vùng OCR."
            )

        with mss.mss() as sct:

            monitor = (
                self.region_monitor
                or
                dict(
                    sct.monitors[1]
                )
            )

            left, top, right, bottom = (
                self.region
            )

            box = {
                "left":
                    monitor["left"]
                    + left,

                "top":
                    monitor["top"]
                    + top,

                "width":
                    right - left,

                "height":
                    bottom - top,
            }

            raw = sct.grab(
                box
            )

            return Image.frombytes(
                "RGB",
                raw.size,
                raw.rgb,
            )


    # =====================================================
    # TEST OCR
    # =====================================================

    def test_ocr(
        self
    ):

        if not self.region:

            messagebox.showwarning(
                "Chưa chọn vùng",
                (
                    "Bấm 'Chọn vùng OCR' "
                    "và chọn move list trước."
                ),
            )

            return

        try:

            img = (
                self.get_region_image()
            )

            raw_text, observed = (
                ocr_moves(img)
            )

            self.output.delete(
                "1.0",
                "end",
            )

            self.output.insert(
                "1.0",
                raw_text.strip(),
            )

            if observed:

                self.status.set(
                    f"Test OK • "
                    f"{len(observed)} nước"
                )

            else:

                self.status.set(
                    "Test xong • chưa nhận SAN"
                )

        except Exception as exc:

            messagebox.showerror(
                "Test OCR",
                str(exc),
            )


    # =====================================================
    # START READER
    # =====================================================

    def start_reader(
        self
    ):

        if not self.region:

            messagebox.showwarning(
                "Chưa chọn vùng",
                (
                    "Bấm 'Chọn vùng OCR' "
                    "rồi chọn move list."
                ),
            )

            return

        if self.running:
            return

        self.running = True

        self.status.set(
            f"Đang đọc • "
            f"{len(self.moves)} nước"
        )

        self.worker = threading.Thread(
            target=self.ocr_loop,
            daemon=True,
        )

        self.worker.start()


    # =====================================================
    # STOP
    # =====================================================

    def stop_reader(
        self
    ):

        self.running = False

        self.status.set(
            "Đã dừng"
        )


    # =====================================================
    # OCR LOOP
    # =====================================================

    def ocr_loop(
        self
    ):

        while self.running:

            started = time.monotonic()

            try:

                img = (
                    self.get_region_image()
                )

                raw_text, observed = (
                    ocr_moves(img)
                )

                merged = merge_moves(
                    self.moves,
                    observed,
                )

                if merged != self.moves:

                    self.moves = merged

                    self.after(
                        0,
                        self.render_moves,
                    )

                self.last_ocr = raw_text

                count = len(
                    self.moves
                )

                self.after(
                    0,
                    lambda c=count:
                        self.status.set(
                            f"Đang đọc • "
                            f"{c} nước"
                        ),
                )

            except Exception as exc:

                error = str(exc)

                self.after(
                    0,
                    lambda e=error:
                        self.status.set(
                            f"OCR lỗi: "
                            f"{e[:55]}"
                        ),
                )

            elapsed = (
                time.monotonic()
                - started
            )

            try:

                delay = max(
                    0.05,
                    float(
                        self.interval.get()
                    )
                    - elapsed,
                )

            except Exception:

                delay = (
                    DEFAULT_INTERVAL
                )

            time.sleep(
                delay
            )


    # =====================================================
    # RENDER MOVES
    # =====================================================

    def render_moves(
        self
    ):

        lines = []

        for i in range(
            0,
            len(self.moves),
            2,
        ):

            number = (
                i // 2 + 1
            )

            white = (
                self.moves[i]
            )

            black = (
                self.moves[i + 1]
                if
                i + 1
                <
                len(self.moves)
                else
                ""
            )

            if black:

                lines.append(
                    f"{number}. "
                    f"{white} "
                    f"{black}"
                )

            else:

                lines.append(
                    f"{number}. "
                    f"{white}"
                )

        text = "\n".join(
            lines
        )

        self.output.delete(
            "1.0",
            "end",
        )

        self.output.insert(
            "1.0",
            text,
        )

        self.output.see(
            "end"
        )


    # =====================================================
    # CLEAR
    # =====================================================

    def clear_moves(
        self
    ):

        self.moves = []
        self.last_ocr = ""

        self.output.delete(
            "1.0",
            "end",
        )

        self.status.set(
            "Đã xóa • trận mới"
        )


    # =====================================================
    # COPY
    # =====================================================

    def get_plaintext(
        self
    ):

        return self.output.get(
            "1.0",
            "end-1c",
        )


    def copy_plaintext(
        self
    ):

        text = (
            self.get_plaintext()
        )

        self.clipboard_clear()

        self.clipboard_append(
            text
        )

        self.update()

        self.status.set(
            "Đã copy plaintext"
        )


    # =====================================================
    # SAVE TXT
    # =====================================================

    def save_txt(
        self
    ):

        text = (
            self.get_plaintext()
        )

        if not text:
            return

        path = (
            filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[
                    (
                        "Text files",
                        "*.txt",
                    ),
                    (
                        "All files",
                        "*.*",
                    ),
                ],
            )
        )

        if not path:
            return

        with open(
            path,
            "w",
            encoding="utf-8",
        ) as f:

            f.write(text)

        self.status.set(
            "Đã lưu .txt"
        )


    # =====================================================
    # CLOSE
    # =====================================================

    def on_close(
        self
    ):

        self.running = False

        try:

            selector = getattr(
                self,
                "region_selector",
                None,
            )

            if (
                selector is not None
                and
                selector.winfo_exists()
            ):

                selector.destroy()

        except Exception:
            pass

        self.destroy()


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    app = ChessMoveReader()

    app.mainloop()