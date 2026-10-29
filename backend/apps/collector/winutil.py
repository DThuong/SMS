"""Hàm Win32 cấp thấp: click/điền chữ bằng message, chụp cửa sổ"""
import ctypes
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
