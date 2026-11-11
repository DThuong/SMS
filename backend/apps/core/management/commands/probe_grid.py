"""
Dò xem đọc được lưới dữ liệu FarPoint của MES (spdData_top) KHÔNG qua clipboard/Excel hay không.
Cách dùng: mở MES → View Work Order → chọn Plant/Period/Oper → bấm View cho lưới có dữ liệu, rồi chạy
    python manage.py probe_grid                      # form mặc định frmCWIPViewWorkOrderList, lưới spdData_top
    python manage.py probe_grid --grid spdData_top --form frmCWIPViewWorkOrderList
Thử 3 cách, KHÔNG bấm Excel, KHÔNG dùng chuột/clipboard:
  1. UI Automation  (Grid/Table pattern, phần tử con, LegacyIAccessible)
  2. MSAA / IAccessible (oleacc) — nhiều lưới FarPoint chỉ hỗ trợ kiểu này
  3. Ô sửa F2: đứng ô (1,1) → F2 → đọc chữ trong ô sửa → Esc → sang phải (như cách đặt FROM_DATE ở sản lượng)
Kết quả in ra màn hình + ghi storage/probe/grid_report.txt — gửi file này cho người phát triển.
"""
import ctypes
import time

import win32con
import win32gui
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _children(h):
    res = []
    try:
        win32gui.EnumChildWindows(h, lambda c, _: res.append(c), None)
    except Exception:
        pass
    return res


class Command(BaseCommand):
    help = "Dò cách đọc lưới FarPoint của MES không qua clipboard"

    def add_arguments(self, parser):
        parser.add_argument("--form", default="frmCWIPViewWorkOrderList")
        parser.add_argument("--grid", default="spdData_top")
        parser.add_argument("--cells", type=int, default=6, help="Số ô thử đọc bằng F2")

    def handle(self, *args, form, grid, cells, **opts):
        import logging

        from apps.collector import winutil
        from apps.collector.mes_client import MesClient

        winutil.enable_dpi_awareness()
        out = settings.STORAGE_DIR / "probe"
        out.mkdir(parents=True, exist_ok=True)
        lines = []

        def say(msg=""):
            lines.append(str(msg))
            self.stdout.write(str(msg))

        mes = MesClient(logging.getLogger("probe_grid"), True)
        mes.connect(auto_start=False)
        f = mes.find_form(form)
        if f is None:
            raise CommandError(f"Không thấy form {form} đang mở trên MES")
        try:
            g = f.child_window(auto_id=grid).wrapper_object()
        except Exception:
            raise CommandError(f"Không thấy lưới {grid} trong form {form}")
        h = g.handle
        say(f"Lưới {grid}: hwnd={h} class={win32gui.GetClassName(h)} rect={g.rectangle()}")

        # ---------- 1. UI Automation ----------
        say("\n=== 1. UI Automation ===")
        ei = g.element_info
        say(f"control_type={ei.control_type} name='{(ei.name or '')[:80]}'")
        for iface in ("iface_grid", "iface_table", "iface_value", "iface_legacy_iaccessible"):
            try:
                obj = getattr(g, iface)
                if iface == "iface_grid":
                    say(f"GridPattern: {obj.CurrentRowCount} dòng x {obj.CurrentColumnCount} cột")
                    for c in range(min(5, obj.CurrentColumnCount)):
                        try:
                            it = obj.GetItem(0, c)
                            say(f"  ô(0,{c}) name='{it.CurrentName}'")
                        except Exception as e:
                            say(f"  ô(0,{c}) lỗi {e}")
                elif iface == "iface_value":
                    say(f"ValuePattern: '{str(obj.CurrentValue)[:200]}'")
                elif iface == "iface_legacy_iaccessible":
                    say(f"Legacy: name='{obj.CurrentName}' value='{str(obj.CurrentValue)[:200]}' "
                        f"role={obj.CurrentRole}")
                else:
                    say(f"{iface}: có")
            except Exception as e:
                say(f"{iface}: không ({type(e).__name__})")
        try:
            desc = g.descendants()
            types = {}
            for d in desc:
                types[d.element_info.control_type] = types.get(d.element_info.control_type, 0) + 1
            say(f"Phần tử con UIA: {len(desc)} — {types}")
            for d in desc[:15]:
                say(f"  [{d.element_info.control_type}] name='{(d.element_info.name or '')[:60]}' "
                    f"auto_id='{d.element_info.automation_id}'")
        except Exception as e:
            say(f"descendants lỗi: {e}")

        # ---------- 2. MSAA ----------
        say("\n=== 2. MSAA / IAccessible ===")
        try:
            import comtypes.client
            from comtypes.automation import VARIANT
            comtypes.client.GetModule("oleacc.dll")
            from comtypes.gen.Accessibility import IAccessible
            acc = ctypes.POINTER(IAccessible)()
            ctypes.oledll.oleacc.AccessibleObjectFromWindow(h, -4, ctypes.byref(IAccessible._iid_), ctypes.byref(acc))
            n = acc.accChildCount
            say(f"accChildCount = {n}; name='{acc.accName(0)}' value='{acc.accValue(0)}'")
            arr = (VARIANT * min(n, 40))()
            got = ctypes.c_long()
            ctypes.oledll.oleacc.AccessibleChildren(acc, 0, min(n, 40), arr, ctypes.byref(got))
            for i in range(got.value):
                v = arr[i].value
                try:
                    if isinstance(v, int):
                        say(f"  con {v}: name='{acc.accName(v)}' value='{acc.accValue(v)}'")
                    else:
                        ch = v.QueryInterface(IAccessible)
                        say(f"  con obj: name='{ch.accName(0)}' value='{ch.accValue(0)}' con={ch.accChildCount}")
                except Exception as e:
                    say(f"  con {i}: lỗi {e}")
        except Exception as e:
            say(f"MSAA lỗi: {type(e).__name__}: {e}")

        # ---------- 3. F2 editor ----------
        say("\n=== 3. Ô sửa F2 (không clipboard) ===")
        winutil.post_keys(h, [win32con.VK_ESCAPE])
        time.sleep(0.3)
        winutil.focus_in_thread(h)
        winutil.post_keys(h, [win32con.VK_HOME] + [win32con.VK_UP] * 3)   # về ô đầu (dòng dữ liệu 1)
        time.sleep(0.4)
        for i in range(cells):
            before = {c for c in _children(h) if win32gui.IsWindowVisible(c)}
            winutil.post_keys(h, [win32con.VK_F2])
            time.sleep(0.6)
            eds = [c for c in _children(h) if win32gui.IsWindowVisible(c) and c not in before]
            if not eds:
                say(f"  ô {i + 1}: F2 KHÔNG mở ô sửa (ô bị khóa chỉ-đọc?)")
            else:
                txt = []
                for e in eds + [c for e in eds for c in _children(e)]:
                    txt.append(f"{win32gui.GetClassName(e)}='{winutil.get_text(e)}'")
                say(f"  ô {i + 1}: " + "; ".join(txt))
            winutil.post_keys(h, [win32con.VK_ESCAPE])
            time.sleep(0.3)
            winutil.post_keys(h, [win32con.VK_RIGHT])
            time.sleep(0.3)

        (out / "grid_report.txt").write_text("\n".join(lines), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"\nĐã ghi {out / 'grid_report.txt'}"))
