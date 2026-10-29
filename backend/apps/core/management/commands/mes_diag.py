"""
Chẩn đoán agent trên máy MES (chạy trên Windows):

  python manage.py mes_diag sheet
      Trước khi chạy: trên MES bấm nút Excel bằng tay để WPS mở ra bảng dữ liệu (giữ nguyên WPS đang mở).
      Kiểm tra SMS có "nhìn thấy" bảng tính trong WPS qua COM không, bằng nhiều cách.

  python manage.py mes_diag date 2026-10-27
      Mở form "Product Time Confirm (TV)" (tự mở nếu chưa), đặt ngày vào ô FROM_DATE giống hệt job thật,
      chụp ảnh trước/sau (hoặc ảnh lúc lỗi).

Kết quả: backend/storage/probe/diag_*.json và diag_date_*.png
"""
import ctypes
import json
import time
from ctypes import wintypes
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

SHEET_EXE = {"et.exe", "wps.exe", "wpsoffice.exe", "excel.exe"}
PROGIDS = ["Ket.Application", "KET.Application", "ET.Application", "Excel.Application",
           "Kwps.Application", "WPS.Application"]
WM_GETOBJECT = 0x003D
OBJID_NATIVEOM = -16


class Command(BaseCommand):
    help = "Chẩn đoán WPS (COM) và cách đặt ngày FROM_DATE"

    def add_arguments(self, parser):
        parser.add_argument("what", choices=["sheet", "date"])
        parser.add_argument("value", nargs="?", help="Ngày YYYY-MM-DD (cho 'date')")
        parser.add_argument("--form", default="Product Time Confirm (TV)")

    def handle(self, *args, what, value, form, **opts):
        out = settings.STORAGE_DIR / "probe"
        out.mkdir(parents=True, exist_ok=True)
        if what == "sheet":
            self._sheet(out)
        else:
            if not value:
                raise CommandError("Thiếu ngày, vd: mes_diag date 2026-10-27")
            self._date(out, date.fromisoformat(value), form)

    # ------------------------------------------------------------------ WPS / COM
    def _sheet(self, out):
        import psutil
        import pythoncom
        import pywintypes
        import win32com.client
        import win32gui
        import win32process

        pythoncom.CoInitialize()
        rep = {}
        procs = {p.pid: p.info["name"] for p in psutil.process_iter(["name"])
                 if (p.info["name"] or "").lower() in SHEET_EXE}
        rep["processes"] = [f"{n} (pid {pid})" for pid, n in procs.items()]

        # 1. Running Object Table
        try:
            rot, ctx = pythoncom.GetRunningObjectTable(), pythoncom.CreateBindCtx(0)
            enum, names = rot.EnumRunning(), []
            while True:
                m = enum.Next(1)
                if not m:
                    break
                names.append(m[0].GetDisplayName(ctx, None))
            rep["rot"] = names
        except Exception as e:
            rep["rot_error"] = str(e)

        # 2. GetActiveObject theo từng ProgID
        rep["progids"] = {}
        for pid in PROGIDS:
            entry = {}
            try:
                pywintypes.IID(pid)
                entry["registered"] = True
            except pywintypes.com_error:
                entry["registered"] = False
            try:
                app = win32com.client.GetActiveObject(pid)
                entry["active"] = True
                entry["workbooks"] = [app.Workbooks.Item(i).Name for i in range(1, app.Workbooks.Count + 1)]
            except Exception as e:
                entry["active"] = False
                entry["error"] = str(e)[:150]
            rep["progids"][pid] = entry

        # 3. Cửa sổ của WPS + thử gắn qua WM_GETOBJECT (cách gắn vào Excel/WPS đang mở không qua ROT)
        user32 = ctypes.windll.user32
        send = user32.SendMessageTimeoutW
        send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                         wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        windows, accessible = [], []

        def visit(h, _):
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid in procs and win32gui.IsWindowVisible(h):
                windows.append({"hwnd": h, "class": win32gui.GetClassName(h), "title": win32gui.GetWindowText(h)[:80]})
                kids = []
                win32gui.EnumChildWindows(h, lambda c, __: kids.append(c), None)
                for c in [h] + kids[:300]:
                    res = ctypes.c_size_t(0)
                    if send(c, WM_GETOBJECT, 0, OBJID_NATIVEOM, 0x2, 500, ctypes.byref(res)) and res.value:
                        item = {"hwnd": c, "class": win32gui.GetClassName(c)}
                        try:
                            obj = pythoncom.ObjectFromLresult(res.value, pythoncom.IID_IDispatch, 0)
                            d = win32com.client.Dispatch(obj)
                            app = d.Application
                            item["app_name"] = str(app.Name)
                            item["workbooks"] = [app.Workbooks.Item(i).Name for i in range(1, app.Workbooks.Count + 1)]
                        except Exception as e:
                            item["error"] = str(e)[:150]
                        accessible.append(item)
            return True

        win32gui.EnumWindows(visit, None)
        rep["sheet_windows"] = windows
        rep["native_om"] = accessible

        (out / "diag_sheet.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(f"Tiến trình bảng tính: {rep['processes'] or 'KHÔNG CÓ'}")
        self.stdout.write(f"Trong ROT: {len(rep.get('rot', []))} mục")
        for k, v in rep["progids"].items():
            self.stdout.write(f"  {k:<18} đăng ký={v['registered']} gắn được={v['active']} {v.get('workbooks', '')}")
        self.stdout.write(f"Gắn qua cửa sổ (WM_GETOBJECT): {len(accessible)} kết quả")
        for a in accessible[:5]:
            self.stdout.write(f"  {a}")
        self.stdout.write(self.style.SUCCESS(f"Đã lưu {out / 'diag_sheet.json'}"))

    # ------------------------------------------------------------------ FROM_DATE
    def _date(self, out, d: date, title: str):
        import logging
        import sys

        from apps.collector import winutil
        from apps.collector.errors import AgentError
        from apps.plans.jobs import _load_collector, mes_config

        log = logging.getLogger("sms.mes_diag")
        log.setLevel(logging.INFO)
        if not log.handlers:
            hd = logging.StreamHandler(sys.stdout)
            hd.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
            log.addHandler(hd)
            log.propagate = False

        c = _load_collector("production")(log, mes_config())
        try:
            c.open(fresh=False)
            winutil.capture_window(c.mes.hwnd, out / "diag_date_0_truoc.png")
            c.set_from_date(d)
            winutil.capture_window(c.mes.hwnd, out / "diag_date_1_sau.png")
        except AgentError as e:
            try:
                winutil.capture_window(c.mes.hwnd, out / "diag_date_loi.png")
            except Exception:
                pass
            self.stdout.write(self.style.ERROR(f"LỖI [{e.code}]: {e}"))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS(f"Đã đặt FROM_DATE = {d:%m/%d/%Y}. Ảnh trước/sau trong {out}"))
