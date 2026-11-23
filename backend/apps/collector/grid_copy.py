"""
Lấy dữ liệu lưới FarPoint của MES bằng CHÉP / DÁN như thao tác tay (thay nút Excel → WPS hay lỗi, 21/11):
  bấm ô góc trái-trên của lưới (chọn hết) → Ctrl+C → đọc chữ trong clipboard (cột cách nhau Tab) → DataFrame.
Ưu tiên GỬI MESSAGE vào lưới (không chuột/bàn phím thật, chạy cả khi KHÓA MÀN HÌNH); không được mới dùng chuột thật
(chỉ khi màn hình mở khóa).
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


def _copy_with(select, log, name: str, settle: float) -> str:
    clear_clipboard()
    select()
    time.sleep(settle)
    text = read_clipboard_text()
    lines = [x for x in text.splitlines() if x.strip()]
    log.info("Chép lưới (%s): %d dòng chữ, %d cột", name, len(lines),
             max((x.count("\t") + 1 for x in lines), default=0))
    return text


def _good(text: str) -> bool:
    lines = [x for x in text.splitlines() if x.strip()]
    return len(lines) >= 2 and any(all(k in x for k in HEADER_KEYS) for x in lines[:3])


def copy_grid(grid_hwnd: int, top_hwnd: int, log, settle: float = 1.0) -> str:
    """
    Chọn hết lưới → Ctrl+C → trả chữ chép được NHIỀU DÒNG NHẤT. Thứ tự thử:
      A. GỬI MESSAGE vào lưới (không chuột/bàn phím thật — chạy được cả khi máy MES KHÓA MÀN HÌNH, 22–23/11):
         1. bấm ô góc trái-trên  2. Ctrl+A  3. Ctrl+Home → Ctrl+Shift+End     (mỗi cách xong gửi Ctrl+C)
      B. Chuột + bàn phím THẬT (cách đã chạy 21/11) — chỉ khi màn hình đang mở khóa
    """
    from . import winutil

    C, A, HOME, END = ord("C"), ord("A"), win32con.VK_HOME, win32con.VK_END
    CTRL, SHIFT = win32con.VK_CONTROL, win32con.VK_SHIFT
    s = _scale(grid_hwnd)
    # tọa độ trong lưới (client): ô góc và ô dữ liệu đầu
    cx, cy = int(14 * s), int(18 * s)
    fx, fy = int(60 * s), int(50 * s)

    def focus():
        winutil.focus_in_thread(grid_hwnd)
        time.sleep(0.2)

    def ctrl_c():
        winutil.post_combo(grid_hwnd, [C], (CTRL,))

    tries = [
        ("message: bấm ô góc + Ctrl+C",
         lambda: (focus(), winutil.post_click_at(grid_hwnd, cx, cy), time.sleep(0.5), ctrl_c())),
        ("message: Ctrl+A + Ctrl+C",
         lambda: (focus(), winutil.post_click_at(grid_hwnd, fx, fy), time.sleep(0.4),
                  winutil.post_combo(grid_hwnd, [A], (CTRL,)), ctrl_c())),
        ("message: Ctrl+Home → Ctrl+Shift+End + Ctrl+C",
         lambda: (focus(), winutil.post_click_at(grid_hwnd, fx, fy), time.sleep(0.4),
                  winutil.post_combo(grid_hwnd, [HOME], (CTRL,), 0.4),
                  winutil.post_combo(grid_hwnd, [END], (CTRL, SHIFT)), ctrl_c())),
    ]
    best = ""
    for name, select in tries:
        try:
            text = _copy_with(select, log, name, settle)
        except Exception as e:
            log.info("Chép lưới (%s) lỗi: %s", name, e)
            continue
        if len(text.splitlines()) > len(best.splitlines()):
            best = text
        if _good(text):
            clear_clipboard()
            return text

    if not winutil.desktop_active():
        log.warning("Máy MES đang KHÓA MÀN HÌNH → không dùng được chuột thật; cách gửi message không chép được đủ lưới")
    else:
        from pywinauto import keyboard, mouse
        try:
            win32gui.ShowWindow(top_hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(top_hwnd)
        except Exception:
            pass
        time.sleep(0.5)
        l, t, _r, _b = win32gui.GetWindowRect(grid_hwnd)
        real = [
            ("chuột thật: bấm ô góc + Ctrl+C",
             lambda: (mouse.click(coords=(l + cx, t + cy)), time.sleep(0.5), keyboard.send_keys("^c"))),
            ("chuột thật: Ctrl+A + Ctrl+C",
             lambda: (mouse.click(coords=(l + fx, t + fy)), time.sleep(0.3), keyboard.send_keys("^a"),
                      time.sleep(0.3), keyboard.send_keys("^c"))),
        ]
        for name, select in real:
            try:
                text = _copy_with(select, log, name, settle)
            except Exception as e:
                log.info("Chép lưới (%s) lỗi: %s", name, e)
                continue
            if len(text.splitlines()) > len(best.splitlines()):
                best = text
            if _good(text):
                break
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


def _desktop_active() -> bool:
    from .winutil import desktop_active
    return desktop_active()


def parse_grid_text(text: str) -> pd.DataFrame:
    """Chữ chép từ lưới (Tab/xuống dòng) → DataFrame có tên cột như tiêu đề lưới MES (giống file Excel cũ)"""
    from .errors import MesError

    rows = [r.split("\t") for r in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    rows = [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise MesError("Ctrl+C không chép được gì từ lưới MES (đã thử gửi message"
                       + ("" if _desktop_active() else "; máy MES đang KHÓA MÀN HÌNH nên không thử được chuột thật")
                       + ") → làm lại", "MES_GRID_COPY_EMPTY")
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
