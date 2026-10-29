"""
Thăm dò một form trong MESClient (không dùng chuột) để biết AutomationId các control.
  python manage.py probe_form "Product Time Confirm (TV)"            # chỉ đọc cấu trúc
  python manage.py probe_form "Product Time Confirm (TV)" --export   # bấm thêm nút Excel, đọc 15 dòng đầu
Kết quả: backend/storage/probe/
"""
import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

MAX_CHILDREN = 40


class Command(BaseCommand):
    help = "Thăm dò cấu trúc một form MESClient"

    def add_arguments(self, parser):
        parser.add_argument("title", help="Tiêu đề form, vd: Product Time Confirm (TV)")
        parser.add_argument("--export", action="store_true", help="Bấm nút Excel rồi đọc dữ liệu xuất ra")

    def handle(self, *args, title, export, **opts):
        import win32gui
        from pywinauto import Desktop

        from apps.collector import winutil

        winutil.enable_dpi_awareness()
        out = settings.STORAGE_DIR / "probe"
        out.mkdir(parents=True, exist_ok=True)

        main = Desktop(backend="uia").window(auto_id="frmMDIMain", title_re=r".*MESClient.*", found_index=0)
        if not main.exists(timeout=3):
            raise CommandError("Không thấy MESClient")
        # Có thể có nhiều cửa sổ trùng tên (form mở 2 lần, hoặc khung MDI bọc ngoài) -> lấy cái đầu tiên
        matches = [w for w in main.wrapper_object().descendants(control_type="Window") if w.window_text() == title]
        self.stdout.write(f"Tìm thấy {len(matches)} cửa sổ tên '{title}'")
        form = main.child_window(title=title, control_type="Window", found_index=0)
        if not form.exists(timeout=2):
            names = [w.window_text() for w in main.wrapper_object().descendants(control_type="Window")]
            raise CommandError(f"Không thấy form '{title}'. Các form đang mở: {names}")
        fw = form.wrapper_object()
        report = {"title": title, "form_auto_id": fw.element_info.automation_id, "controls": [],
                  "same_title_windows": [{"auto_id": m.element_info.automation_id, "class": m.element_info.class_name,
                                          "rect": str(m.element_info.rectangle)} for m in matches]}

        # 1. Cây UI Automation của form
        lines = []

        def dump(w, depth=0):
            ei = w.element_info
            r = ei.rectangle
            lines.append("  " * depth + f"[{ei.control_type}] class='{ei.class_name}' name='{(ei.name or '')[:60]}'"
                         f" auto_id='{ei.automation_id}' rect=({r.left},{r.top},{r.right},{r.bottom})")
            if ei.automation_id and ei.control_type in ("Button", "Edit", "Pane", "ComboBox", "Table", "DataGrid"):
                report["controls"].append({"type": ei.control_type, "auto_id": ei.automation_id,
                                           "name": ei.name, "class": ei.class_name})
            try:
                kids = w.children()
            except Exception:
                return
            for i, k in enumerate(kids):
                if i >= MAX_CHILDREN:
                    lines.append("  " * (depth + 1) + f"... +{len(kids) - i}")
                    break
                dump(k, depth + 1)

        dump(fw)
        (out / f"uia_{report['form_auto_id']}.txt").write_text("\n".join(lines), encoding="utf-8")

        # 2. Class Win32 (để nhận ra lưới FarPoint, DateTimePicker...)
        classes = []
        win32gui.EnumChildWindows(fw.handle, lambda h, _: classes.append(
            f"{win32gui.GetClassName(h)} '{win32gui.GetWindowText(h)[:40]}'"), None)
        report["win32_classes"] = sorted(set(classes))

        # 3. Kiểm tra đường dẫn menu
        try:
            inquiry = main.child_window(title="Inquiry", control_type="MenuItem", found_index=0)
            report["inquiry_menu"] = [c.window_text() for c in inquiry.wrapper_object().children()]
        except Exception as e:
            report["inquiry_menu_error"] = str(e)

        # 4. Thử bấm Excel và đọc dữ liệu
        if export:
            report["export"] = self._try_export(form, title)

        (out / "report_form.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                              encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Xong. Xem {out}"))

    def _try_export(self, form, title):
        import pythoncom
        import pywintypes
        import win32com.client

        from apps.collector import winutil
        from apps.collector.base import SPREADSHEET_PROGIDS

        pythoncom.CoInitialize()

        def books():
            res = {}
            for pid in SPREADSHEET_PROGIDS:
                try:
                    app = win32com.client.GetActiveObject(pid)
                    for i in range(1, app.Workbooks.Count + 1):
                        wb = app.Workbooks.Item(i)
                        res[wb.FullName] = wb
                except pywintypes.com_error:
                    pass
            return res

        btn = form.child_window(auto_id="btnExcel")
        if not btn.exists(timeout=1):
            return {"error": "Không thấy nút btnExcel trong form"}
        before = set(books())
        winutil.post_click(btn.wrapper_object().handle)
        self.stdout.write("Đã bấm Excel, chờ tối đa 60s...")
        for _ in range(60):
            time.sleep(1)
            new = {k: v for k, v in books().items() if k not in before}
            if new:
                time.sleep(3)   # chờ MES ghi xong
                name, wb = next(iter(new.items()))
                ws = wb.Worksheets.Item(1)
                ur = ws.UsedRange
                top = ws.Range(ws.Cells(1, 1), ws.Cells(min(15, ur.Rows.Count), ur.Column + ur.Columns.Count - 1)).Value
                return {"workbook": name, "rows": ur.Rows.Count, "cols": ur.Columns.Count,
                        "top": [[None if c is None else str(c) for c in r] for r in top]}
        return {"error": "Không thấy workbook mới trong 60s"}
