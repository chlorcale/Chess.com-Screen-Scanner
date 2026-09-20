"""Chess Move Reader v3 — latest-frame OCR pipeline

OCR danh sách nước đi trên màn hình -> dựng lại ván cờ -> Stockfish phân tích.

Thay đổi chính so với v1
------------------------
OCR
  * Sửa lỗi nước tốt cột b (b4, bxc5...) bị biến thành quân Tượng rồi bị loại.
  * Dùng SỐ THỨ TỰ nước ("12.") làm mỏ neo tuyệt đối khi danh sách bị cuộn.
  * Tự đảo màu cho giao diện tối, khử nền ô đang được tô sáng, tắt từ điển Tesseract.
  * Producer/consumer frame mới nhất: chụp màn hình không bị Tesseract chặn; không tích backlog khi chess.com cuộn.
  * Debounce frame sau scroll + xác nhận 2 khung hình; giữ số thứ tự làm mỏ neo tuyệt đối.
Stockfish
  * Một luồng engine duy nhất, coalescing chỉ còn vị trí mới nhất, huỷ phân tích cũ ngay lập tức.
  * Tự khởi động lại khi engine chết, cache theo vị trí, kết quả hiển thị dần theo depth.
UI
  * Giao diện tối, thanh eval, bàn cờ mini có mũi tên, tab lịch sử / OCR debug.
  * Mọi cập nhật Tk đều chạy trên main thread (hết treo ngẫu nhiên).
"""

import bisect
import ctypes
import json
import math
import os
import queue
import re
import shutil as file_shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import zlib
import tkinter.font as tkfont

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import chess
import chess.engine
import mss
import pytesseract

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageTk


APP_TITLE = "Chess Move Reader"
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "chess_reader_config.json"

DEFAULT_INTERVAL = 0.35
ENGINE_TIME = 0.50
BACKLOG_ENGINE_TIME = 0.15      # thời gian cho các vị trí cũ (chỉ để điền lịch sử)
MAX_BURST_ANALYSIS = 6          # tối đa số vị trí đưa vào hàng đợi trong 1 lần cập nhật
ANALYSIS_HISTORY_LIMIT = 200
TESSERACT_TIMEOUT = 3
CAPTURE_POLL = 0.04             # chụp màn hình liên tục, không bị chặn bởi Tesseract
OCR_SETTLE_TIME = 0.08          # chờ danh sách đứng yên sau khi scroll/animation rồi mới OCR
FAST_OCR_PLANS = 3              # realtime: 3 plan khi frame mơ hồ; Test OCR vẫn thử toàn bộ

IS_WINDOWS = sys.platform.startswith("win")


# =========================================================
# STOCKFISH / TESSERACT DISCOVERY
# =========================================================

def _is_engine_file(path: Path) -> bool:
    if not path.is_file() or "stockfish" not in path.name.lower():
        return False
    if IS_WINDOWS:
        return path.suffix.lower() == ".exe"
    return path.suffix == "" and os.access(path, os.X_OK)


def find_stockfish(preferred=None):
    """Tìm Stockfish: đường dẫn người dùng chọn -> cạnh app -> ./stockfish/** -> PATH."""
    if preferred and Path(preferred).is_file():
        return str(preferred)

    candidates = []
    for root in (APP_DIR, APP_DIR / "stockfish"):
        if root.is_dir():
            candidates.extend(root.glob("*"))
    sub = APP_DIR / "stockfish"
    if sub.is_dir():
        candidates.extend(sub.rglob("*"))

    for candidate in candidates:
        if _is_engine_file(candidate):
            return str(candidate)

    return file_shutil.which("stockfish")


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
# SAN TOKENS
# =========================================================

SAN_RE = re.compile(
    r"^(?:O-O-O|O-O"
    r"|[KQRBN][a-h]?[1-8]?x?[a-h][1-8][+#]?"
    r"|[a-h]x[a-h][1-8](?:=?[QRBN])?[+#]?"
    r"|[a-h][1-8](?:=?[QRBN])?[+#]?)$"
)

# Token "trông giống nước đi" nhưng chưa chuẩn (vd. NfG, N95). Giữ lại để bước
# sửa theo thế cờ xử lý, thay vì lặng lẽ bỏ qua và làm lệch cả danh sách.
LOOSE_RE = re.compile(r"^[KQRBNkqrnOa-h][A-Za-z0-9x=+#\-]{1,6}$")

SCAN_RE = re.compile(
    r"(?P<num>\d{1,3})\s?(?P<dots>\.{1,3})(?!\d)"
    r"|(?P<tok>[A-Za-z0-9+#=\-]+)"
)
FUSED_RE = re.compile(r"^(\d{1,3})([a-hKQRBNO].+)$")

# Có 0 và 9 để đọc được số thứ tự 9, 10, 20...; nhầm 9<->g, 0<->O được sửa sau.
OCR_WHITELIST = "KQRBNabcdefgh0123456789x+#=O-."

CONFUSABLE = {
    frozenset(p) for p in (
        "g9", "gq", "q9", "b6", "bh", "ce", "38", "56", "17", "O0", "o0", "oO",
        "B8", "BR", "QO", "Q0", "G6", "l1", "I1", "S5", "s5", "Z2", "z2", "A4",
        "tf", "D0", "a0", "eo", "g8", "e6", "d4",
    )
}


def normalize_ocr(text: str) -> str:
    text = text.replace("—", "-").replace("–", "-")

    def _castling(match):
        compact = re.sub(r"[^0oO]", "", match.group(0))
        return "O-O-O" if len(compact) == 3 else "O-O"

    text = re.sub(
        r"(?<![A-Za-z0-9])[0oO](?:-?[0oO]){1,2}(?![A-Za-z0-9])",
        _castling,
        text,
    )
    text = re.sub(r"(?<!\S)(?:1-0|0-1|1/2-1/2|\*)(?!\S)", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def canonical_ocr_token(token):
    token = (token or "").strip(".,:;[](){}")
    token = token.replace("X", "x")
    token = re.sub(r"[!?]+$", "", token)

    compact = token.replace("-", "")
    if re.fullmatch(r"[0oO]{2}", compact):
        return "O-O"
    if re.fullmatch(r"[0oO]{3}", compact):
        return "O-O-O"

    # CHÚ Ý: không viết hoa 'b' — 'b' là cột b (b4, bxc5). v1 viết hoa cả 'b'
    # nên mọi nước tốt cột b đều bị hỏng.
    if token[:1] in "kqrn":
        token = token[:1].upper() + token[1:]
    return token


@dataclass
class Observation:
    tokens: list = field(default_factory=list)
    plies: list = field(default_factory=list)   # chỉ số ply tuyệt đối (0 = nước 1 của Trắng) hoặc None
    text: str = ""
    optional: set = field(default_factory=set)  # token đầu dòng nghi là số thứ tự bị đọc sai

    def __bool__(self):
        return bool(self.tokens)


def parse_observation(text: str) -> Observation:
    """Tách text OCR thành token + vị trí tuyệt đối suy ra từ số thứ tự nước.

    Xử lý theo từng DÒNG (mỗi dòng = "số. trắng đen"): token rác ở đầu dòng
    thường là số thứ tự bị đọc sai ("11." -> "fa") nên không được chiếm một ply.
    """
    tokens, plies, optional = [], [], set()
    next_ply = None

    for line in normalize_ocr(text).splitlines():
        row, has_number = [], False
        for m in SCAN_RE.finditer(line):
            if m.group("num"):
                n = int(m.group("num"))
                if 1 <= n <= 400 and not row:
                    next_ply = (n - 1) * 2 + (1 if len(m.group("dots")) >= 2 else 0)
                    has_number = True
                continue

            tok = canonical_ocr_token(m.group("tok"))
            if tok and not SAN_RE.match(tok):
                fused = FUSED_RE.match(tok)          # "12e4" -> số 12 + e4
                if fused and SAN_RE.match(canonical_ocr_token(fused.group(2))):
                    n = int(fused.group(1))
                    if 1 <= n <= 400 and not row:
                        next_ply = (n - 1) * 2
                        has_number = True
                    tok = canonical_ocr_token(fused.group(2))
            if tok and (SAN_RE.match(tok) or LOOSE_RE.match(tok)):
                row.append(tok)

        # Một dòng có tối đa 2 nước: bỏ rác ở đầu dòng nếu thừa.
        while len(row) > 2 and not SAN_RE.match(row[0]):
            row.pop(0)

        for k, tok in enumerate(row):
            doubtful = k == 0 and not has_number and not SAN_RE.match(tok) and len(row) >= 2
            if doubtful:
                optional.add(len(tokens))
                tokens.append(tok)
                plies.append(None)
                continue
            tokens.append(tok)
            plies.append(next_ply)
            if next_ply is not None:
                next_ply += 1

    first = next((i for i, p in enumerate(plies) if p is not None), None)
    if first:
        for i in range(first - 1, -1, -1):
            prev = plies[i + 1] - 1
            if prev < 0 or i in optional:
                break
            plies[i] = prev

    return Observation(tokens, plies, text, optional)


def extract_san_tokens(text: str):
    return [t for t in parse_observation(text).tokens if SAN_RE.match(t)]


# =========================================================
# FUZZY MATCHING
# =========================================================

def _core(token):
    return canonical_ocr_token(token).rstrip("+#")


def _sub_cost(a, b):
    if a == b:
        return 0.0
    if a.lower() == b.lower() or frozenset((a, b)) in CONFUSABLE:
        return 0.5
    return 1.0


def ocr_distance(a, b, limit=2.0):
    """Levenshtein có trọng số: cặp ký tự OCR hay nhầm chỉ tốn 0.5."""
    a, b = _core(a), _core(b)
    if a == b:
        return 0.0
    if abs(len(a) - len(b)) > limit:
        return limit + 1

    previous = [float(j) for j in range(len(b) + 1)]
    for i, ca in enumerate(a, start=1):
        current = [float(i)]
        for j, cb in enumerate(b, start=1):
            current.append(min(
                current[j - 1] + 1,
                previous[j] + 1,
                previous[j - 1] + _sub_cost(ca, cb),
            ))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def tokens_similar(a, b):
    return ocr_distance(a, b, 1.0) <= 1.0


def repair_tail_from_position(board, tokens, banned=frozenset(), skippable=()):
    """Biến các token mới thành SAN hợp lệ theo thế cờ. Dừng khi không chắc chắn.

    ``board`` bị thay đổi tại chỗ. ``banned`` = {(ply, san)} người dùng đã xoá tay.
    """
    repaired = []
    tokens = list(tokens)
    consumed = 0

    for pos, raw in enumerate(tokens):
        token = canonical_ocr_token(raw)
        ply = board.ply()
        move = None

        try:
            parsed = board.parse_san(token)
            if (ply, board.san(parsed)) not in banned:
                move = parsed
        except ValueError:
            pass

        if move is None:
            core = _core(token)
            max_d = 1.0 if len(core) <= 3 else 1.5
            found = []
            for legal in board.legal_moves:
                san = board.san(legal)
                if (ply, san) in banned:
                    continue
                d = ocr_distance(core, san, max_d)
                if d <= max_d:
                    found.append((d, san, legal))
            found.sort(key=lambda item: (item[0], item[1]))
            # Không đoán khi có 2 ứng viên gần như ngang nhau.
            if not found or (len(found) > 1 and found[1][0] - found[0][0] < 0.5):
                # Token nghi là số thứ tự đọc sai: bỏ qua nếu token kế tiếp hợp lệ chính xác.
                if pos in skippable and pos + 1 < len(tokens):
                    try:
                        board.parse_san(canonical_ocr_token(tokens[pos + 1]))
                        consumed += 1
                        continue
                    except ValueError:
                        pass
                break
            move = found[0][2]

        repaired.append(board.san(move))
        board.push(move)
        consumed += 1

    return repaired, consumed


def build_board_from_moves(moves):
    board = chess.Board()
    for index, san in enumerate(moves, start=1):
        try:
            board.push(board.parse_san(san))
        except ValueError as exc:
            raise ValueError(f"Không thể dựng vị trí từ nước #{index}: {san}") from exc
    return board


def legal_prefix(moves):
    board = chess.Board()
    valid = []
    for san in moves:
        try:
            move = board.parse_san(san)
        except ValueError:
            break
        valid.append(board.san(move))
        board.push(move)
    return valid


# =========================================================
# HISTORY RECONCILIATION
# =========================================================

MAX_RECONCILE_WINDOW = 18
MAX_RECONCILE_GAPS = 2
MAX_ROLLBACK = 2


def _align_cost(left, right):
    m, n = len(left), len(right)
    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    gaps = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        dp[i][0], gaps[i][0] = i * 2.0, i
    for j in range(1, n + 1):
        dp[0][j], gaps[0][j] = j * 2.0, j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            d = ocr_distance(left[i - 1], right[j - 1], 1.0)
            sub = dp[i - 1][j - 1] + (0 if d == 0 else 1 if d <= 1.0 else 2)
            dl = dp[i - 1][j] + 2
            dr = dp[i][j - 1] + 2
            best = min(sub, dl, dr)
            dp[i][j] = best
            opts = []
            if best == sub:
                opts.append(gaps[i - 1][j - 1])
            if best == dl:
                opts.append(gaps[i - 1][j] + 1)
            if best == dr:
                opts.append(gaps[i][j - 1] + 1)
            gaps[i][j] = min(opts)
    return dp[m][n], gaps[m][n]


def _fuzzy_overlap(existing, tokens):
    """Tìm đoạn đuôi của lịch sử khớp với đoạn đầu khung OCR (không cần số thứ tự)."""
    best = None
    max_e = min(len(existing), MAX_RECONCILE_WINDOW)
    max_o = min(len(tokens), MAX_RECONCILE_WINDOW)
    min_len = 1 if len(existing) == 1 else 2

    for suffix_len in range(max_e, min_len - 1, -1):
        left = existing[-suffix_len:]
        lo = max(min_len, suffix_len - MAX_RECONCILE_GAPS)
        hi = min(max_o, suffix_len + MAX_RECONCILE_GAPS)
        for prefix_len in range(lo, hi + 1):
            cost, gaps = _align_cost(left, tokens[:prefix_len])
            if gaps > MAX_RECONCILE_GAPS or cost > min(suffix_len, prefix_len):
                continue
            score = min(suffix_len, prefix_len) * 10 - cost * 3 - abs(suffix_len - prefix_len) * 2
            cand = (score, suffix_len, prefix_len, cost)
            if best is None or cand > best:
                best = cand
    return best


def reconcile(existing, board, obs, banned=frozenset()):
    """Ghép khung OCR hiện tại vào lịch sử đã xác nhận.

    Trả về (merged, meta). meta["kind"]:
      initial | anchored | overlap | same | gap | need-start | no-overlap | rollback | empty
    meta["stalled"]: số token mới chưa hiểu được; meta["clean"]: kết quả đáng tin.
    """
    n = len(existing)
    meta = {"kind": "empty", "added": 0, "stalled": 0, "overlap": 0, "agree": 0, "clean": False}
    if not obs:
        return list(existing), meta

    tokens, plies = obs.tokens, obs.plies
    anchored = [(i, p) for i, p in enumerate(plies) if p is not None]

    # ---------- ván mới ----------
    if n == 0:
        start = 0
        if anchored:
            zero = next((i for i, p in anchored if p == 0), None)
            if zero is None:
                meta["kind"] = "need-start"
                return [], meta
            start = zero
        skip = {i - start for i in obs.optional if i >= start}
        repaired, used = repair_tail_from_position(chess.Board(), tokens[start:], banned, skip)
        meta.update(kind="initial", added=len(repaired), stalled=len(tokens) - start - used)
        meta["clean"] = bool(repaired) and meta["stalled"] == 0
        return repaired, meta

    # ---------- đường chính: neo theo số thứ tự ----------
    if anchored:
        overlap = [(i, p) for i, p in anchored if p < n]
        agree = [(i, p) for i, p in overlap if tokens_similar(existing[p], tokens[i])]
        trusted = (
            len(agree) >= max(1, math.ceil(0.6 * len(overlap))) if overlap
            else min(p for _, p in anchored) == n
        )
        if trusted:
            meta.update(overlap=len(overlap), agree=len(agree))
            meta["seen"] = [(p, tokens[i]) for i, p in overlap]
            start = next((i for i, p in anchored if p == n), None)

            if start is None:
                beyond = [p for _, p in anchored if p > n]
                meta["kind"] = "gap" if beyond else "same"
                meta["clean"] = not beyond and len(agree) >= 0.85 * len(overlap)
                return list(existing), meta

            tail, skip = _contiguous(obs, start, n)
            work = board.copy()
            repaired, used = repair_tail_from_position(work, tail, banned, skip)
            stalled = len(tail) - used
            merged = list(existing) + repaired
            meta.update(kind="anchored", added=len(repaired), stalled=stalled)
            meta["clean"] = stalled == 0 and len(agree) >= 0.85 * len(overlap)

            # Tự sửa: nếu bị kẹt VÀ 1-2 nước cuối đã lưu khác với khung hình,
            # thử thay chúng; chỉ nhận khi giải thích được nhiều token hơn.
            if stalled:
                alt = _try_rollback(existing, board, obs, agree, overlap, banned)
                if alt is not None and len(alt) > len(merged):
                    meta.update(kind="rollback", added=len(alt) - n, stalled=0, clean=False)
                    return alt, meta
            return merged, meta

    # ---------- dự phòng: khớp mờ đuôi/đầu ----------
    if tokens[:n] == list(existing):
        prefix_len = n
    else:
        found = _fuzzy_overlap(list(existing), tokens)
        if found is None:
            meta["kind"] = "no-overlap"
            return list(existing), meta
        _, suffix_len, prefix_len, cost = found
        meta.update(overlap=min(suffix_len, prefix_len), agree=int(min(suffix_len, prefix_len) - cost))

    tail = tokens[prefix_len:]
    skip = {i - prefix_len for i in obs.optional if i >= prefix_len}
    repaired, used = repair_tail_from_position(board.copy(), tail, banned, skip)
    meta.update(kind="overlap" if repaired or tail else "same",
                added=len(repaired), stalled=len(tail) - used)
    meta["clean"] = meta["stalled"] == 0
    return list(existing) + repaired, meta


def _contiguous(obs, start, first_ply):
    """Các token liên tiếp từ ``start``; trả về (tokens, vị trí có thể bỏ qua)."""
    tail, skip, expect = [], set(), first_ply
    for i in range(start, len(obs.tokens)):
        ply = obs.plies[i]
        if ply is None:
            if i in obs.optional:
                skip.add(len(tail))
                tail.append(obs.tokens[i])
                continue
            break
        if ply != expect:
            break
        tail.append(obs.tokens[i])
        expect += 1
    return tail, skip


def _try_rollback(existing, board, obs, agree, overlap, banned):
    n = len(existing)
    agreed = {p for _, p in agree}
    wrong = sorted(p for _, p in overlap if p not in agreed and p >= n - MAX_ROLLBACK)
    if not wrong:
        return None
    p0 = wrong[0]
    start = next(i for i, p in enumerate(obs.plies) if p == p0)

    work = board.copy()
    for _ in range(n - p0):
        work.pop()
    alt_tail, skip = _contiguous(obs, start, p0)
    repaired, used = repair_tail_from_position(work, alt_tail, banned, skip)
    if used != len(alt_tail):
        return None
    return list(existing[:p0]) + repaired


class GameTracker:
    """Lịch sử ván cờ đã xác nhận. Thread-safe."""

    def __init__(self):
        self.lock = threading.RLock()
        self.epoch = 0
        self.reset()

    def reset(self):
        with self.lock:
            self.sans = []
            self.board = chess.Board()
            self.pending = None
            self.banned = set()
            self.votes = {}
            self.epoch += 1

    def snapshot(self):
        with self.lock:
            return list(self.sans), self.board.copy(), frozenset(self.banned), self.epoch

    def _commit(self, sans):
        if sans[:len(self.sans)] == self.sans:
            for san in sans[len(self.sans):]:
                self.board.push(self.board.parse_san(san))
        else:
            self.board = build_board_from_moves(sans)
        self.sans = list(sans)

    def observe(self, obs, epoch):
        """Nước mới chỉ được ghi khi 2 lần quan sát liên tiếp cùng đồng ý."""
        with self.lock:
            if epoch != self.epoch:
                return False, {"kind": "stale"}
            merged, meta = reconcile(self.sans, self.board, obs, frozenset(self.banned))
            if self._vote(meta.pop("seen", ())):
                self.pending = None
                meta["kind"] = "healed"
                return True, meta
            if merged == self.sans:
                self.pending = None
                return False, meta

            changed = False
            if self.pending is not None:
                k = 0
                for a, b in zip(self.pending, merged):
                    if a != b:
                        break
                    k += 1
                common = merged[:k]
                if len(common) >= len(self.sans) and common != self.sans:
                    self._commit(common)
                    changed = True
            self.pending = merged if merged != self.sans else None
            meta["pending"] = 0 if self.pending is None else len(self.pending) - len(self.sans)
            return changed, meta

    def _vote(self, seen):
        """Bỏ phiếu theo từng ply qua các khung hình KHÁC NHAU.

        Một nước đã lưu mà nhiều khung hình sau đó đều đọc ra nước hợp lệ khác
        (vd. g3 <-> g4) sẽ được thay thế, các nước phía sau được dựng lại.
        """
        fix = None
        for ply, tok in seen:
            if ply >= len(self.sans) or not SAN_RE.match(tok):
                continue
            counter = self.votes.setdefault(ply, {})
            counter[_core(tok)] = counter.get(_core(tok), 0) + 1
            mine = counter.get(_core(self.sans[ply]), 0)
            alt, alt_votes = max(counter.items(), key=lambda kv: kv[1])
            if alt != _core(self.sans[ply]) and alt_votes >= 3 and alt_votes >= 2 * mine + 1:
                if fix is None or ply < fix[0]:
                    fix = (ply, alt)
        if fix is None:
            return False

        ply, alt = fix
        board = build_board_from_moves(self.sans[:ply])
        try:
            move = board.parse_san(alt)
        except ValueError:
            self.votes[ply].pop(alt, None)
            return False
        if (ply, board.san(move)) in self.banned:
            return False
        rebuilt = self.sans[:ply] + [board.san(move)]
        board.push(move)
        for san in self.sans[ply + 1:]:
            try:
                nxt = board.parse_san(san)
            except ValueError:
                break
            rebuilt.append(board.san(nxt))
            board.push(nxt)
        self.votes = {p: v for p, v in self.votes.items() if p < ply}
        self._commit(rebuilt)
        return True

    def confirm_static(self, epoch):
        """Khung hình đứng yên = xác nhận những gì đang chờ."""
        with self.lock:
            if epoch != self.epoch or self.pending is None:
                return False
            self._commit(self.pending)
            self.pending = None
            return True

    def undo_last(self):
        with self.lock:
            if not self.sans:
                return False
            san = self.sans[-1]
            self.board.pop()
            self.sans.pop()
            self.banned.add((len(self.sans), san))
            self.votes.pop(len(self.sans), None)
            self.pending = None
            return True

    def push_manual(self, san_text):
        with self.lock:
            move = self.board.parse_san(canonical_ocr_token(san_text))
            san = self.board.san(move)
            self.banned.discard((len(self.sans), san))
            self.board.push(move)
            self.sans.append(san)
            self.pending = None
            return san


# =========================================================
# OCR
# =========================================================

def _otsu_threshold(gray):
    hist = gray.histogram()
    total = sum(hist)
    sum_all = sum(i * h for i, h in enumerate(hist))
    sum_b = w_b = 0
    best_var, best_t = -1.0, 127
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best_var:
            best_var, best_t = var, t
    return best_t


def _base_gray(img):
    """Build an OCR base that preserves bright text even on highlighted rows.

    Chess.com dark theme can contain several nearby gray levels: the move-list
    background, the selected/highlighted move background, and anti-aliased
    white/blue text. Using only luminance + a global median inversion can make
    those levels collapse together. The MAX RGB channel keeps bright colored
    text (including blue move text) and is more robust to the highlight.
    """
    rgb = img.convert("RGB")
    r, g, b = rgb.split()
    # Brightest channel wins: preserves white text and saturated blue text.
    bright = ImageChops.lighter(ImageChops.lighter(r, g), b)
    bright = ImageOps.autocontrast(bright, cutoff=1)

    scale = 3.0
    if bright.width * scale > 1500:
        scale = max(1.5, 1500 / bright.width)
    size = (max(1, int(bright.width * scale)), max(1, int(bright.height * scale)))
    return bright.resize(size, Image.LANCZOS)


def _legacy_gray_base(img):
    """Fallback luminance preprocessing for light/different web themes."""
    gray = ImageOps.grayscale(img)
    hist = gray.histogram()
    half, acc, median = sum(hist) / 2, 0, 0
    for median, h in enumerate(hist):
        acc += h
        if acc >= half:
            break
    if median < 128:
        gray = ImageOps.invert(gray)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    scale = 3.0
    if gray.width * scale > 1500:
        scale = max(1.5, 1500 / gray.width)
    size = (max(1, int(gray.width * scale)), max(1, int(gray.height * scale)))
    return gray.resize(size, Image.LANCZOS)


def _white_tophat(base, size=9):
    # Bright-text extraction. It removes slow background changes such as the
    # highlighted move rectangle while preserving thin glyph strokes.
    opened = base.filter(ImageFilter.MinFilter(size))
    opened = opened.filter(ImageFilter.MaxFilter(size))
    return ImageOps.autocontrast(ImageChops.subtract(base, opened), cutoff=1)


def _black_tophat(base, size=9):
    # Dark-text extraction for light-theme move lists.
    closed = base.filter(ImageFilter.MaxFilter(size))
    closed = closed.filter(ImageFilter.MinFilter(size))
    return ImageOps.autocontrast(ImageChops.subtract(closed, base), cutoff=1)


def _variant(base, name):
    if name == "direct":
        return base.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=2))
    if name == "local_bright":
        # Correct polarity for bright text on dark background. The old code
        # subtracted in the opposite direction and then inverted, which could
        # erase the exact text we wanted when a row was highlighted.
        background = base.filter(ImageFilter.GaussianBlur(radius=18))
        diff = ImageChops.subtract(base, background)
        return ImageOps.autocontrast(diff, cutoff=1)
    if name == "tophat":
        return _white_tophat(base, 9)
    if name == "otsu":
        t = _otsu_threshold(base)
        return base.point(lambda p, t=t: 255 if p > t else 0)
    if name == "dark_tophat":
        return _black_tophat(base, 9)
    if name == "gray":
        return base.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))
    if name == "contrast":
        return ImageEnhance.Contrast(base).enhance(2.2)
    raise ValueError(name)




OCR_PLANS = (
    ("direct", 6),
    ("local_bright", 6),
    ("tophat", 6),
    ("otsu", 6),
    ("gray", 4),
    ("contrast", 11),
)


def _tesseract(image, psm):
    config = (
        f"--psm {psm} -c tessedit_char_whitelist={OCR_WHITELIST} "
        "-c load_system_dawg=0 -c load_freq_dawg=0 -c preserve_interword_spaces=1"
    )
    return pytesseract.image_to_string(image, config=config, timeout=TESSERACT_TIMEOUT)


def ocr_frame(img, existing=(), board=None, banned=frozenset(), first_plan=0, exhaustive=False):
    """OCR TOÀN BỘ khung move-list và ghép với lịch sử cũ.

    Realtime mode cố ý chỉ thử tối đa FAST_OCR_PLANS plan. Khi khung có vấn đề
    (đang cuộn, OCR mơ hồ, không khớp lịch sử), frame tiếp theo sẽ là nguồn dữ
    liệu mới nhất; không tạo hàng đợi OCR phía sau.

    ``exhaustive=True`` chỉ dùng cho nút Test OCR để thử toàn bộ plan.
    """
    existing = list(existing)
    board = board if board is not None else build_board_from_moves(existing)
    base = _base_gray(img)
    cache = {}

    all_order = [first_plan] + [i for i in range(len(OCR_PLANS)) if i != first_plan]
    if exhaustive:
        order = all_order
    else:
        order = all_order[:max(1, FAST_OCR_PLANS)]

    best = (-10**9, Observation(), {"kind": "empty", "clean": False}, first_plan)
    errors = []

    for tried, idx in enumerate(order, start=1):
        name, psm = OCR_PLANS[idx]
        try:
            if name not in cache:
                cache[name] = _variant(base, name)
            text = _tesseract(cache[name], psm)
        except Exception as exc:
            errors.append(str(exc))
            continue

        obs = parse_observation(text)
        merged, meta = reconcile(existing, board, obs, banned)
        score = (
            len(merged) * 100
            + meta["agree"] * 10
            - meta["stalled"] * 40
            + (30 if meta["clean"] else 0)
            + len(obs.tokens)
        )
        if score > best[0]:
            best = (score, obs, meta, idx)

        # Chỉ dừng sớm khi frame thực sự đủ thông tin. Đặc biệt ở đầu ván,
        # một plan có thể chỉ đọc được "e4" rồi kết luận sai rằng frame sạch.
        # Với danh sách ngắn/mất số thứ tự, thử thêm plan để chống highlight.
        if not exhaustive and meta["clean"] and meta["added"] > 0:
            anchored_count = sum(p is not None for p in obs.plies)
            enough_tokens = len(obs.tokens) >= 3
            if enough_tokens and (anchored_count > 0 or existing):
                break

    if not best[1] and errors:
        raise RuntimeError(errors[0])
    return best[1], best[2], best[3]


# =========================================================
# STOCKFISH SERVICE
# =========================================================

def format_eval(score_white):
    if score_white.is_mate():
        mate = score_white.mate()
        return f"M{abs(mate)} {'Trắng' if mate > 0 else 'Đen'}"
    return f"{score_white.score() / 100:+.2f}"


def describe_move(board, move):
    tags = []
    if board.is_castling(move):
        tags.append("nhập thành")
    if board.is_capture(move):
        tags.append("ăn quân")
    if move.promotion:
        tags.append("phong cấp")
    if board.gives_check(move):
        after = board.copy(stack=False)
        after.push(move)
        tags.append("chiếu hết" if after.is_checkmate() else "chiếu")
    return ", ".join(tags)


@dataclass
class AnalysisJob:
    key: int                 # số nước đã đi (end_count)
    board: chess.Board
    move_label: str
    mover: str
    seconds: float
    multipv: int
    generation: int
    manual: bool = False


class EngineService:
    """Một luồng duy nhất sở hữu tiến trình Stockfish.

    * Luôn lấy vị trí MỚI NHẤT trước; vị trí cũ chỉ được phân tích nhanh để điền lịch sử.
    * Việc mới đến -> dừng ngay phân tích đang chạy (không phải chờ hết thời gian).
    * Engine chết / treo pipe -> tự khởi động lại và thử lại 1 lần.
    """

    def __init__(self, emit, engine_path=None):
        self._emit = emit                  # emit(kind, payload) — phải thread-safe
        self.engine_path = engine_path
        self._cv = threading.Condition()
        self._jobs = {}
        self._current = None
        self._preempt = False
        self._stopping = False
        self._restart = False
        self._latest_key = 0
        self._engine = None
        self._cache = OrderedDict()
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()

    # ---- API gọi từ UI thread ----
    def submit(self, job):
        with self._cv:
            # Live mode chỉ cần vị trí mới nhất. Nếu OCR bắt được nước nhanh
            # hơn Stockfish, không được để hàng đợi cũ phình ra rồi xử lý lần lượt.
            if not job.manual:
                for key in list(self._jobs):
                    old = self._jobs.pop(key)
                    self._emit("dropped", {"key": key, "generation": old.generation})
            self._jobs[job.key] = job
            self._latest_key = max(self._latest_key, job.key)
            if self._current is not None:
                self._preempt = True
            self._cv.notify()

    def clear(self):
        with self._cv:
            self._jobs.clear()
            self._latest_key = 0
            self._preempt = True

    def set_engine_path(self, path):
        with self._cv:
            self.engine_path = path
            self._restart = True
            self._preempt = True
            self._cv.notify()

    def shutdown(self):
        with self._cv:
            self._stopping = True
            self._preempt = True
            self._jobs.clear()
            self._cv.notify()
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            self._kill_engine()

    # ---- luồng engine ----
    def _run(self):
        while True:
            with self._cv:
                while not self._jobs and not self._stopping:
                    self._cv.wait()
                if self._stopping:
                    break
                key = max(self._jobs, key=lambda k: (self._jobs[k].manual, k))
                job = self._jobs.pop(key)
                # Manual jobs có thể có một burst nhỏ; realtime auto jobs đã
                # được coalesced tại submit().
                if job.manual and len(self._jobs) > MAX_BURST_ANALYSIS:
                    for old in sorted(self._jobs)[:-MAX_BURST_ANALYSIS]:
                        dropped = self._jobs.pop(old)
                        self._emit("dropped", {"key": old, "generation": dropped.generation})
                self._current = job
                self._preempt = False
                is_latest = job.manual or job.key >= self._latest_key
                if self._restart:
                    self._restart = False
                    self._kill_engine()
            try:
                self._process(job, is_latest)
            except Exception as exc:
                self._emit("error", {"key": job.key, "generation": job.generation,
                                     "move_label": job.move_label, "error": str(exc)})
            finally:
                with self._cv:
                    self._current = None
        self._kill_engine()

    def _ensure_engine(self):
        if self._engine is not None:
            return self._engine
        path = find_stockfish(self.engine_path)
        if not path:
            raise FileNotFoundError(
                "Không tìm thấy Stockfish. Đặt stockfish.exe cạnh main.py, trong thư mục "
                "'stockfish', hoặc bấm ⚙ để chọn file."
            )
        kwargs = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        engine = chess.engine.SimpleEngine.popen_uci(path, timeout=15, **kwargs)
        cores = os.cpu_count() or 2
        wanted = {"Threads": max(1, min(4, cores // 2)), "Hash": 128}   # chừa CPU cho Tesseract
        for name, value in wanted.items():
            if name in engine.options:
                try:
                    engine.configure({name: value})
                except Exception:
                    pass
        self._engine = engine
        return engine

    def _kill_engine(self):
        engine, self._engine = self._engine, None
        if engine is None:
            return
        try:
            engine.quit()
        except Exception:
            pass
        try:
            engine.close()
        except Exception:
            pass

    def _process(self, job, is_latest):
        seconds = job.seconds if is_latest else min(job.seconds, BACKLOG_ENGINE_TIME)
        cache_key = (job.board.epd(), job.multipv)

        cached = self._cache.get(cache_key)
        if cached and (cached["seconds"] >= seconds * 0.8 or cached["mate"]):
            self._cache.move_to_end(cache_key)
            self._emit("result", self._payload(job, cached["lines"], final=True))
            return

        for attempt in (1, 2):
            try:
                engine = self._ensure_engine()
                lines, complete = self._analyse(engine, job, seconds)
                break
            except FileNotFoundError:
                raise
            except Exception:
                # EngineTerminatedError, EngineError, BrokenPipe, timeout...
                self._kill_engine()
                if attempt == 2:
                    raise
                time.sleep(0.2)

        if not lines:
            self._emit("dropped", {"key": job.key, "generation": job.generation})
            return
        if complete:
            self._cache[cache_key] = {
                "lines": lines, "seconds": seconds,
                "mate": lines[0]["mate"],
            }
            while len(self._cache) > 512:
                self._cache.popitem(last=False)
        self._emit("result", self._payload(job, lines, final=True))

    def _analyse(self, engine, job, seconds):
        limit = chess.engine.Limit(time=seconds)
        stopped = False
        last_emit = 0.0
        memo = {}

        def snapshot(infos):
            # Dòng info cuối của Stockfish (lowerbound/upperbound) thường chỉ có PV 1 nước:
            # giữ lại PV dài hơn nếu nước đầu không đổi.
            out = []
            for i, info in enumerate(infos):
                pv = info.get("pv")
                if pv:
                    old = memo.get(i)
                    if old and old[0] == pv[0] and len(old) > len(pv):
                        info = dict(info, pv=old)
                    else:
                        memo[i] = list(pv)
                out.append(info)
            return out
        with engine.analysis(job.board, limit, multipv=job.multipv) as analysis:
            for _ in analysis:
                current = snapshot(analysis.multipv)
                if (self._preempt or self._stopping) and not stopped:
                    stopped = True
                    analysis.stop()
                now = time.monotonic()
                if not stopped and now - last_emit > 0.15:
                    lines = self._lines(job.board, current)
                    if lines and lines[0]["depth"] >= 8:
                        last_emit = now
                        self._emit("result", self._payload(job, lines, final=False))
            infos = snapshot(analysis.multipv)
        return self._lines(job.board, infos), not stopped

    @staticmethod
    def _lines(board, infos):
        lines = []
        for info in infos:
            pv = info.get("pv")
            score = info.get("score")
            if not pv or score is None:
                continue
            white = score.white()
            sans, temp = [], board.copy(stack=False)
            for mv in pv[:8]:
                if mv not in temp.legal_moves:
                    break
                sans.append(temp.san(mv))
                temp.push(mv)
            if not sans:
                continue
            try:
                expectation = white.wdl(model="sf", ply=board.ply()).expectation()
            except Exception:
                expectation = 0.5
            lines.append({
                "san": sans[0], "uci": pv[0].uci(), "eval": format_eval(white),
                "expectation": expectation, "mate": white.is_mate(),
                "depth": info.get("depth", 0), "pv": " ".join(sans),
                "tags": describe_move(board, pv[0]),
            })
        return lines

    @staticmethod
    def _payload(job, lines, final):
        return {
            "key": job.key, "generation": job.generation, "final": final,
            "move_label": job.move_label, "mover": job.mover, "lines": lines,
            "fen": job.board.fen(), "manual": job.manual,
        }


# =========================================================
# THEME
# =========================================================

C = {
    "bg": "#14171d", "card": "#1d222b", "card2": "#252b36", "border": "#2f3745",
    "text": "#e7eaf0", "muted": "#8a94a6", "accent": "#4ea1ff", "accent_dk": "#2b6fc2",
    "good": "#4cc38a", "warn": "#f0b35a", "bad": "#ef6b6b",
    "sq_light": "#d9dee8", "sq_dark": "#6f86a6", "sq_last": "#e8c55a", "arrow": "#2f9bff",
}

PIECE_GLYPH = {"k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟"}


def pick_font(candidates, fallback="TkDefaultFont"):
    families = set(tkfont.families())
    return next((f for f in candidates if f in families), fallback)


class FlatButton(tk.Label):
    """Nút phẳng có hover; giống nhau trên mọi nền tảng (ttk trên Windows bỏ qua màu nền)."""

    def __init__(self, parent, text, command, kind="normal", font=None, padx=10, pady=5):
        self._palette = {
            "normal": (C["card2"], "#303848", C["text"]),
            "primary": (C["accent_dk"], C["accent"], "#ffffff"),
            "danger": ("#5a2a2e", "#7a353a", "#ffd9d9"),
            "ghost": (C["bg"], C["card2"], C["muted"]),
        }[kind]
        super().__init__(parent, text=text, bg=self._palette[0], fg=self._palette[2],
                         font=font, padx=padx, pady=pady, cursor="hand2")
        self._command = command
        self._enabled = True
        self.bind("<Enter>", lambda _e: self._enabled and self.configure(bg=self._palette[1]))
        self.bind("<Leave>", lambda _e: self.configure(bg=self._palette[0]))
        self.bind("<ButtonRelease-1>", self._click)

    def _click(self, event):
        if self._enabled and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self._command()

    def set_kind_colors(self, normal, hover, fg):
        self._palette = (normal, hover, fg)
        self.configure(bg=normal, fg=fg)

    def set_enabled(self, enabled):
        self._enabled = enabled
        self.configure(fg=self._palette[2] if enabled else C["muted"],
                       cursor="hand2" if enabled else "arrow")


class Segmented(tk.Frame):
    def __init__(self, parent, options, variable, command=None, font=None):
        super().__init__(parent, bg=C["border"], padx=1, pady=1)
        self.variable, self.command, self.items = variable, command, {}
        for value, label in options:
            item = tk.Label(self, text=label, font=font, padx=9, pady=3, cursor="hand2")
            item.pack(side="left")
            item.bind("<ButtonRelease-1>", lambda _e, v=value: self._select(v))
            self.items[value] = item
        self.refresh()

    def _select(self, value):
        self.variable.set(value)
        self.refresh()
        if self.command:
            self.command()

    def refresh(self):
        for value, item in self.items.items():
            on = value == self.variable.get()
            item.configure(bg=C["accent_dk"] if on else C["card"], fg="#ffffff" if on else C["muted"])


# =========================================================
# REGION SELECTOR
# =========================================================

class RegionSelector(tk.Toplevel):
    def __init__(self, parent, screenshot, monitor, callback):
        super().__init__(parent)
        self.callback = callback
        self.start = None
        self.shot = screenshot
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.geometry(f"{screenshot.width}x{screenshot.height}+{monitor['left']}+{monitor['top']}")

        self.dim_img = ImageTk.PhotoImage(ImageEnhance.Brightness(screenshot).enhance(0.45))
        self.sel_img = None

        self.canvas = tk.Canvas(self, width=screenshot.width, height=screenshot.height,
                                highlightthickness=0, cursor="crosshair", bg="black")
        self.canvas.pack()
        self.canvas.create_image(0, 0, image=self.dim_img, anchor="nw")
        self.sel_item = self.canvas.create_image(0, 0, anchor="nw")
        self.rect = self.canvas.create_rectangle(0, 0, 0, 0, outline=C["accent"], width=2, state="hidden")
        self.size_text = self.canvas.create_text(0, 0, anchor="sw", fill="#ffffff",
                                                 font=("Segoe UI", 10, "bold"), text="")
        self.canvas.create_rectangle(0, 0, 640, 40, fill="#000000", outline="")
        self.canvas.create_text(14, 10, anchor="nw", fill="#ffffff", font=("Segoe UI", 13, "bold"),
                                text="Kéo chuột chọn đúng vùng DANH SÁCH NƯỚC ĐI (gồm cả số thứ tự)  •  ESC để hủy")

        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.bind("<Escape>", lambda _e: self.callback(None))
        self.focus_force()
        self.lift()

    def _box(self, event):
        x1, y1 = self.start
        return min(x1, event.x), min(y1, event.y), max(x1, event.x), max(y1, event.y)

    def on_down(self, event):
        self.start = (event.x, event.y)
        self.canvas.itemconfigure(self.rect, state="normal")

    def on_drag(self, event):
        if self.start is None:
            return
        left, top, right, bottom = self._box(event)
        self.canvas.coords(self.rect, left, top, right, bottom)
        if right - left > 2 and bottom - top > 2:
            self.sel_img = ImageTk.PhotoImage(self.shot.crop((left, top, right, bottom)))
            self.canvas.itemconfigure(self.sel_item, image=self.sel_img)
            self.canvas.coords(self.sel_item, left, top)
        self.canvas.itemconfigure(self.size_text, text=f"{right - left} × {bottom - top}")
        self.canvas.coords(self.size_text, left, max(14, top - 4))

    def on_up(self, event):
        if self.start is None:
            return
        left, top, right, bottom = self._box(event)
        if right - left < 60 or bottom - top < 40:
            self.start = None
            self.canvas.itemconfigure(self.size_text, text="Vùng quá nhỏ — kéo lại")
            return
        self.callback((left, top, right, bottom))


# =========================================================
# MAIN APPLICATION
# =========================================================

class ChessMoveReader(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("540x780")
        self.minsize(460, 560)
        self.configure(bg=C["bg"])
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        ui = pick_font(("Segoe UI", "Inter", "Helvetica Neue", "DejaVu Sans"))
        mono = pick_font(("Cascadia Mono", "Consolas", "DejaVu Sans Mono"), "TkFixedFont")
        self.piece_font = pick_font(("Segoe UI Symbol", "DejaVu Sans", "Arial Unicode MS"))
        self.F = {
            "ui": (ui, 10), "small": (ui, 9), "tiny": (ui, 8), "bold": (ui, 10, "bold"),
            "title": (ui, 13, "bold"), "huge": (ui, 30, "bold"), "big": (ui, 13, "bold"),
            "mono": (mono, 12),
        }

        self.cfg = self._load_config()
        self.region = tuple(self.cfg["region"]) if self.cfg.get("region") else None
        self.region_monitor = self.cfg.get("monitor")

        self.tracker = GameTracker()
        self.moves = []
        self.running = False
        self.ocr_session = 0
        self.worker = None              # OCR consumer
        self.capture_worker = None       # màn hình producer
        self.frame_cv = threading.Condition()
        self.latest_frame = None         # (width, height, rgb_bytes, signature)
        self.latest_frame_seq = 0
        self.latest_frame_changed_at = 0.0
        self.generation = 0
        self.requested = {}          # end_count -> fen đã gửi engine
        self.history = {}            # end_count -> (iid)
        self.history_keys = []
        self.current_line = None     # dòng tốt nhất của vị trí mới nhất
        self.flipped = False

        # Bản sao thuần Python của các thiết lập: luồng nền KHÔNG được đụng vào biến Tk.
        self.cfg_interval = float(self.cfg.get("interval", DEFAULT_INTERVAL))
        self.cfg_engine_time = float(self.cfg.get("engine_time", ENGINE_TIME))
        self.cfg_multipv = int(self.cfg.get("multipv", 1))
        self.analysis_side = self.cfg.get("side", "both")
        self.auto_analyze_enabled = bool(self.cfg.get("auto", True))

        self.ui_queue = queue.Queue()
        self.engine = EngineService(self._emit_engine, self.cfg.get("engine_path"))

        self._style()
        self.build_ui()
        self.after(40, self._drain_ui_queue)

        try:
            pytesseract.get_tesseract_version()
        except Exception:
            self.after(150, self.show_tesseract_error)

        if self.region:
            l, t, r, b = self.region
            self.set_status(f"Vùng đã lưu: {r - l}×{b - t} — bấm ▶ để bắt đầu", "muted")

    # ---------------- infrastructure ----------------

    def post(self, fn, *args):
        """Gọi fn(*args) trên main thread. An toàn từ mọi luồng."""
        self.ui_queue.put((fn, args))

    def _drain_ui_queue(self):
        try:
            for _ in range(200):
                fn, args = self.ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception as exc:       # một callback lỗi không được làm chết vòng lặp UI
                    print("UI callback error:", exc, file=sys.stderr)
        except queue.Empty:
            pass
        self.after(40, self._drain_ui_queue)

    def _emit_engine(self, kind, payload):
        self.post(self._on_engine_event, kind, payload)

    def _load_config(self):
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_config(self):
        data = {
            "region": self.region, "monitor": self.region_monitor,
            "interval": self.cfg_interval, "engine_time": self.cfg_engine_time,
            "multipv": self.cfg_multipv, "side": self.analysis_side,
            "auto": self.auto_analyze_enabled, "engine_path": self.engine.engine_path,
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=C["bg"], foreground=C["text"], font=self.F["ui"])
        flat = {"bordercolor": C["border"], "lightcolor": C["card"], "darkcolor": C["card"]}
        style.configure("TNotebook", background=C["bg"], borderwidth=0, tabmargins=(0, 4, 0, 0), **flat)
        style.configure("TNotebook.Tab", background=C["bg"], foreground=C["muted"],
                        padding=(14, 6), borderwidth=0, font=self.F["bold"], **flat)
        style.map("TNotebook.Tab", background=[("selected", C["card"])],
                  foreground=[("selected", C["text"])])
        style.configure("Treeview", background=C["card"], fieldbackground=C["card"],
                        foreground=C["text"], borderwidth=0, rowheight=24, font=self.F["small"])
        style.configure("Treeview.Heading", background=C["card2"], foreground=C["muted"],
                        borderwidth=0, font=self.F["tiny"], padding=4)
        style.map("Treeview", background=[("selected", C["accent_dk"])],
                  foreground=[("selected", "#ffffff")])
        style.configure("Vertical.TScrollbar", background=C["card2"], troughcolor=C["card"],
                        borderwidth=0, arrowcolor=C["muted"])
        style.configure("TSpinbox", fieldbackground=C["card2"], background=C["card2"],
                        foreground=C["text"], arrowcolor=C["text"], borderwidth=0,
                        insertcolor=C["text"], padding=2, **flat)
        style.configure("Treeview", **flat)

    # ---------------- UI ----------------

    def _label(self, parent, text="", font="ui", fg="text", bg=None, **kw):
        return tk.Label(parent, text=text, font=self.F[font], fg=C[fg],
                        bg=bg or parent["bg"], **kw)

    def build_ui(self):
        outer = tk.Frame(self, bg=C["bg"], padx=12, pady=10)
        outer.pack(fill="both", expand=True)

        # ---- header
        head = tk.Frame(outer, bg=C["bg"])
        head.pack(fill="x")
        self._label(head, "♞", "title", "accent").pack(side="left")
        self._label(head, " Chess Move Reader", "title").pack(side="left")
        self.pin_button = FlatButton(head, "📌 Ghim", self.toggle_topmost, "ghost", self.F["small"])
        self.pin_button.pack(side="right")
        FlatButton(head, "⚙ Stockfish", self.choose_engine, "ghost", self.F["small"]).pack(side="right")

        # ---- toolbar
        bar = tk.Frame(outer, bg=C["bg"])
        bar.pack(fill="x", pady=(10, 6))
        self.start_button = FlatButton(bar, "▶  Bắt đầu", self.toggle_reader, "primary", self.F["bold"], 14)
        self.start_button.pack(side="left")
        for text, cmd, kind in (("⛶ Chọn vùng", self.select_region, "normal"),
                                ("Test OCR", self.test_ocr, "normal"),
                                ("Ván mới", self.clear_moves, "danger")):
            FlatButton(bar, text, cmd, kind, self.F["ui"]).pack(side="left", padx=(6, 0))

        # ---- settings
        sets = tk.Frame(outer, bg=C["bg"])
        sets.pack(fill="x", pady=(0, 8))
        self.interval = tk.DoubleVar(value=self.cfg_interval)
        self.engine_time = tk.DoubleVar(value=self.cfg_engine_time)
        self.multipv = tk.IntVar(value=self.cfg_multipv)
        for label, var, lo, hi, inc, width in (("OCR (s)", self.interval, 0.15, 5.0, 0.05, 5),
                                               ("Engine (s)", self.engine_time, 0.05, 5.0, 0.05, 5),
                                               ("Số biến", self.multipv, 1, 3, 1, 3)):
            self._label(sets, label, "small", "muted").pack(side="left")
            ttk.Spinbox(sets, from_=lo, to=hi, increment=inc, textvariable=var,
                        width=width, font=self.F["small"]).pack(side="left", padx=(4, 10))
            var.trace_add("write", self._on_settings_changed)

        row2 = tk.Frame(outer, bg=C["bg"])
        row2.pack(fill="x", pady=(0, 8))
        self._label(row2, "Phân tích sau nước của", "small", "muted").pack(side="left", padx=(0, 6))
        self.analysis_side_var = tk.StringVar(value=self.analysis_side)
        Segmented(row2, (("white", "Trắng"), ("black", "Đen"), ("both", "Cả hai")),
                  self.analysis_side_var, self.on_analysis_side_change, self.F["small"]).pack(side="left")
        self.auto_var = tk.StringVar(value="on" if self.auto_analyze_enabled else "off")
        Segmented(row2, (("on", "Auto"), ("off", "Tắt")), self.auto_var,
                  self.on_auto_analyze_toggle, self.F["small"]).pack(side="right")

        # ---- best move card
        card = tk.Frame(outer, bg=C["card"], padx=14, pady=10,
                        highlightbackground=C["border"], highlightthickness=1)
        card.pack(fill="x")
        top = tk.Frame(card, bg=C["card"])
        top.pack(fill="x")
        self.last_analyzed_var = tk.StringVar(value="NƯỚC TỐT NHẤT")
        tk.Label(top, textvariable=self.last_analyzed_var, font=self.F["tiny"],
                 fg=C["muted"], bg=C["card"]).pack(side="left")
        self.depth_var = tk.StringVar(value="")
        tk.Label(top, textvariable=self.depth_var, font=self.F["tiny"],
                 fg=C["muted"], bg=C["card"]).pack(side="right")

        mid = tk.Frame(card, bg=C["card"])
        mid.pack(fill="x")
        self.best_move_var = tk.StringVar(value="—")
        self.best_move_label = tk.Label(mid, textvariable=self.best_move_var, font=self.F["huge"],
                                        fg=C["accent"], bg=C["card"])
        self.best_move_label.pack(side="left")
        self.best_eval_var = tk.StringVar(value="")
        tk.Label(mid, textvariable=self.best_eval_var, font=self.F["big"],
                 fg=C["text"], bg=C["card"]).pack(side="right", anchor="s", pady=(0, 8))

        self.eval_bar = tk.Canvas(card, height=8, bg=C["card2"], highlightthickness=0)
        self.eval_bar.pack(fill="x", pady=(2, 6))
        self.eval_bar.bind("<Configure>", lambda _e: self._draw_eval_bar())
        self._expectation = 0.5

        self.best_reason_var = tk.StringVar(
            value="Chọn vùng danh sách nước đi rồi bấm ▶. Stockfish sẽ tự phân tích sau mỗi nước.")
        self.reason_label = tk.Label(card, textvariable=self.best_reason_var, font=self.F["small"],
                                     fg=C["muted"], bg=C["card"], justify="left", anchor="w", wraplength=480)
        self.reason_label.pack(fill="x")
        card.bind("<Configure>", lambda e: self.reason_label.configure(wraplength=max(200, e.width - 40)))

        # ---- tabs
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True, pady=(10, 0))
        self._build_board_tab()
        self._build_moves_tab()
        self._build_history_tab()
        self._build_debug_tab()

        # ---- status bar
        status = tk.Frame(outer, bg=C["bg"])
        status.pack(fill="x", pady=(8, 0))
        self.status_dot = tk.Label(status, text="●", font=self.F["small"], fg=C["muted"], bg=C["bg"])
        self.status_dot.pack(side="left")
        self.status = tk.StringVar(value="Chưa chọn vùng")
        tk.Label(status, textvariable=self.status, font=self.F["small"], fg=C["muted"],
                 bg=C["bg"], anchor="w").pack(side="left", fill="x", expand=True, padx=(4, 0))

    def _build_board_tab(self):
        tab = tk.Frame(self.tabs, bg=C["card"], padx=8, pady=8)
        self.tabs.add(tab, text="Bàn cờ")
        tools = tk.Frame(tab, bg=C["card"])
        tools.pack(fill="x")
        self._label(tools, "Kiểm tra nhanh OCR có dựng đúng thế cờ không", "tiny", "muted").pack(side="left")
        FlatButton(tools, "⇅ Lật", self.flip_board, "normal", self.F["tiny"], 8, 2).pack(side="right")
        self.board_canvas = tk.Canvas(tab, bg=C["card"], highlightthickness=0)
        self.board_canvas.pack(fill="both", expand=True, pady=(6, 0))
        self.board_canvas.bind("<Configure>", lambda _e: self.draw_board())

    def _build_moves_tab(self):
        tab = tk.Frame(self.tabs, bg=C["card"], padx=8, pady=8)
        self.tabs.add(tab, text="Nước đi")
        text_frame = tk.Frame(tab, bg=C["card"])
        text_frame.pack(fill="both", expand=True)
        self.output = tk.Text(text_frame, wrap="word", font=self.F["mono"], padx=8, pady=8,
                              bg=C["card2"], fg=C["text"], insertbackground=C["text"],
                              relief="flat", height=6, state="disabled")
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)

        tools = tk.Frame(tab, bg=C["card"])
        tools.pack(fill="x", pady=(8, 0))
        FlatButton(tools, "Copy", self.copy_plaintext, "normal", self.F["small"]).pack(side="left")
        FlatButton(tools, "Lưu .txt", self.save_txt, "normal", self.F["small"]).pack(side="left", padx=6)
        FlatButton(tools, "↶ Bỏ nước cuối", self.undo_last, "normal", self.F["small"]).pack(side="left")
        FlatButton(tools, "Thêm", self.add_manual_move, "normal", self.F["small"]).pack(side="right")
        self.manual_entry = tk.Entry(tools, width=8, font=self.F["mono"], bg=C["card2"], fg=C["text"],
                                     insertbackground=C["text"], relief="flat")
        self.manual_entry.pack(side="right", padx=6, ipady=3)
        self.manual_entry.bind("<Return>", lambda _e: self.add_manual_move())
        self._label(tools, "Sửa tay:", "tiny", "muted").pack(side="right")

    def _build_history_tab(self):
        tab = tk.Frame(self.tabs, bg=C["card"], padx=8, pady=8)
        self.tabs.add(tab, text="Lịch sử")
        columns = (("no", "#", 46), ("move", "Sau nước", 90), ("best", "Tốt nhất", 80),
                   ("eval", "Eval", 80), ("depth", "Depth", 50))
        self.analysis_tree = ttk.Treeview(tab, columns=[c[0] for c in columns], show="headings", height=6)
        for name, heading, width in columns:
            self.analysis_tree.heading(name, text=heading)
            self.analysis_tree.column(name, width=width, anchor="center", stretch=True)
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.analysis_tree.yview)
        self.analysis_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.analysis_tree.pack(side="left", fill="both", expand=True)

    def _build_debug_tab(self):
        tab = tk.Frame(self.tabs, bg=C["card"], padx=8, pady=8)
        self.tabs.add(tab, text="OCR")
        self.debug_info = tk.StringVar(value="Bấm 'Test OCR' để xem Tesseract đọc được gì.")
        tk.Label(tab, textvariable=self.debug_info, font=self.F["tiny"], fg=C["muted"], bg=C["card"],
                 justify="left", anchor="w", wraplength=480).pack(fill="x")
        self.debug_text = tk.Text(tab, wrap="none", font=self.F["mono"], padx=8, pady=8, height=6,
                                  bg=C["card2"], fg=C["text"], relief="flat")
        self.debug_text.pack(fill="both", expand=True, pady=(6, 0))

    # ---------------- small helpers ----------------

    def set_status(self, text, level="muted"):
        self.status.set(text)
        self.status_dot.configure(fg=C[level])

    def _draw_eval_bar(self):
        bar = self.eval_bar
        bar.delete("all")
        w, h = max(1, bar.winfo_width()), max(1, bar.winfo_height())
        split = int(w * min(0.98, max(0.02, self._expectation)))
        bar.create_rectangle(0, 0, split, h, fill="#f1f3f7", outline="")
        bar.create_rectangle(split, 0, w, h, fill="#0c0e12", outline="")
        bar.create_line(w // 2, 0, w // 2, h, fill=C["accent"])

    def _on_settings_changed(self, *_):
        for var, attr, lo, hi, cast in ((self.interval, "cfg_interval", 0.15, 5.0, float),
                                        (self.engine_time, "cfg_engine_time", 0.05, 5.0, float),
                                        (self.multipv, "cfg_multipv", 1, 3, int)):
            try:
                setattr(self, attr, min(hi, max(lo, cast(var.get()))))
            except (tk.TclError, ValueError):
                pass

    def show_tesseract_error(self):
        messagebox.showerror(
            "Thiếu Tesseract OCR",
            "Không tìm thấy tesseract.exe.\n\nKiểm tra:\n"
            "C:\\Program Files\\Tesseract-OCR\\tesseract.exe\n\nSau khi cài, mở lại app.")

    def toggle_topmost(self):
        pinned = not bool(self.attributes("-topmost"))
        self.attributes("-topmost", pinned)
        self.pin_button.configure(text="📌 Ghim" if pinned else "○ Không ghim")

    def choose_engine(self):
        path = filedialog.askopenfilename(
            title="Chọn file Stockfish",
            filetypes=[("Stockfish", "*.exe" if IS_WINDOWS else "*"), ("All files", "*.*")])
        if not path:
            return
        self.engine.set_engine_path(path)
        self._save_config()
        self.set_status(f"Stockfish: {Path(path).name}", "good")
        self.requested.clear()
        self.analyze_captured_position()

    def flip_board(self):
        self.flipped = not self.flipped
        self.draw_board()

    # ---------------- board drawing ----------------

    def draw_board(self):
        canvas = self.board_canvas
        canvas.delete("all")
        size = min(canvas.winfo_width(), canvas.winfo_height())
        if size < 80:
            return
        sq = (size - 34) // 8
        ox = (canvas.winfo_width() - sq * 8) // 2
        oy = (canvas.winfo_height() - sq * 8) // 2

        with self.tracker.lock:
            board = self.tracker.board.copy()
        last = board.peek() if board.move_stack else None

        def xy(square):
            f, r = chess.square_file(square), chess.square_rank(square)
            if self.flipped:
                f, r = 7 - f, 7 - r
            return ox + f * sq, oy + (7 - r) * sq

        font = (self.piece_font, -int(sq * 0.74))
        for square in chess.SQUARES:
            x, y = xy(square)
            light = (chess.square_file(square) + chess.square_rank(square)) % 2 == 1
            color = C["sq_light"] if light else C["sq_dark"]
            if last and square in (last.from_square, last.to_square):
                color = C["sq_last"]
            canvas.create_rectangle(x, y, x + sq, y + sq, fill=color, outline="")
            piece = board.piece_at(square)
            if piece:
                glyph = PIECE_GLYPH[piece.symbol().lower()]
                cx, cy = x + sq / 2, y + sq / 2
                fill, edge = ("#ffffff", "#20242c") if piece.color else ("#15181e", "#c9d1de")
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    canvas.create_text(cx + dx, cy + dy, text=glyph, font=font, fill=edge)
                canvas.create_text(cx, cy, text=glyph, font=font, fill=fill)

        for i in range(8):
            file_char = "abcdefgh"[7 - i if self.flipped else i]
            rank_char = str(i + 1 if self.flipped else 8 - i)
            canvas.create_text(ox + i * sq + sq / 2, oy + 8 * sq + 7, text=file_char,
                               font=self.F["tiny"], fill=C["muted"])
            canvas.create_text(ox - 7, oy + i * sq + sq / 2, text=rank_char,
                               font=self.F["tiny"], fill=C["muted"])

        line = self.current_line
        if line and line.get("fen") == board.fen():
            try:
                move = chess.Move.from_uci(line["uci"])
                (x1, y1), (x2, y2) = xy(move.from_square), xy(move.to_square)
                canvas.create_line(x1 + sq / 2, y1 + sq / 2, x2 + sq / 2, y2 + sq / 2,
                                   fill=C["arrow"], width=max(4, sq // 7), arrow="last",
                                   arrowshape=(sq * 0.38, sq * 0.45, sq * 0.18), capstyle="round")
            except ValueError:
                pass

    # ---------------- screen capture ----------------

    def capture_primary_screen(self):
        with mss.mss() as sct:
            monitor = dict(sct.monitors[1])
            raw = sct.grab(monitor)
            return monitor, Image.frombytes("RGB", raw.size, raw.rgb)

    def select_region(self):
        if self.running:
            self.stop_reader()
        self.withdraw()
        self.update()
        time.sleep(0.3)
        try:
            monitor, screenshot = self.capture_primary_screen()
        except Exception as exc:
            self.deiconify()
            messagebox.showerror("Lỗi chụp màn hình", str(exc))
            return
        self._pending_monitor = monitor
        self.region_selector = RegionSelector(self, screenshot, monitor, self.on_region_selected)

    def on_region_selected(self, region):
        selector = getattr(self, "region_selector", None)
        if selector is not None and selector.winfo_exists():
            selector.destroy()
        self.deiconify()
        self.lift()
        if region is None:
            self.set_status("Đã hủy chọn vùng")
            return
        self.region = region
        self.region_monitor = {k: self._pending_monitor[k] for k in ("left", "top", "width", "height")}
        self._save_config()
        left, top, right, bottom = region
        self.set_status(f"Vùng: {right - left}×{bottom - top} — bấm ▶ để bắt đầu", "good")

    def _region_box(self):
        if not self.region:
            raise RuntimeError("Chưa chọn vùng OCR.")
        monitor = self.region_monitor or {"left": 0, "top": 0}
        left, top, right, bottom = self.region
        return {"left": monitor["left"] + left, "top": monitor["top"] + top,
                "width": right - left, "height": bottom - top}

    # ---------------- OCR ----------------

    @staticmethod
    def _frame_signature(pixels):
        """Hash một mẫu nhỏ của ảnh để bỏ qua thay đổi pixel vô nghĩa."""
        if not pixels:
            return 0
        step = max(1, len(pixels) // 4096)
        return zlib.adler32(pixels[::step])

    def test_ocr(self):
        if not self.region:
            messagebox.showwarning("Chưa chọn vùng", "Bấm '⛶ Chọn vùng' và chọn danh sách nước đi trước.")
            return
        self.set_status("Đang test OCR…", "warn")
        box = self._region_box()
        existing, board, banned, _ = self.tracker.snapshot()

        def work():
            try:
                with mss.mss() as sct:
                    raw = sct.grab(box)
                img = Image.frombytes("RGB", raw.size, raw.rgb)
                started = time.monotonic()
                obs, meta, plan = ocr_frame(img, existing, board, banned, exhaustive=True)
                self.post(self._show_test_result, obs, meta, plan, time.monotonic() - started)
            except Exception as exc:
                self.post(self.set_status, f"Test OCR lỗi: {exc}"[:90], "bad")

        threading.Thread(target=work, daemon=True).start()

    def _show_test_result(self, obs, meta, plan, elapsed):
        valid = [t for t in obs.tokens if SAN_RE.match(t)]
        anchored = sum(p is not None for p in obs.plies)
        self.debug_text.delete("1.0", "end")
        self.debug_text.insert("1.0", obs.text.strip())
        hints = []
        if obs.tokens and anchored == 0:
            hints.append("⚠ Không đọc được số thứ tự — hãy chọn vùng bao gồm cả cột số (1. 2. 3.).")
        if len(obs.tokens) >= 4 and len(valid) < 0.6 * len(obs.tokens):
            hints.append("⚠ Nhiều token lỗi. Nếu web đang dùng ký hiệu HÌNH quân cờ (♘f3), "
                         "hãy đổi sang ký hiệu CHỮ (Nf3) trong cài đặt bàn cờ.")
        self.debug_info.set(
            f"Plan {OCR_PLANS[plan][0]}/psm{OCR_PLANS[plan][1]} • {len(valid)}/{len(obs.tokens)} token hợp lệ • "
            f"{anchored} có số thứ tự • khớp lịch sử: {meta['kind']} • {elapsed:.2f}s (thử tất cả plan)\n"
            + "\n".join(hints))
        self.tabs.select(3)
        self.set_status(f"Test xong • {len(valid)} nước đọc được", "good" if valid else "warn")

    def toggle_reader(self):
        self.stop_reader() if self.running else self.start_reader()

    def start_reader(self):
        if not self.region:
            messagebox.showwarning("Chưa chọn vùng", "Bấm '⛶ Chọn vùng' rồi chọn danh sách nước đi.")
            return
        if self.running:
            return

        self.running = True
        self.ocr_session += 1
        session = self.ocr_session
        box = self._region_box()

        # Reset latest-frame mailbox. Capture và OCR tách riêng để Tesseract
        # chạy 0.5s cũng không làm app bỏ lỡ các frame mới trong lúc đó.
        with self.frame_cv:
            self.latest_frame = None
            self.latest_frame_seq = 0
            self.latest_frame_changed_at = time.monotonic()

        self.start_button.configure(text="■  Dừng")
        self.start_button.set_kind_colors("#5a2a2e", "#7a353a", "#ffd9d9")
        self.set_status(f"Đang đọc realtime • {len(self.moves)} nước", "good")

        self.capture_worker = threading.Thread(
            target=self.capture_loop,
            args=(session, box),
            name="screen-capture",
            daemon=True,
        )
        self.worker = threading.Thread(
            target=self.ocr_loop,
            args=(session,),
            name="ocr",
            daemon=True,
        )
        self.capture_worker.start()
        self.worker.start()

        self.analyze_latest_qualifying_position()

    def stop_reader(self):
        self.running = False
        with self.frame_cv:
            self.frame_cv.notify_all()
        self.start_button.configure(text="▶  Bắt đầu")
        self.start_button.set_kind_colors(C["accent_dk"], C["accent"], "#ffffff")
        self.set_status("Đã dừng OCR • kết quả vẫn được giữ")

    def capture_loop(self, session, box):
        """Producer: chụp màn hình nhanh, chỉ giữ frame mới nhất."""
        last_sig = None
        with mss.mss() as sct:
            while self.running and session == self.ocr_session:
                try:
                    raw = sct.grab(box)
                    pixels = raw.rgb
                    signature = self._frame_signature(pixels)

                    if signature != last_sig:
                        last_sig = signature
                        now = time.monotonic()
                        with self.frame_cv:
                            self.latest_frame = (raw.width, raw.height, pixels, signature)
                            self.latest_frame_seq += 1
                            self.latest_frame_changed_at = now
                            self.frame_cv.notify()
                except Exception as exc:
                    self.post(self.set_status, f"Chụp màn hình lỗi: {str(exc)[:70]}", "bad")
                    time.sleep(0.2)
                    continue

                time.sleep(CAPTURE_POLL)

    def ocr_loop(self, session):
        """Consumer: luôn lấy frame MỚI NHẤT, không tích backlog OCR."""
        processed_seq = 0
        plan = 0
        failures = 0

        while self.running and session == self.ocr_session:
            # Chờ frame mới.
            with self.frame_cv:
                while (
                    self.running
                    and session == self.ocr_session
                    and self.latest_frame_seq <= processed_seq
                ):
                    self.frame_cv.wait(timeout=0.5)
                if not self.running or session != self.ocr_session:
                    break

                width, height, pixels, seq = self.latest_frame
                changed_at = self.latest_frame_changed_at

            # Debounce scroll animation: nếu chrome/chess.com đang cuộn,
            # frame tiếp theo sẽ thay thế frame này. Chỉ OCR khi ảnh đã đứng
            # yên một khoảng ngắn.
            wait = OCR_SETTLE_TIME - (time.monotonic() - changed_at)
            if wait > 0:
                time.sleep(wait)
                with self.frame_cv:
                    if self.latest_frame_seq != seq:
                        # Có frame mới hơn, bỏ qua frame cũ ngay lập tức.
                        continue
                    width, height, pixels, seq = self.latest_frame
                    changed_at = self.latest_frame_changed_at

            processed_seq = seq

            try:
                existing, board, banned, epoch = self.tracker.snapshot()
                img = Image.frombytes("RGB", (width, height), pixels)

                obs, meta, plan = ocr_frame(
                    img,
                    existing,
                    board,
                    banned,
                    plan,
                    exhaustive=False,
                )

                changed, meta = self.tracker.observe(obs, epoch)
                if changed:
                    self.post(self._on_tracker_changed, meta)
                else:
                    self.post(self._on_ocr_idle, meta)
                failures = 0

            except Exception as exc:
                failures += 1
                self.post(self.set_status, f"OCR lỗi: {str(exc)[:80]}", "bad")
                if failures >= 3:
                    time.sleep(0.25)

    def _on_ocr_idle(self, meta):
        if not self.running:
            return
        kind = meta.get("kind")
        count = len(self.moves)
        if kind == "need-start":
            self.set_status("Chưa thấy nước 1 • hãy để move list chứa số thứ tự", "warn")
        elif kind == "gap":
            self.set_status(f"Đang chờ khung tiếp theo • {count} nước", "warn")
        elif kind == "no-overlap":
            # Đây thường chỉ là frame trung gian khi chess.com vừa scroll.
            # Không báo "ván mới" vì sẽ gây hiểu nhầm và không làm reset lịch sử.
            self.set_status(f"Đang đồng bộ khung OCR • giữ {count} nước", "warn")
        elif meta.get("stalled"):
            self.set_status(f"Đang đọc • {count} nước • 1 token chưa rõ", "warn")
        elif meta.get("pending"):
            self.set_status(f"Đang đọc • {count} nước • xác nhận nước mới…", "good")
        else:
            self.set_status(f"Đang quét toàn khung • {count} nước", "good")

    def _on_tracker_changed(self, meta):
        old = self.moves
        with self.tracker.lock:
            new = list(self.tracker.sans)
        self.moves = new

        same = 0
        for a, b in zip(old, new):
            if a != b:
                break
            same += 1

        # Mọi thứ sau điểm khác nhau đầu tiên đều không còn đúng.
        for key in [k for k in self.requested if k > same]:
            del self.requested[key]
        for key in [k for k in self.history_keys if k > same]:
            self.analysis_tree.delete(self.history.pop(key))
            self.history_keys.remove(key)

        self.render_moves()
        self.draw_board()
        note = {"healed": " • đã tự sửa nước đọc nhầm", "rollback": " • đã sửa nước cuối"}.get(meta.get("kind"), "")
        self.set_status(f"Đang đọc • {len(new)} nước{note}" if self.running else f"{len(new)} nước{note}", "good")

        if self.auto_analyze_enabled and len(new) > same:
            # Hot path: CHỈ gửi vị trí mới nhất. Nếu chơi nhanh hơn Stockfish,
            # vị trí cũ được bỏ qua thay vì tạo backlog làm trễ vị trí hiện tại.
            self.schedule_analysis_for_position(len(new))

    def render_moves(self):
        lines = []
        for i in range(0, len(self.moves), 2):
            black = self.moves[i + 1] if i + 1 < len(self.moves) else ""
            lines.append(f"{i // 2 + 1}. {self.moves[i]} {black}".rstrip())
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", "\n".join(lines))
        self.output.configure(state="disabled")
        self.output.see("end")

    def undo_last(self):
        if self.tracker.undo_last():
            self._on_tracker_changed({"kind": "manual"})
            self.analyze_latest_qualifying_position()

    def add_manual_move(self):
        text = self.manual_entry.get().strip()
        if not text:
            return
        try:
            self.tracker.push_manual(text)
        except ValueError:
            self.set_status(f"'{text}' không hợp lệ ở thế cờ hiện tại", "bad")
            return
        self.manual_entry.delete(0, "end")
        self._on_tracker_changed({"kind": "manual"})

    # ---------------- analysis scheduling ----------------

    def on_auto_analyze_toggle(self):
        self.auto_analyze_enabled = self.auto_var.get() == "on"
        self.set_status("Phân tích tự động: " + ("BẬT" if self.auto_analyze_enabled else "TẮT"))
        self.analyze_latest_qualifying_position()

    def on_analysis_side_change(self):
        self.analysis_side = self.analysis_side_var.get()
        self.analyze_latest_qualifying_position()

    def should_analyze_after_move(self, move_index):
        if self.analysis_side == "both":
            return True
        return ("white" if move_index % 2 == 0 else "black") == self.analysis_side

    def analyze_latest_qualifying_position(self):
        if self.auto_analyze_enabled and self.moves:
            self.schedule_analysis_for_position(len(self.moves))

    def analyze_captured_position(self):
        if self.moves:
            self.schedule_analysis_for_position(len(self.moves), manual=True)

    def schedule_analysis_for_position(self, end_count, manual=False):
        if not 0 < end_count <= len(self.moves):
            return
        move_index = end_count - 1
        if not manual and not self.should_analyze_after_move(move_index):
            return
        try:
            board = build_board_from_moves(self.moves[:end_count])
        except ValueError as exc:
            self.set_status(str(exc), "bad")
            return

        fen = board.fen()
        if not manual and self.requested.get(end_count) == fen:
            return
        self.requested[end_count] = fen

        label = self.moves[move_index]
        mover = "Trắng" if move_index % 2 == 0 else "Đen"
        number = f"{move_index // 2 + 1}{'.' if move_index % 2 == 0 else '...'}"

        if board.is_game_over():
            self._upsert_history(end_count, (number, label, "—", board.result(), "—"))
            if end_count == len(self.moves):
                self.current_line = None
                self.last_analyzed_var.set(f"SAU {number} {label}")
                self.best_move_var.set("Hết ván")
                self.best_eval_var.set(board.result())
                self.best_reason_var.set("Ván cờ đã kết thúc.")
                self.depth_var.set("")
                self.draw_board()
            return

        if end_count == len(self.moves):
            self.last_analyzed_var.set(f"SAU {number} {label}  •  {'ĐEN' if board.turn == chess.BLACK else 'TRẮNG'} ĐI")
            self.best_move_label.configure(fg=C["muted"])
            self.depth_var.set("đang tính…")

        live_multipv = 1 if not manual else self.cfg_multipv
        self.engine.submit(AnalysisJob(
            key=end_count, board=board, move_label=f"{number} {label}", mover=mover,
            seconds=self.cfg_engine_time, multipv=live_multipv,
            generation=self.generation, manual=manual))

    def _on_engine_event(self, kind, payload):
        if payload.get("generation") != self.generation:
            return
        key = payload["key"]
        is_latest = key == len(self.moves)

        if kind == "dropped":
            self.requested.pop(key, None)
            return
        if kind == "error":
            self.requested.pop(key, None)
            if is_latest:
                self.best_move_var.set("Lỗi engine")
                self.best_move_label.configure(fg=C["bad"])
                self.best_eval_var.set("")
                self.depth_var.set("")
                self.best_reason_var.set(payload["error"])
                missing = "Không tìm thấy" in payload["error"]
                self.set_status("Chưa có Stockfish — bấm ⚙ Stockfish để chọn file" if missing
                                else "Stockfish lỗi — sẽ tự khởi động lại ở nước sau", "bad")
            return

        if self.requested.get(key) != payload["fen"]:
            return                                   # vị trí đã bị sửa sau khi gửi đi
        lines = payload["lines"]
        best = lines[0]
        if payload["final"]:
            number, label = payload["move_label"].split(" ", 1)
            self._upsert_history(key, (number, label, best["san"], best["eval"], best["depth"]))
        if not is_latest:
            return

        self.current_line = dict(best, fen=payload["fen"])
        self.best_move_var.set(best["san"])
        self.best_move_label.configure(fg=C["accent"] if payload["final"] else "#7fb6ee")
        self.best_eval_var.set(best["eval"])
        self.depth_var.set(f"depth {best['depth']}" + ("" if payload["final"] else " …"))
        self._expectation = best["expectation"]
        self._draw_eval_bar()

        text = []
        if best["tags"]:
            text.append(best["tags"].capitalize() + ".")
        text.append("Biến chính: " + best["pv"])
        for alt in lines[1:]:
            text.append(f"  {alt['san']}  ({alt['eval']})  {alt['pv']}")
        self.best_reason_var.set("\n".join(text))
        self.draw_board()

    def _upsert_history(self, key, values):
        iid = self.history.get(key)
        if iid and self.analysis_tree.exists(iid):
            self.analysis_tree.item(iid, values=values)
            return
        index = bisect.bisect_left(self.history_keys, key)
        iid = self.analysis_tree.insert("", index, values=values)
        self.history_keys.insert(index, key)
        self.history[key] = iid
        while len(self.history_keys) > ANALYSIS_HISTORY_LIMIT:
            oldest = self.history_keys.pop(0)
            self.analysis_tree.delete(self.history.pop(oldest))
        if index == len(self.history_keys) - 1:
            self.analysis_tree.see(iid)

    # ---------------- misc actions ----------------

    def clear_moves(self):
        self.generation += 1
        self.engine.clear()
        self.tracker.reset()
        self.moves = []
        self.requested.clear()
        self.history.clear()
        self.history_keys.clear()
        self.current_line = None
        self.analysis_tree.delete(*self.analysis_tree.get_children())
        self.best_move_var.set("—")
        self.best_move_label.configure(fg=C["accent"])
        self.best_eval_var.set("")
        self.depth_var.set("")
        self.last_analyzed_var.set("NƯỚC TỐT NHẤT")
        self.best_reason_var.set("Ván mới. Đang chờ nước đi đầu tiên.")
        self._expectation = 0.5
        self._draw_eval_bar()
        self.render_moves()
        self.draw_board()
        self.set_status("Ván mới" + (" • đang đọc" if self.running else ""), "good" if self.running else "muted")

    def get_plaintext(self):
        return self.output.get("1.0", "end-1c")

    def copy_plaintext(self):
        self.clipboard_clear()
        self.clipboard_append(self.get_plaintext())
        self.set_status("Đã copy danh sách nước đi", "good")

    def save_txt(self):
        text = self.get_plaintext()
        if not text:
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            Path(path).write_text(text, encoding="utf-8")
            self.set_status("Đã lưu .txt", "good")

    def on_close(self):
        self.running = False
        self.generation += 1
        self._save_config()
        try:
            self.engine.shutdown()
        except Exception:
            pass
        self.destroy()


def enable_dpi_awareness():
    """Windows: nếu không bật, toạ độ Tk (đã scale 125%/150%) lệch so với pixel thật
    của mss -> vùng OCR bị cắt lệch. Phải gọi TRƯỚC khi tạo cửa sổ Tk."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


if __name__ == "__main__":
    enable_dpi_awareness()
    ChessMoveReader().mainloop()
