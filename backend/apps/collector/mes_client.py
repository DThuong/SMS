"""
Điều khiển MESClient qua UI Automation (pywinauto) + Win32 message — không chiếm chuột.
Tự mở MESClient, tự đăng nhập khi bị đăng xuất, tự bấm "No" khi MES hỏi nâng cấp phiên bản.
"""
import ctypes
import logging
import subprocess
import time
from pathlib import Path

import psutil
import win32con
import win32gui
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError

from . import winutil
from .errors import MesError

MAIN_TITLE_RE = r".*MESClient.*"
# Thông báo dạng "đã xong" của MES → tự bấm OK
INFO_WORDS = ("complete", "success", "saved", "export", "완료", "성공", "저장")


def _mes_pid() -> int | None:
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() == "mesclient.exe":
            return p.pid
    return None


class MesClient:
    def __init__(self, log: logging.Logger, auto_dismiss_upgrade: bool = True):
        self.log = log
        self.auto_dismiss_upgrade = auto_dismiss_upgrade
        self.pid = None
        self.main = None
        self.hwnd = None

    # ================= Kết nối / tự mở =================
    def _attach(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            pid = _mes_pid()
            if pid:
                spec = Desktop(backend="uia").window(process=pid, title_re=MAIN_TITLE_RE,
                                                     control_type="Window", found_index=0)
                if spec.exists(timeout=1):
                    self.pid, self.main = pid, spec
                    self.hwnd = spec.wrapper_object().handle
                    return True
            time.sleep(1)
        return False

    def connect(self, exe_path: str = "", auto_start: bool = True, start_timeout: int = 90):
        if self._attach(timeout=3):
            self.log.info("Đã kết nối MESClient: %s", self.main.window_text())
            return
        if not auto_start:
            raise MesError("MESClient chưa mở trên máy agent", "MES_NOT_RUNNING")
        exe = Path(exe_path or "")
        if not exe_path or not exe.is_file():
            raise MesError(f"MESClient chưa mở và không thấy file chạy tại '{exe_path}'", "MES_NOT_RUNNING")
        self.log.info("MESClient chưa mở → đang tự mở %s", exe)
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
        except OSError as e:
            raise MesError(f"Không chạy được {exe}: {e}", "MES_START_FAILED")
        if not self._attach(timeout=start_timeout):
            raise MesError(f"Đã chạy MESClient nhưng sau {start_timeout}s chưa thấy cửa sổ", "MES_START_FAILED")
        self.log.info("Đã mở MESClient")
        time.sleep(2)

    # ================= Popup =================
    def _message_boxes(self):
        """Hộp thoại frmMessageBox của MES (có thể là cửa sổ con hoặc cửa sổ riêng)"""
        boxes = []
        try:
            for w in Desktop(backend="uia").windows(process=self.pid):
                if w.element_info.automation_id == "frmMessageBox":
                    boxes.append(w)
                boxes += [c for c in w.descendants(control_type="Window")
                          if c.element_info.automation_id == "frmMessageBox"]
        except Exception:
            pass
        return boxes

    def _dismiss_dotnet_error(self) -> int:
        """
        Hộp thoại "Microsoft .NET Framework — Unhandled exception..." của MESClient → bấm Continue
        (bấm Quit sẽ tắt MES). Ghi log nội dung lỗi để theo dõi.
        """
        closed = 0
        try:
            wins = Desktop(backend="win32").windows(process=self.pid, visible_only=True)
        except Exception:
            return 0
        for w in wins:
            try:
                if ".NET Framework" not in w.window_text():
                    continue
                texts = [c.window_text().strip() for c in w.descendants() if c.window_text().strip()]
                btn = [c for c in w.descendants(class_name_re=r".*BUTTON.*")
                       if c.window_text().strip().lower() == "continue"]
                if btn:
                    winutil.post_click(btn[0].handle)
                    closed += 1
                    self.log.warning("MESClient báo lỗi nội bộ .NET → đã bấm Continue: %s", " | ".join(texts)[:300])
                    time.sleep(1)
            except Exception:
                pass
        return closed

    def handle_popup(self) -> int:
        """
        Gọi thường xuyên: hộp thoại hỏi nâng cấp → bấm No (MES hỏi lại nhiều lần trong lúc dùng);
        lỗi nội bộ .NET của MES → bấm Continue; thông báo "xong" → OK. Hộp thoại khác → báo lỗi kèm nội dung.
        Trả về số hộp thoại đã đóng.
        """
        closed = self._dismiss_dotnet_error()
        for _ in range(5):                       # đóng liên tiếp nếu hiện nhiều cái
            boxes = self._message_boxes()
            if not boxes:
                return closed
            box = boxes[0]
            msg = ""
            for c in box.descendants(control_type="Text"):
                if c.window_text().strip():
                    msg = c.window_text().strip()
                    break
            if self.auto_dismiss_upgrade and "upgrade" in msg.lower():
                no = [b for b in box.descendants(control_type="Button")
                      if b.element_info.automation_id == "btnYesNoNo" or b.window_text().strip().lower() == "no"]
                if not no:
                    raise MesError(f"Hộp thoại nâng cấp không có nút No: {msg}", "MES_POPUP_BLOCKING")
                winutil.post_click(no[0].handle)
                time.sleep(0.8)
                closed += 1
                self.log.info("Đã đóng hộp thoại hỏi nâng cấp (chọn No)")
                continue
            if any(w in msg.lower() for w in INFO_WORDS):
                ok = [b for b in box.descendants(control_type="Button")
                      if b.window_text().strip().lower() in ("ok", "yes", "확인")]
                if ok:
                    winutil.post_click(ok[0].handle)
                    time.sleep(0.8)
                    closed += 1
                    self.log.info("Đã đóng thông báo của MES: %s", msg[:120])
                    continue
            raise MesError(f"Có hộp thoại đang chặn MES: {msg or '(không đọc được nội dung)'}", "MES_POPUP_BLOCKING")
        return closed

    def wait(self, seconds: float):
        """Chờ nhưng vẫn canh hộp thoại nâng cấp mỗi giây"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.handle_popup()
            time.sleep(min(1.0, max(0.0, deadline - time.time())))

    # ================= Đăng nhập =================
    def _password_edits(self):
        """Ô mật khẩu đang hiện (style ES_PASSWORD) = MES đang ở màn hình đăng nhập"""
        res = []
        for top in Desktop(backend="win32").windows(process=self.pid, visible_only=True):
            for c in [top] + top.descendants(class_name_re=r".*EDIT.*"):
                try:
                    style = win32gui.GetWindowLong(c.handle, win32con.GWL_STYLE)
                    if style & win32con.ES_PASSWORD and c.is_visible():
                        res.append(c)
                except Exception:
                    pass
        return res

    def is_login_screen(self) -> bool:
        return bool(self._password_edits())

    def ensure_logged_in(self, user_id: str, password: str, timeout: int = 60) -> bool:
        """Đang ở màn hình đăng nhập → tự điền User ID + Password, bấm OK. Trả về True nếu vừa đăng nhập."""
        pw_edits = self._password_edits()
        if not pw_edits:
            return False
        self.log.info("MES đang ở màn hình đăng nhập → tự đăng nhập")
        if not user_id or not password:
            raise MesError("MES đã đăng xuất nhưng chưa cấu hình mes_user_id / mes_password", "MES_LOGIN_REQUIRED")

        pw = pw_edits[0]
        # Tìm khung đăng nhập chứa nút OK (đi ngược lên các cửa sổ cha)
        box, ok_btn = pw, None
        for _ in range(8):
            box = box.parent()
            if box is None:
                break
            ok = [b for b in box.descendants(class_name_re=r".*BUTTON.*") if b.window_text().strip().upper() == "OK"]
            if ok:
                ok_btn = ok[0]
                break
        if ok_btn is None:
            raise MesError("Không tìm thấy nút OK ở màn hình đăng nhập", "MES_LOGIN_FAILED")

        # Ô User ID = ô nhập ngay phía trên ô Password
        edits = sorted([e for e in box.descendants(class_name_re=r".*EDIT.*") if e.is_visible()],
                       key=lambda e: (e.rectangle().top, e.rectangle().left))
        idx = next((i for i, e in enumerate(edits) if e.handle == pw.handle), -1)
        if idx <= 0:
            raise MesError("Không tìm thấy ô User ID ở màn hình đăng nhập", "MES_LOGIN_FAILED")
        winutil.set_text(edits[idx - 1].handle, user_id)
        winutil.set_text(pw.handle, password)
        winutil.post_click(ok_btn.handle)
        self.log.info("Đã điền User ID = %s và bấm OK", user_id)

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1)
            try:
                self.handle_popup()               # hộp thoại nâng cấp hiện ngay sau khi đăng nhập
            except MesError as e:
                raise MesError(f"Đăng nhập MES thất bại: {e}", "MES_LOGIN_FAILED")
            if not self._password_edits():
                self._attach(timeout=10)          # tiêu đề cửa sổ đổi sau khi đăng nhập
                self.log.info("Đăng nhập MES thành công: %s", self.main.window_text() if self.main else "")
                return True
        raise MesError(f"Đã bấm OK nhưng sau {timeout}s vẫn ở màn hình đăng nhập", "MES_LOGIN_FAILED")

    # ================= Menu & form =================
    def open_form(self, form_id: str, menu_path: list, timeout: int = 20, by: str = "auto_id"):
        """by="auto_id": tìm form theo AutomationId; by="title": theo tiêu đề cửa sổ"""
        self.handle_popup()
        form = self.find_form(form_id, by)
        if form is not None:
            self.log.info("Màn hình '%s' đã mở sẵn", form_id)
            self.maximize(form)
            return form

        self.log.info("Mở menu: %s", " → ".join(menu_path))
        if self._click_menu(menu_path, direct=True):
            # Invoke thẳng có lúc "thành công" nhưng MES không mở màn hình → thử lại kiểu mở từng cấp
            for _ in range(6):
                time.sleep(1)
                self.handle_popup()
                form = self.find_form(form_id, by)
                if form is not None:
                    self.log.info("Đã mở màn hình '%s'", form_id)
                    self.maximize(form)
                    return form
            self.log.info("Chọn menu trực tiếp chưa mở được màn hình → mở từng cấp menu")
        self._click_menu(menu_path, direct=False)

        deadline = time.time() + timeout
        while time.time() < deadline:
            self.handle_popup()
            form = self.find_form(form_id, by)
            if form is not None:
                self.log.info("Đã mở màn hình '%s'", form_id)
                self.maximize(form)
                return form
            time.sleep(1)
        raise MesError(f"Đã chọn menu nhưng không thấy màn hình '{form_id}'", "MES_FORM_NOT_FOUND")

    # ---------- menu ----------
    def _menu_items(self, name: str):
        """Mọi mục menu tên `name` trong mọi cửa sổ của MESClient (menu xổ ra là cửa sổ riêng)"""
        found = []
        for top in Desktop(backend="uia").windows(process=self.pid):
            try:
                found += [m for m in top.descendants(control_type="MenuItem") if m.window_text().strip() == name]
            except Exception:
                pass
        return found

    def _click_menu(self, path: list, direct: bool) -> bool:
        leaf = path[-1]
        if direct:
            # Cách 1: Invoke thẳng mục lá (WinForms cho phép kể cả khi menu chưa xổ)
            for item in self._menu_items(leaf):
                try:
                    item.invoke()
                    self.log.info("Chọn menu '%s'", leaf)
                    return True
                except Exception as e:
                    self.log.info("Invoke '%s' chưa được (%s)", leaf, e)
            return False
        # Cách 2: mở từng cấp menu rồi chọn mục lá
        for name in path[:-1]:
            items = self._menu_items(name)
            if not items:
                raise MesError(f"Không tìm thấy menu '{name}' ({' → '.join(path)})", "MES_MENU_NOT_FOUND")
            try:
                items[0].expand()
            except Exception:
                try:
                    items[0].invoke()
                except Exception as e:
                    raise MesError(f"Không mở được menu '{name}': {e}", "MES_MENU_NOT_FOUND")
            time.sleep(0.6)
        for _ in range(5):
            items = self._menu_items(leaf)
            if items:
                try:
                    items[0].invoke()
                except Exception:
                    items[0].select()
                self.log.info("Chọn menu '%s' (mở từng cấp)", leaf)
                return True
            time.sleep(0.5)
        # Đóng menu đang xổ (Esc) để không chặn giao diện
        winutil.post_keys(self.hwnd, [win32con.VK_ESCAPE] * 3)
        raise MesError(f"Không tìm thấy mục menu '{leaf}' ({' → '.join(path)})", "MES_MENU_NOT_FOUND")

    def find_form(self, form_id: str, by: str = "auto_id"):
        """
        Form MDI đang HIỆN và LỚN NHẤT trùng tên/AutomationId.
        (Sau khi đăng nhập MES hay có form ở dạng cửa sổ thu nhỏ, hoặc 2 bản trùng tên.)
        Trả về WindowSpecification theo handle, hoặc None.
        """
        try:
            wins = self.main.wrapper_object().descendants(control_type="Window")
        except Exception:
            return None
        key = (lambda w: w.window_text() == form_id) if by == "title" else \
              (lambda w: w.element_info.automation_id == form_id)
        found = [w for w in wins if key(w) and w.is_visible()]
        if not found:
            return None
        best = max(found, key=lambda w: w.rectangle().width() * w.rectangle().height())
        return self.main.child_window(handle=best.handle)

    def maximize(self, form):
        """Phóng to form MDI bằng message (không dùng chuột) để giao diện ổn định, ảnh chụp lỗi đầy đủ"""
        try:
            h = form.wrapper_object().handle
            if not ctypes.windll.user32.IsZoomed(h):
                win32gui.PostMessage(h, win32con.WM_SYSCOMMAND, win32con.SC_MAXIMIZE, 0)
                time.sleep(0.8)
                self.log.info("Đã phóng to màn hình (đang ở dạng cửa sổ thu nhỏ)")
        except Exception as e:
            self.log.info("Không phóng to được màn hình: %s", e)

    def close_form(self, form_id: str, by: str = "auto_id", timeout: int = 10):
        """Đóng form đang mở (để mở lại với điều kiện mặc định)"""
        closed = 0
        while closed < 5:
            spec = self.find_form(form_id, by)
            if spec is None:
                break
            h = spec.wrapper_object().handle
            try:
                btn = spec.child_window(auto_id="btnClose").wrapper_object()
                winutil.post_click(btn.handle)
            except ElementNotFoundError:
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)    # form không có nút Close
            closed += 1
            deadline = time.time() + timeout
            while time.time() < deadline and win32gui.IsWindow(h) and win32gui.IsWindowVisible(h):
                self.handle_popup()
                time.sleep(0.5)
        if closed:
            self.log.info("Đã đóng %d màn hình '%s' đang mở", closed, form_id)

    # ================= Hộp thoại Save As (#32770) =================
    def dialogs(self) -> set:
        return {w.handle for w in Desktop(backend="win32").windows(process=self.pid, visible_only=True)
                if w.class_name() == "#32770"}

    def wait_save_dialog(self, before: set, timeout: int = 30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            new = self.dialogs() - before
            if new:
                return Desktop(backend="win32").window(handle=new.pop())
            self.handle_popup()
            time.sleep(0.5)
        raise MesError(f"Đã bấm nút Excel nhưng sau {timeout}s không thấy hộp thoại lưu file", "MES_SAVE_DIALOG_NOT_FOUND")

    @staticmethod
    def _save_dialog_controls(dlg_h: int) -> tuple[int, int]:
        """
        Tìm ô tên file + nút Save trong hộp thoại Save As chuẩn của Windows theo MÃ CỐ ĐỊNH:
          ô nhập tên file = Edit id 1001 (nằm trong ComboBox id 1148), nút Save = Button id 1.
        (Hộp thoại kiểu mới có 2 ComboBoxEx32 — thanh địa chỉ và ô File name — nên không tìm theo thứ tự được.)
        """
        kids = []
        win32gui.EnumChildWindows(dlg_h, lambda c, _: kids.append(c), None)

        def cls(h):
            return win32gui.GetClassName(h)

        def cid(h):
            try:
                return win32gui.GetDlgCtrlID(h)
            except Exception:
                return -1

        edits = [h for h in kids if cls(h) == "Edit"]
        edit = next((h for h in edits if cid(h) == 1001), None)
        if edit is None:   # ô Edit nằm trong combo File name (id 1148)
            combo = next((h for h in kids if cid(h) == 1148), None)
            if combo:
                inner = []
                win32gui.EnumChildWindows(combo, lambda c, _: inner.append(c), None)
                edit = next((h for h in inner if cls(h) == "Edit"), None)
        if edit is None:   # dự phòng: ô Edit đang hiện nằm thấp nhất (ô File name ở dưới thanh địa chỉ)
            vis = [h for h in edits if win32gui.IsWindowVisible(h)]
            edit = max(vis, key=lambda h: win32gui.GetWindowRect(h)[1]) if vis else None
        save = next((h for h in kids if cls(h) == "Button" and cid(h) == 1), None)
        if not edit or not save:
            found = [f"{cls(h)}#{cid(h)}" for h in kids][:60]
            raise MesError(f"Không tìm thấy ô tên file / nút Save trong hộp thoại lưu. Các control: {found}",
                           "MES_SAVE_DIALOG_FAILED")
        return edit, save

    def save_via_dialog(self, dlg, target: Path, timeout: int = 90) -> Path:
        """
        Điền đường dẫn đầy đủ (không đuôi) vào ô File name, bấm Save.
        Hộp thoại tự thêm đuôi theo loại file của MES (.xls/.xlsx) → tìm file theo tên gốc.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        edit_h, save_h = self._save_dialog_controls(dlg.wrapper_object().handle)
        winutil.set_text(edit_h, str(target))
        winutil.post_click(save_h)
        self.log.info("Lưu file vào: %s.*", target)

        deadline, last_size, stable = time.time() + timeout, -1, 0
        while time.time() < deadline:
            time.sleep(1)
            self.handle_popup()
            # Hộp thoại báo lỗi (đường dẫn sai, file đang mở...) của Windows
            for h in self.dialogs():
                w = Desktop(backend="win32").window(handle=h)
                if h != dlg.handle and w.exists(timeout=0):
                    texts = [c.window_text() for c in w.wrapper_object().descendants() if c.window_text().strip()]
                    raise MesError(f"Windows báo khi lưu file: {' | '.join(texts)[:300]}", "MES_SAVE_DIALOG_FAILED")
            files = [f for f in target.parent.glob(target.name + "*") if f.is_file()]
            if files:
                f = max(files, key=lambda x: x.stat().st_mtime)
                size = f.stat().st_size
                stable = stable + 1 if size == last_size and size > 0 else 0
                last_size = size
                if stable >= 2:
                    self.log.info("MES đã ghi xong file %s (%d byte)", f.name, size)
                    return f
        raise MesError(f"Sau {timeout}s chưa thấy file được lưu ở {target.parent}", "MES_SAVE_DIALOG_FAILED")

    # ================= Thao tác trên form =================
    def _control(self, form, what: str, **criteria):
        try:
            return form.child_window(**criteria).wrapper_object()
        except ElementNotFoundError:
            raise MesError(f"Không tìm thấy {what} trên màn hình MES", "MES_CONTROL_NOT_FOUND")

    def set_code_view(self, form, cdv_auto_id: str, value: str):
        """Ô dạng [mã][...][mô tả] của Miracom: điền mã vào txtDisplay"""
        cdv = form.child_window(auto_id=cdv_auto_id)
        edit = self._control(cdv, f"ô {cdv_auto_id}", auto_id="txtDisplay")
        winutil.set_text(edit.handle, value)
        actual = (edit.get_value() or "").strip()
        if actual != value:
            raise MesError(f"Điền {cdv_auto_id}='{value}' thất bại (đang là '{actual}')", "MES_CONTROL_NOT_FOUND")
        self.log.info("Đặt %s = %s", cdv_auto_id, value)

    def click(self, form, auto_id: str):
        self.handle_popup()
        btn = self._control(form, f"nút {auto_id}", auto_id=auto_id)
        if not btn.is_enabled():
            raise MesError(f"Nút {auto_id} đang bị khóa", "MES_CONTROL_NOT_FOUND")
        winutil.post_click(btn.handle)
        self.log.info("Bấm %s", auto_id)

    def click_named(self, form, name: str):
        """Bấm nút theo chữ hiển thị (khi chưa biết AutomationId)"""
        self.handle_popup()
        btn = self._control(form, f"nút '{name}'", title=name, control_type="Button", found_index=0)
        if not btn.is_enabled():
            raise MesError(f"Nút '{name}' đang bị khóa", "MES_CONTROL_NOT_FOUND")
        winutil.post_click(btn.handle)
        self.log.info("Bấm nút '%s'", name)

    def read_period(self, form) -> str:
        try:
            f = form.child_window(auto_id="dtpFromDate").window_text()
            t = form.child_window(auto_id="dtpToDate").window_text()
            return f"{f} ~ {t}"
        except Exception:
            return "(không đọc được)"

    def screenshot(self, path: Path):
        if self.hwnd:
            winutil.capture_window(self.hwnd, path)
