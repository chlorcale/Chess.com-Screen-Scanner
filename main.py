
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import pytesseract
import mss
import pytesseract
from PIL import Image, ImageEnhance, ImageOps, ImageTk

from pathlib import Path

TESSERACT_PATH = Path(
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

if TESSERACT_PATH.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)
    

APP_TITLE = "Chess Move Reader"
DEFAULT_INTERVAL = 0.8

SAN_RE = re.compile(
    r"^(?:"
    r"O-O-O|O-O|0-0-0|0-0|"
    r"[KQRBN](?:[a-h1-8]{0,2})?x?[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|[a-h]x[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|[a-h][1-8](?:=[QRBN])?[+#]?"
    r")$"
)


def normalize_ocr(text: str) -> str:
    text = text.replace("\n", " ")
    text = text.replace("—", "-").replace("–", "-")
    text = text.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_san_tokens(text: str):
    """
    Extract likely SAN move tokens from OCR output.
    The OCR region should contain only the move list for best results.
    """
    text = normalize_ocr(text)
    # Remove common move-number forms: 1. 2... 12. etc.
    text = re.sub(r"\b\d+\.(?:\.\.)?\s*", " ", text)
    # Remove game-result markers.
    text = re.sub(r"\b(?:1-0|0-1|1/2-1/2|\*)\b", " ", text)

    raw = re.findall(r"[A-Za-z0-9+#=x\-]+", text)
    moves = []

    for token in raw:
        token = token.strip(".,:;[](){}")
        if not token:
            continue
        if token in {"O-O", "O-O-O"}:
            moves.append(token)
            continue
        # OCR often reads lowercase piece letters.
        if token[:1] in "kqrbn":
            token = token[:1].upper() + token[1:]
        if SAN_RE.match(token):
            moves.append(token)

    return moves


def merge_moves(existing, observed):
    """
    Merge a newly OCR-read sequence into the stored sequence.
    Works when chess.com shows only the visible tail after scrolling.
    """
    if not observed:
        return existing

    if not existing:
        return observed[:]

    if len(observed) <= len(existing):
        # Exact tail already present.
        for size in range(len(observed), 0, -1):
            if existing[-size:] == observed[:size]:
                return existing
        return existing

    # If the observed list starts with the current full sequence, append tail.
    if observed[:len(existing)] == existing:
        return observed[:]

    # Find the largest suffix/prefix overlap.
    max_overlap = min(len(existing), len(observed))
    for size in range(max_overlap, 0, -1):
        if existing[-size:] == observed[:size]:
            return existing + observed[size:]

    # No overlap: ignore this OCR frame rather than corrupting the sequence.
    return existing


class RegionSelector(tk.Toplevel):
    def __init__(self, parent, screenshot, callback):
        super().__init__(parent)
        self.parent = parent
        self.callback = callback
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        self.img = ImageTk.PhotoImage(screenshot)
        self.canvas = tk.Canvas(self, width=screenshot.width, height=screenshot.height,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack()

        self.canvas.create_image(0, 0, image=self.img, anchor="nw")
        self.start = None
        self.rect = None
        self.text = self.canvas.create_text(
            15, 15,
            anchor="nw",
            text="Kéo chuột chọn vùng danh sách nước đi • ESC để hủy",
            fill="red",
            font=("Segoe UI", 14, "bold")
        )

        self.geometry(f"{screenshot.width}x{screenshot.height}+0+0")
        self.bind("<Escape>", lambda e: self.destroy())
        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)

    def on_down(self, event):
        self.start = (event.x, event.y)
        if self.rect:
            self.canvas.delete(self.rect)

    def on_drag(self, event):
        if not self.start:
            return
        x1, y1 = self.start
        x2, y2 = event.x, event.y
        self.rect = self.canvas.create_rectangle(
            x1, y1, x2, y2, outline="red", width=3
        )

    def on_up(self, event):
        if not self.start:
            return
        x1, y1 = self.start
        x2, y2 = event.x, event.y
        left, top = min(x1, x2), min(y1, y2)
        right, bottom = max(x1, x2), max(y1, y2)

        if right - left < 20 or bottom - top < 20:
            messagebox.showwarning("Vùng quá nhỏ", "Hãy chọn vùng lớn hơn.")
            return

        self.callback((left, top, right, bottom))
        self.destroy()


class ChessMoveReader(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("430x420")
        self.minsize(360, 300)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.region = None
        self.running = False
        self.worker = None
        self.moves = []
        self.last_ocr = ""
        self.status = tk.StringVar(value="Chưa chọn vùng")
        self.interval = tk.DoubleVar(value=DEFAULT_INTERVAL)

        self.build_ui()

    def build_ui(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        title_row = ttk.Frame(outer)
        title_row.pack(fill="x")

        ttk.Label(title_row, text="CHESS MOVE READER",
                  font=("Segoe UI", 15, "bold")).pack(side="left")

        self.pin_button = ttk.Button(
            title_row, text="📌 Always on top", command=self.toggle_topmost
        )
        self.pin_button.pack(side="right")

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(10, 6))

        ttk.Button(controls, text="Chọn vùng OCR",
                   command=self.select_region).pack(side="left", padx=(0, 6))
        ttk.Button(controls, text="▶ Bắt đầu",
                   command=self.start_reader).pack(side="left", padx=3)
        ttk.Button(controls, text="■ Dừng",
                   command=self.stop_reader).pack(side="left", padx=3)
        ttk.Button(controls, text="Xóa",
                   command=self.clear_moves).pack(side="left", padx=3)

        settings = ttk.Frame(outer)
        settings.pack(fill="x", pady=(2, 6))

        ttk.Label(settings, text="Chu kỳ (giây):").pack(side="left")
        ttk.Spinbox(settings, from_=0.3, to=5.0, increment=0.1,
                    textvariable=self.interval, width=6).pack(side="left", padx=6)
        ttk.Label(settings, textvariable=self.status).pack(side="right")

        text_frame = ttk.Frame(outer)
        text_frame.pack(fill="both", expand=True)

        self.output = tk.Text(
            text_frame,
            wrap="word",
            font=("Consolas", 13),
            padx=8,
            pady=8,
            undo=False
        )
        self.output.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(text_frame, orient="vertical",
                               command=self.output.yview)
        scroll.pack(side="right", fill="y")
        self.output.configure(yscrollcommand=scroll.set)

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(6, 0))

        ttk.Button(bottom, text="Copy plaintext",
                   command=self.copy_plaintext).pack(side="left")
        ttk.Button(bottom, text="Lưu .txt",
                   command=self.save_txt).pack(side="left", padx=6)

        ttk.Label(
            bottom,
            text="Chỉ đọc nước đi • không có engine/gợi ý",
            font=("Segoe UI", 8)
        ).pack(side="right")

    def toggle_topmost(self):
        current = bool(self.attributes("-topmost"))
        self.attributes("-topmost", not current)
        self.pin_button.configure(
            text="📌 Always on top" if not current else "○ Không ghim"
        )

    def select_region(self):
        was_running = self.running
        if was_running:
            self.stop_reader()

        self.withdraw()
        self.update_idletasks()

        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                raw = sct.grab(monitor)
                shot = Image.frombytes("RGB", raw.size, raw.rgb)
        except Exception as exc:
            self.deiconify()
            messagebox.showerror("Lỗi chụp màn hình", str(exc))
            return

        self.deiconify()
        RegionSelector(self, shot, self.set_region)

    def set_region(self, region):
        self.region = region
        self.status.set(
            f"Vùng: {region[2]-region[0]}×{region[3]-region[1]}"
        )

    def clear_moves(self):
        self.moves = []
        self.last_ocr = ""
        self.output.delete("1.0", "end")

    def start_reader(self):
        if not self.region:
            messagebox.showwarning(
                "Chưa chọn vùng",
                "Hãy bấm 'Chọn vùng OCR' rồi kéo quanh danh sách nước đi trên chess.com."
            )
            return
        if self.running:
            return

        self.running = True
        self.status.set("Đang đọc...")
        self.worker = threading.Thread(target=self.ocr_loop, daemon=True)
        self.worker.start()

    def stop_reader(self):
        self.running = False
        self.status.set("Đã dừng")

    def ocr_loop(self):
        with mss.mss() as sct:
            monitor = sct.monitors[1]

            while self.running:
                started = time.monotonic()
                try:
                    x1, y1, x2, y2 = self.region
                    # Region selector is relative to the primary monitor.
                    monitor_region = {
                        "left": monitor["left"] + x1,
                        "top": monitor["top"] + y1,
                        "width": x2 - x1,
                        "height": y2 - y1,
                    }
                    raw = sct.grab(monitor_region)
                    img = Image.frombytes("RGB", raw.size, raw.rgb)

                    # OCR-friendly preprocessing.
                    gray = ImageOps.grayscale(img)
                    gray = ImageEnhance.Contrast(gray).enhance(2.2)
                    gray = gray.resize((gray.width * 2, gray.height * 2))

                    text = pytesseract.image_to_string(
                        gray,
                        config="--psm 6"
                    )
                    observed = extract_san_tokens(text)

                    merged = merge_moves(self.moves, observed)
                    if merged != self.moves:
                        self.moves = merged
                        self.after(0, self.render_moves)

                    self.last_ocr = text
                    self.after(0, lambda: self.status.set(
                        f"Đang đọc • {len(self.moves)} nước"
                    ))

                except Exception as exc:
                    self.after(0, lambda e=str(exc): self.status.set(
                        f"OCR lỗi: {e[:45]}"
                    ))

                elapsed = time.monotonic() - started
                delay = max(0.05, float(self.interval.get()) - elapsed)
                time.sleep(delay)

    def render_moves(self):
        # Plaintext only, one move-number pair per line.
        lines = []
        for i in range(0, len(self.moves), 2):
            number = i // 2 + 1
            white = self.moves[i]
            black = self.moves[i + 1] if i + 1 < len(self.moves) else ""
            lines.append(f"{number}. {white}" + (f" {black}" if black else ""))

        text = "\n".join(lines)
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.see("end")

    def get_plaintext(self):
        return self.output.get("1.0", "end-1c")

    def copy_plaintext(self):
        text = self.get_plaintext()
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.status.set("Đã copy plaintext")

    def save_txt(self):
        text = self.get_plaintext()
        if not text:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.status.set("Đã lưu .txt")

    def on_close(self):
        self.running = False
        self.destroy()


if __name__ == "__main__":
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Thiếu Tesseract OCR",
            "Chưa tìm thấy Tesseract OCR.\n\n"
            "Cài Tesseract, sau đó mở lại ứng dụng.\n"
            "Nếu Tesseract nằm ngoài PATH, đặt đường dẫn trong README."
        )
        root.destroy()
        raise SystemExit(1)

    app = ChessMoveReader()
    app.mainloop()
