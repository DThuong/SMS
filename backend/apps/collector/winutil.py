"""Hàm Win32 cấp thấp: click/điền chữ/gửi phím bằng message, chụp cửa sổ, đặt ngày DateTimePicker"""
import ctypes
import struct as _struct
from ctypes import wintypes as _wt
from datetime import datetime as _datetime
from pathlib import Path

import win32api
import win32con
import win32gui
import win32ui
from PIL import Image


def enable_dpi_awareness():
    """Tránh lệch tọa độ khi Windows scale 125-150%"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def post_click(hwnd: int):
    """Click không dùng chuột: đặt BM_CLICK vào hàng đợi của nút (bất đồng bộ, không treo khi app mở modal)"""
    win32api.PostMessage(hwnd, win32con.BM_CLICK, 0, 0)


def set_text(hwnd: int, text: str):
    """Điền chữ vào ô Edit bằng WM_SETTEXT — WinForms vẫn bắn sự kiện TextChanged"""
    win32gui.SendMessage(hwnd, win32con.WM_SETTEXT, 0, text)


def capture_window(hwnd: int, path: Path) -> bool:
    """Chụp cửa sổ bằng PrintWindow (app tự vẽ vào bộ nhớ, không nháy màn hình) — chỉ dùng khi lỗi"""
    path.parent.mkdir(parents=True, exist_ok=True)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    hdc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(hdc)
    mem = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc, w, h)
    mem.SelectObject(bmp)
    ok = ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
    bi = bmp.GetInfo()
    Image.frombuffer("RGB", (bi["bmWidth"], bi["bmHeight"]), bmp.GetBitmapBits(True),
                     "raw", "BGRX", 0, 1).save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC(); mfc.DeleteDC(); win32gui.ReleaseDC(hwnd, hdc)
    return bool(ok)


def post_keys(hwnd: int, vkeys: list[int]):
    """Gửi phím điều hướng (mũi tên, Home...) thẳng vào control — không dùng bàn phím thật"""
    for vk in vkeys:
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0xC0000001)


def post_text(hwnd: int, text: str):
    """Gõ chữ vào control bằng WM_CHAR — không dùng bàn phím thật"""
    for ch in text:
        win32api.PostMessage(hwnd, win32con.WM_CHAR, ord(ch), 0)


def send_key(hwnd: int, vk: int, char: int | None = None, timeout_ms: int = 5000):
    """
    Gửi 1 phím bằng SendMessage (đi thẳng vào WndProc, KHÔNG qua vòng lặp message của WinForms → không bị
    PreProcessMessage/ProcessDialogKey nuốt mất). Sự kiện KeyDown/KeyPress của WinForms vẫn chạy.
    char: mã ký tự gửi kèm WM_CHAR (vd 13 cho Enter) — None = không gửi WM_CHAR.
    """
    scan = win32api.MapVirtualKey(vk, 0)
    down = 1 | (scan << 16)
    up = down | 0xC0000000
    win32gui.SendMessageTimeout(hwnd, win32con.WM_KEYDOWN, vk, down, win32con.SMTO_ABORTIFHUNG, timeout_ms)
    if char is not None:
        win32gui.SendMessageTimeout(hwnd, win32con.WM_CHAR, char, down, win32con.SMTO_ABORTIFHUNG, timeout_ms)
    win32gui.SendMessageTimeout(hwnd, win32con.WM_KEYUP, vk, up, win32con.SMTO_ABORTIFHUNG, timeout_ms)


# ======================= Bộ nhớ trong tiến trình MESClient =======================
# Một số message của control Windows (DateTimePicker...) truyền CON TRỎ tới struct → struct phải nằm trong
# bộ nhớ của chính tiến trình MESClient. Cấp phát vùng nhớ ở đó bằng VirtualAllocEx, ghi/đọc bằng
# WriteProcessMemory/ReadProcessMemory. Không dùng chuột/bàn phím thật.
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = _wt.HANDLE
_k32.OpenProcess.argtypes = [_wt.DWORD, _wt.BOOL, _wt.DWORD]
_k32.VirtualAllocEx.restype = ctypes.c_void_p
_k32.VirtualAllocEx.argtypes = [_wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, _wt.DWORD, _wt.DWORD]
_k32.VirtualFreeEx.restype = _wt.BOOL
_k32.VirtualFreeEx.argtypes = [_wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, _wt.DWORD]
_k32.WriteProcessMemory.restype = _wt.BOOL
_k32.WriteProcessMemory.argtypes = [_wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                    ctypes.POINTER(ctypes.c_size_t)]
_k32.ReadProcessMemory.restype = _wt.BOOL
_k32.ReadProcessMemory.argtypes = [_wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]
_k32.IsWow64Process.restype = _wt.BOOL
_k32.IsWow64Process.argtypes = [_wt.HANDLE, ctypes.POINTER(_wt.BOOL)]
_k32.GetCurrentProcess.restype = _wt.HANDLE
_k32.CloseHandle.argtypes = [_wt.HANDLE]

_PROCESS_ACCESS = 0x0008 | 0x0010 | 0x0020 | 0x0400   # VM_OPERATION | VM_READ | VM_WRITE | QUERY_INFORMATION
_MEM_COMMIT_RESERVE = 0x1000 | 0x2000
_MEM_RELEASE = 0x8000
_PAGE_READWRITE = 0x04


def _is_wow64(h) -> bool:
    flag = _wt.BOOL(False)
    return bool(_k32.IsWow64Process(h, ctypes.byref(flag)) and flag.value)


_OS_64BIT = _struct.calcsize("P") == 8 or _is_wow64(_k32.GetCurrentProcess())


class RemoteBuffer:
    """Vùng nhớ tạm trong tiến trình sở hữu cửa sổ `hwnd`. Dùng: with RemoteBuffer(h, 64) as rb: ..."""

    def __init__(self, hwnd: int, size: int):
        import win32process
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        self.proc = _k32.OpenProcess(_PROCESS_ACCESS, False, pid)
        if not self.proc:
            raise OSError(ctypes.get_last_error(), f"Không mở được tiến trình {pid} (chạy agent cùng quyền với MESClient)")
        self.addr = _k32.VirtualAllocEx(self.proc, None, size, _MEM_COMMIT_RESERVE, _PAGE_READWRITE)
        if not self.addr:
            err = ctypes.get_last_error()
            _k32.CloseHandle(self.proc)
            raise OSError(err, "VirtualAllocEx thất bại")
        self.size = size
        self.is64 = _OS_64BIT and not _is_wow64(self.proc)   # tiến trình đích 64-bit?

    def write(self, data: bytes, offset: int = 0):
        buf = ctypes.create_string_buffer(data, len(data))
        n = ctypes.c_size_t(0)
        if not _k32.WriteProcessMemory(self.proc, self.addr + offset, buf, len(data), ctypes.byref(n)):
            raise OSError(ctypes.get_last_error(), "WriteProcessMemory thất bại")

    def read(self, size: int, offset: int = 0) -> bytes:
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t(0)
        if not _k32.ReadProcessMemory(self.proc, self.addr + offset, buf, size, ctypes.byref(n)):
            raise OSError(ctypes.get_last_error(), "ReadProcessMemory thất bại")
        return buf.raw[:n.value]

    def close(self):
        if self.addr:
            _k32.VirtualFreeEx(self.proc, self.addr, 0, _MEM_RELEASE)
            self.addr = None
        if self.proc:
            _k32.CloseHandle(self.proc)
            self.proc = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ======================= DateTimePicker (ô ngày From/To của MES) =======================
DTM_GETSYSTEMTIME = 0x1001
DTM_SETSYSTEMTIME = 0x1002
GDT_VALID = 0


def _send(hwnd: int, msg: int, wparam: int, lparam: int, timeout_ms: int = 5000) -> int:
    _, res = win32gui.SendMessageTimeout(hwnd, msg, wparam, lparam, win32con.SMTO_ABORTIFHUNG, timeout_ms)
    return res


def _systemtime(dt: _datetime) -> bytes:
    return _struct.pack("<8H", dt.year, dt.month, dt.isoweekday() % 7, dt.day,
                        dt.hour, dt.minute, dt.second, 0)


def dtp_get(hwnd: int) -> _datetime | None:
    """Giá trị đang hiện trên ô DateTimePicker (None nếu ô đang trống/không đọc được)"""
    with RemoteBuffer(hwnd, 16) as rb:
        if _send(hwnd, DTM_GETSYSTEMTIME, 0, rb.addr) != GDT_VALID:
            return None
        y, m, _, d, hh, mm, ss, _ = _struct.unpack("<8H", rb.read(16))
    try:
        return _datetime(y, m, d, hh, mm, ss)
    except ValueError:
        return None


def _dtp_raw_set(hwnd: int, dt: _datetime):
    """DTM_SETSYSTEMTIME: ô hiện ngày mới (WinForms CHƯA biết — Value vẫn là ngày cũ)"""
    with RemoteBuffer(hwnd, 16) as rb:
        rb.write(_systemtime(dt), 0)
        if not _send(hwnd, DTM_SETSYSTEMTIME, GDT_VALID, rb.addr):
            raise OSError(0, "DTM_SETSYSTEMTIME bị từ chối (ngày ngoài khoảng MinDate/MaxDate?)")


def focus_window(hwnd: int):
    """Đặt focus bàn phím vào control của tiến trình khác (gắn tạm hàng đợi input của 2 luồng)"""
    import win32process
    tid, _ = win32process.GetWindowThreadProcessId(hwnd)
    cur = win32api.GetCurrentThreadId()
    attached = bool(ctypes.windll.user32.AttachThreadInput(cur, tid, True))
    try:
        win32gui.SetFocus(hwnd)
    except Exception:
        pass
    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(cur, tid, False)


def dtp_set(hwnd: int, dt: _datetime, up_first: bool = False) -> int:
    """
    Đặt ngày cho ô DateTimePicker của WinForms mà không dùng chuột/bàn phím thật.
    WinForms chỉ cập nhật DateTimePicker.Value khi chính ô ngày báo DTN_DATETIMECHANGE (Windows CẤM tiến trình khác
    gửi WM_NOTIFY vào MES — lỗi Access is denied), nên:
      1. DTM_SETSYSTEMTIME → ô hiện đúng ngày cần đặt
      2. Gửi 2 phím ngược nhau (SendMessage) vào ô → ô tự đổi 1 nấc rồi quay lại ở phần đang chọn (tháng/ngày/năm)
         và TỰ báo DTN_DATETIMECHANGE cho MES; lần báo cuối mang đúng ngày cần đặt → MES nhận như người dùng chọn
    up_first: thứ tự phím. MES tự sửa khoảng ngày khi Từ > Đến (vd đến ngày tạm < từ ngày → MES kéo từ ngày theo),
      nên ô "từ ngày" phải ↓ trước (giá trị tạm NHỎ hơn), ô "đến ngày" phải ↑ trước (giá trị tạm LỚN hơn).
    Phím không có tác dụng (đang đứng ở dấu "/"), hoặc đổi tháng làm lệch ngày (31/10 ↑ → 30/11) → đặt lại,
    chuyển sang phần khác (→) và thử lại; vẫn không được thì đặt focus vào ô rồi thử tiếp.
    Trả về số lần thử thêm (0 = được ngay).
    """
    want = dt.date()
    first, second = (win32con.VK_UP, win32con.VK_DOWN) if up_first else (win32con.VK_DOWN, win32con.VK_UP)

    def get():
        v = dtp_get(hwnd)
        return v.date() if v else None

    for attempt in range(8):
        _dtp_raw_set(hwnd, dt)
        if attempt == 4:
            focus_window(hwnd)
        if attempt % 4:
            send_key(hwnd, win32con.VK_RIGHT)            # sang phần khác của ô ngày
        send_key(hwnd, first)
        if get() in (None, want):
            continue                                      # phím không làm đổi gì → MES chưa được báo
        send_key(hwnd, second)
        if get() == want:
            return attempt
    raise OSError(0, "ô ngày không nhận phím ↑/↓ để báo MES đổi ngày")
