"""
Lấy dữ liệu lưới FarPoint của MES bằng CHÉP / DÁN như thao tác tay (thay nút Excel → WPS hay lỗi, 21/11):
  bấm ô góc trái-trên của lưới (chọn hết) → Ctrl+C → đọc chữ trong clipboard (cột cách nhau Tab) → DataFrame.
DÙNG CHUỘT + BÀN PHÍM THẬT: máy MES phải đang mở khóa màn hình, không ai dùng chuột trong lúc này (~5 giây).
"""
import ctypes
import re
import time
from datetime import datetime

import pandas as pd
import win32clipboard
import win32con
import win32gui

HEADER_KEYS = ("Factory", "Work Order")
_NUM_RE = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d+)?$|^-?\d+(\.\d+)?$")
_DT_RE = re.compile(r"^(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}/\d{1,2}/\d{4})([ T]\d{1,2}:\d{2}(:\d{2})?( ?[AP]M)?)?$",
                    re.I)


def clear_clipboard() -> bool:
    try:
        win32clipboard.OpenClipboard(0)
        try:
            win32clipboard.EmptyClipboard()
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


def read_clipboard_text(timeout: float = 5.0) -> str:
    """Chữ Unicode trong clipboard ('' = trống). Clipboard đang bị giữ thì thử lại tới timeout"""
    deadline = time.time() + timeout
    while True:
        try:
            win32clipboard.OpenClipboard(0)
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
                return ""
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            if time.time() >= deadline:
                raise
            time.sleep(0.3)


def _scale(hwnd: int) -> float:
    try:
        return (ctypes.windll.user32.GetDpiForWindow(hwnd) or 96) / 96
    except Exception:
        return 1.0


def copy_grid(grid_hwnd: int, top_hwnd: int, log, settle: float = 1.0) -> str:
    """
    Thử lần lượt các cách chọn hết lưới rồi Ctrl+C, trả chữ chép được NHIỀU DÒNG NHẤT:
      1. bấm ô góc trái-trên (giao của tiêu đề cột và cột số dòng) — cách chọn hết của FarPoint
      2. bấm ô dữ liệu đầu tiên → Ctrl+A
      3. bấm ô dữ liệu đầu tiên → Ctrl+Home → Ctrl+Shift+End (bôi từ đầu tới cuối)
    """
    from pywinauto import keyboard, mouse

    try:
        win32gui.ShowWindow(top_hwnd, win32con.SW_SHOW)
        win32gui.SetForegroundWindow(top_hwnd)
    except Exception:
        pass
    time.sleep(0.5)
    l, t, _r, _b = win32gui.GetWindowRect(grid_hwnd)
    s = _scale(grid_hwnd)
    corner = (l + int(14 * s), t + int(18 * s))
    first_cell = (l + int(60 * s), t + int(50 * s))
    tries = [
        ("bấm ô góc chọn hết", lambda: mouse.click(coords=corner)),
        ("Ctrl+A", lambda: (mouse.click(coords=first_cell), time.sleep(0.3), keyboard.send_keys("^a"))),
        ("bôi Ctrl+Home → Ctrl+Shift+End", lambda: (mouse.click(coords=first_cell), time.sleep(0.3),
                                                      keyboard.send_keys("^{HOME}"), time.sleep(0.2),
                                                      keyboard.send_keys("^+{END}"))),
    ]
    best = ""
    for name, select in tries:
        clear_clipboard()
        select()
        time.sleep(0.5)
        keyboard.send_keys("^c")
        time.sleep(settle)
        text = read_clipboard_text()
        lines = [x for x in text.splitlines() if x.strip()]
        log.info("Chép lưới (%s): %d dòng chữ, %d cột", name, len(lines),
                 max((x.count("\t") + 1 for x in lines), default=0))
        if len(lines) > len(best.splitlines()):
            best = text
        if len(lines) >= 2 and any(all(k in x for k in HEADER_KEYS) for x in lines[:3]):
            break                                   # có cả dòng tiêu đề + dữ liệu → đủ
    clear_clipboard()                               # bỏ dữ liệu lớn khỏi clipboard
    return best


def _convert(col: pd.Series) -> pd.Series:
    vals = [v for v in col if v != ""]
    if not vals:
        return col.replace("", None)
    if all(_NUM_RE.match(v) for v in vals):
        nums = pd.to_numeric(col.str.replace(",", "", regex=False).replace("", None), errors="coerce")
        return nums.map(lambda x: int(x) if pd.notna(x) and float(x).is_integer() else x)
    if all(_DT_RE.match(v) for v in vals):
        dt = pd.to_datetime(col.replace("", None), errors="coerce")
        if dt.notna().sum() == len(vals):
            return dt
    return col.replace("", None)


def parse_grid_text(text: str) -> pd.DataFrame:
    """Chữ chép từ lưới (Tab/xuống dòng) → DataFrame có tên cột như tiêu đề lưới MES (giống file Excel cũ)"""
    from .errors import MesError

    rows = [r.split("\t") for r in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    rows = [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise MesError("Ctrl+C không chép được gì từ lưới MES (máy MES đang khóa màn hình / có người dùng chuột / "
                       "cửa sổ MES bị che?) → làm lại", "MES_GRID_COPY_EMPTY")
    hdr = next((i for i, r in enumerate(rows[:5]) if all(k in r for k in HEADER_KEYS)), None)
    if hdr is None:
        preview = " | ".join("\t".join(r)[:120] for r in rows[:2])
        raise MesError(f"Chép lưới được {len(rows)} dòng nhưng KHÔNG có dòng tiêu đề {HEADER_KEYS} "
                       f"(lưới không chép kèm tiêu đề cột?). 2 dòng đầu: {preview}", "MES_GRID_COPY_FAILED")
    header = rows[hdr]
    width = max(len(r) for r in rows[hdr:])
    header = header + [""] * (width - len(header))
    data = [r + [""] * (width - len(r)) for r in rows[hdr + 1:]]
    # Cột số dòng của FarPoint (ô tiêu đề trống, dữ liệu 1, 2, 3...) và cột trống → bỏ
    keep = [j for j, h in enumerate(header) if h]
    names, seen = [], {}
    for j in keep:
        n = header[j]
        seen[n] = seen.get(n, 0) + 1
        names.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
    df = pd.DataFrame([[r[j] for j in keep] for r in data], columns=names, dtype=str)
    df = df[(df != "").any(axis=1)].reset_index(drop=True)
    for c in df.columns:
        df[c] = _convert(df[c])
    return df
