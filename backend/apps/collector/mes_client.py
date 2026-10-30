"""
Điều khiển MESClient qua UI Automation (pywinauto) + Win32 message — không chiếm chuột.
Tự mở MESClient, tự đăng nhập khi bị đăng xuất, tự bấm "No" khi MES hỏi nâng cấp phiên bản.
"""
import ctypes
import logging
import re
import shutil
import subprocess
import time
import unicodedata
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
# Thông báo "không có dữ liệu" khi View/Excel → bấm OK, ghi nhận để collector trả 0 dòng (không phải lỗi)
NO_DATA_WORDS = ("no data", "no record", "data not found", "no rows", "데이터가 없", "자료가 없", "조회 결과가 없",
                 "không có dữ liệu")
# Hộp thoại "Confirm Save As — ... already exists. Do you want to replace it?" của Windows
OVERWRITE_WORDS = ("already exists", "replace it", "이미 있", "바꾸시겠", "đã tồn tại", "thay thế")
# Nút đóng hộp thoại an toàn (KHÔNG BAO GIỜ bấm Yes / ghi đè): id chuẩn IDNO = 7, IDCANCEL = 2
SAFE_BUTTON_IDS = (7, 2)
SAFE_BUTTON_TEXTS = ("no", "&no", "cancel", "아니요", "아니요(&n)", "취소", "không", "hủy")
TMP_PREFIX = ".dang_luu_"      # thư mục tạm (rỗng) để hộp thoại Save As lưu vào, lưu xong chuyển file ra ngoài


def _norm_menu(text: str) -> str:
    """Chuẩn hóa tên menu: NFKC (ngoặc full-width → thường), bỏ '&' phím tắt, gộp khoảng trắng, chữ thường"""
    t = unicodedata.normalize("NFKC", text or "").replace("&", "")
    t = t.split("\t")[0]                       # bỏ phần phím tắt "Ctrl+X" phía sau tab
    return re.sub(r"\s+", " ", t).strip().casefold()


def _strip_prefix(text: str) -> str:
    """Bỏ tiền tố dạng "[PM]" ở đầu tên menu"""
    return re.sub(r"^\[[^\]]*\]\s*", "", text)


def _mes_procs() -> list:
    """Mọi tiến trình MESClient.exe đang chạy (có thể nhiều hơn 1: bản cập nhật, mở 2 lần...)"""
    return [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "mesclient.exe"]


def _top_windows() -> list[tuple[int, int, str, int]]:
    """Cửa sổ cấp cao đang hiện: (hwnd, pid, tiêu đề, diện tích)"""
    import win32process

    res = []

    def visit(h, _):
        try:
            if win32gui.IsWindowVisible(h):
                title = win32gui.GetWindowText(h)
                if title:
                    _, pid = win32process.GetWindowThreadProcessId(h)
                    l, t, r, b = win32gui.GetWindowRect(h)
                    res.append((h, pid, title, (r - l) * (b - t)))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(visit, None)
    return res


def _main_window_win32(pids: set) -> tuple[int, int] | None:
    """
    Cửa sổ chính MESClient (tiêu đề chứa 'MESClient') bằng Win32 — nhanh, không phụ thuộc UIA khi MES đang bận.
    Ưu tiên cửa sổ của tiến trình MESClient.exe; không có thì nhận cửa sổ của BẤT KỲ tiến trình nào có tiêu đề đó
    (MES chạy qua launcher / tên tiến trình khác).
    """
    wins = [w for w in _top_windows() if re.match(MAIN_TITLE_RE, w[2])]
    if not wins:
        return None
    own = [w for w in wins if w[1] in pids]
    if not own:
        # Tiến trình khác tên: chỉ nhận cửa sổ có tiêu đề BẮT ĐẦU bằng "MESClient" và không phải Explorer/VS Code...
        skip = {"explorer.exe", "code.exe", "powershell.exe", "cmd.exe", "windowsterminal.exe"}

        def name_of(pid):
            try:
                return psutil.Process(pid).name().lower()
            except Exception:
                return ""
        own = [w for w in wins if w[2].startswith("MESClient") and name_of(w[1]) not in skip]
    if not own:
        return None
    h, pid, _, _ = max(own, key=lambda w: w[3])
    return h, pid


def mes_diagnostics() -> str:
    """Tiến trình + cửa sổ có chữ 'mes' — ghi vào lỗi để biết MES có thật sự đang chạy không"""
    procs = sorted({f"{p.info['name']} (pid {p.pid})" for p in psutil.process_iter(["name"])
                    if "mes" in (p.info["name"] or "").lower()})
    wins = sorted({f"'{t[:60]}' (pid {pid})" for _, pid, t, _ in _top_windows() if "mes" in t.lower()})
    return f"Tiến trình có chữ 'mes': {procs or 'không có'}; cửa sổ có chữ 'mes': {wins or 'không có'}"


class MesClient:
    def __init__(self, log: logging.Logger, auto_dismiss_upgrade: bool = True):
        self.log = log
        self.auto_dismiss_upgrade = auto_dismiss_upgrade
        self.pid = None
        self.main = None
        self.hwnd = None
        self.exe_found = ""          # đường dẫn MESClient.exe thật của tiến trình đang chạy
        self._confirmed = {}         # ô mã đã chọn + xác nhận trong phiên: {"Plant": (mã yêu cầu, ô mã, ô mô tả)}
        self.no_data_message = ""    # MES báo "không có dữ liệu" (handle_popup ghi lại, collector đọc rồi xóa)

    # ================= Kết nối / tự mở =================
    def _attach(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while True:
            pids = {p.pid for p in _mes_procs()}
            hit = _main_window_win32(pids)
            if hit:
                h, pid = hit
                self.pid, self.hwnd = pid, h
                self.main = Desktop(backend="uia").window(handle=h)
                self.exe_found = self._exe_of(pid)
                return True
            if pids:
                # Dự phòng: tìm qua UI Automation
                for pid in pids:
                    spec = Desktop(backend="uia").window(process=pid, title_re=MAIN_TITLE_RE,
                                                         control_type="Window", found_index=0)
                    if spec.exists(timeout=1):
                        self.pid, self.main = pid, spec
                        self.hwnd = spec.wrapper_object().handle
                        self.exe_found = self._exe_of(pid)
                        return True
            if time.time() >= deadline:
                return False
            time.sleep(1)

    @staticmethod
    def _exe_of(pid: int) -> str:
        try:
            return psutil.Process(pid).exe()
        except Exception:
            return ""

    def connect(self, exe_path: str = "", auto_start: bool = True, start_timeout: int = 90):
        running = bool(_mes_procs()) or _main_window_win32(set()) is not None
        # MES đang chạy nhưng có thể bận (đang tải, đổi kích thước form...) → chờ lâu hơn
        if self._attach(timeout=20 if running else 3):
            self.log.info("Đã kết nối MESClient: %s", self.main.window_text())
            return
        if _mes_procs():
            raise MesError("MESClient đang chạy nhưng sau 20s không thấy cửa sổ chính "
                           "(MES đang treo/bận hoặc đang ở hộp thoại khác) — kiểm tra màn hình máy MES",
                           "MES_NOT_RUNNING")
        self.log.info("Không thấy cửa sổ MESClient. %s", mes_diagnostics())
        if not auto_start:
            raise MesError("MESClient chưa mở trên máy agent", "MES_NOT_RUNNING")
        exe = Path(exe_path or "")
        if not exe_path or not exe.is_file():
            raise MesError(f"MESClient chưa mở và không thấy file chạy tại '{exe_path}'. "
                           "Mở MESClient bằng tay một lần — agent sẽ tự ghi nhớ đường dẫn đúng", "MES_NOT_RUNNING")
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
            if any(w in msg.lower() for w in NO_DATA_WORDS):
                ok = [b for b in box.descendants(control_type="Button")
                      if b.window_text().strip().lower() in ("ok", "yes", "확인", "close")]
                if ok:
                    winutil.post_click(ok[0].handle)
                    time.sleep(0.8)
                    closed += 1
                    self.no_data_message = msg[:200]
                    self.log.info("MES báo không có dữ liệu: %s", msg[:120])
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

    def wait_idle(self, timeout: float = 300) -> float:
        """
        Chờ MESClient xử lý xong (luồng giao diện rảnh). MES truy vấn ngay trên luồng giao diện → trong lúc tải dữ liệu
        cửa sổ không nhận message; gửi WM_NULL tới khi được trả lời = MES đã tải xong. Trả về số giây đã chờ.
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                win32gui.SendMessageTimeout(self.hwnd, win32con.WM_NULL, 0, 0, win32con.SMTO_ABORTIFHUNG, 2000)
                return time.time() - start
            except Exception:
                time.sleep(0.5)
        raise MesError(f"MESClient bận (không phản hồi) quá {int(timeout)}s sau khi bấm View", "MES_BUSY_TIMEOUT")

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
            if top.class_name() == "#32770":
                continue          # hộp thoại Windows (Save As, Confirm...) không phải màn hình đăng nhập MES
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
    def _all_menu_items(self):
        items = []
        for top in Desktop(backend="uia").windows(process=self.pid):
            try:
                items += top.descendants(control_type="MenuItem")
            except Exception:
                pass
        return items

    def _menu_items(self, name: str):
        """
        Mọi mục menu tên `name` trong mọi cửa sổ của MESClient (menu xổ ra là cửa sổ riêng).
        So khớp linh hoạt theo thứ tự ưu tiên:
          1. trùng sau khi chuẩn hóa (bỏ '&', gộp khoảng trắng, NFKC cho ngoặc full-width, không phân biệt hoa thường)
          2. trùng sau khi bỏ tiền tố dạng "[PM]" (vd "[IQ]Inquiry" ↔ "Inquiry")
          3. tên menu chứa `name` (chọn tên ngắn nhất)
        """
        want = _norm_menu(name)
        items = []
        for m in self._all_menu_items():
            try:
                items.append((m, _norm_menu(m.window_text())))
            except Exception:
                pass
        exact = [m for m, t in items if t == want]
        if exact:
            return exact
        noprefix = [m for m, t in items if _strip_prefix(t) == _strip_prefix(want)]
        if noprefix:
            return noprefix
        contains = sorted([(len(t), m) for m, t in items if want and want in t], key=lambda x: x[0])
        return [m for _, m in contains]

    def menu_names(self, limit: int = 80) -> list[str]:
        """Tên các mục menu đang thấy (để ghi vào lỗi khi không tìm được menu)"""
        names = []
        for m in self._all_menu_items():
            try:
                t = m.window_text().strip()
            except Exception:
                continue
            if t and t not in names:
                names.append(t)
        return names[:limit]

    def _click_menu(self, path: list, direct: bool) -> bool:
        leaf = path[-1]
        if direct:
            # Cách 1: Invoke thẳng mục lá (WinForms cho phép kể cả khi menu chưa xổ)
            items = self._menu_items(leaf)
            if not items and self.restore_maximized_forms():
                items = self._menu_items(leaf)
            for item in items:
                try:
                    item.invoke()
                    self.log.info("Chọn menu '%s'", leaf)
                    return True
                except Exception as e:
                    self.log.info("Invoke '%s' chưa được (%s)", leaf, e)
            return False
        # Cách 2: mở từng cấp menu rồi chọn mục lá
        if not self._menu_items(path[0]) and self.restore_maximized_forms():
            self.handle_popup()
        for name in path[:-1]:
            items = self._menu_items(name)
            if not items:
                seen = self.menu_names()
                self.log.error("Các menu MES đang thấy: %s", seen)
                raise MesError(f"Không tìm thấy menu '{name}' ({' → '.join(path)}). Menu đang thấy: {seen}",
                               "MES_MENU_NOT_FOUND")
            if items[0].window_text().strip() != name:
                self.log.info("Menu '%s' khớp với '%s'", name, items[0].window_text().strip())
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
        seen = self.menu_names()
        self.log.error("Các menu MES đang thấy: %s", seen)
        # Đóng menu đang xổ (Esc) để không chặn giao diện
        winutil.post_keys(self.hwnd, [win32con.VK_ESCAPE] * 3)
        raise MesError(f"Không tìm thấy mục menu '{leaf}' ({' → '.join(path)}). Menu đang thấy: {seen}",
                       "MES_MENU_NOT_FOUND")

    def _child_hwnds(self) -> list[int]:
        """Mọi cửa sổ con (Win32) của MESClient — không phụ thuộc cây UI Automation"""
        res = []
        if not self.hwnd:
            return res
        try:
            win32gui.EnumChildWindows(self.hwnd, lambda h, _: res.append(h), None)
        except Exception:
            pass
        return res

    def _mdi_forms(self) -> list[int]:
        """Các form MDI (cửa sổ con trực tiếp của khung MDIClient)"""
        res = []
        for h in self._child_hwnds():
            try:
                parent = win32gui.GetParent(h)
                # Form MDI: có cờ WS_EX_MDICHILD, hoặc nằm trong khung MDIClient
                # (Win32 thuần: "MDIClient"; WinForms: "WindowsForms10.MDICLIENT.app.0.xxxx")
                ex = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
                if ex & 0x40 or (parent and "MDICLIENT" in win32gui.GetClassName(parent).upper()):   # 0x40 = WS_EX_MDICHILD
                    res.append(h)
            except Exception:
                pass
        return res

    def restore_maximized_forms(self) -> int:
        """
        Form con đang PHÓNG TO thì thanh menu của MES không lộ ra cho UI Automation (chỉ thấy 'System')
        → đưa form về kích thước thường (không đóng form) để chọn được menu.
        """
        n = 0
        for h in self._mdi_forms():
            if ctypes.windll.user32.IsZoomed(h):
                win32gui.PostMessage(h, win32con.WM_SYSCOMMAND, win32con.SC_RESTORE, 0)
                n += 1
        if n:
            time.sleep(1.2)
            self.log.info("Có form đang phóng to che menu → đã đưa %d form về kích thước thường", n)
        return n

    def find_form(self, form_id: str, by: str = "auto_id"):
        """
        Form MDI đang HIỆN và LỚN NHẤT trùng tên/AutomationId.
        (Sau khi đăng nhập MES hay có form ở dạng cửa sổ thu nhỏ, hoặc 2 bản trùng tên.)
        Theo tiêu đề: tìm bằng Win32 trước (đúng cả khi form đang phóng to — lúc đó UIA hay không thấy),
        rồi mới tới UI Automation. Trả về WindowSpecification theo handle, hoặc None.
        """
        if by == "title":
            want = _norm_menu(form_id)
            hs = []
            for h in self._child_hwnds():
                try:
                    if win32gui.IsWindowVisible(h) and _norm_menu(win32gui.GetWindowText(h)) == want:
                        hs.append(h)
                except Exception:
                    pass
            if hs:
                def area(h):
                    l, t, r, b = win32gui.GetWindowRect(h)
                    return (r - l) * (b - t)
                return Desktop(backend="uia").window(handle=max(hs, key=area))
        else:
            # Theo AutomationId: hỏi UIA trực tiếp từng form MDI theo handle (đúng cả khi form đang phóng to)
            hs = []
            for h in self._mdi_forms():
                try:
                    if win32gui.IsWindowVisible(h) and \
                            Desktop(backend="uia").window(handle=h).wrapper_object().element_info.automation_id == form_id:
                        hs.append(h)
                except Exception:
                    pass
            if hs:
                return Desktop(backend="uia").window(handle=hs[0])
        try:
            wins = self.main.wrapper_object().descendants(control_type="Window")
        except Exception:
            return None
        key = (lambda w: _norm_menu(w.window_text()) == _norm_menu(form_id)) if by == "title" else \
              (lambda w: w.element_info.automation_id == form_id)
        found = [w for w in wins if key(w) and w.is_visible()]
        if not found:
            return None
        best = max(found, key=lambda w: w.rectangle().width() * w.rectangle().height())
        return Desktop(backend="uia").window(handle=best.handle)

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

    # ---------- Dọn hộp thoại Windows còn sót ----------
    @staticmethod
    def _dialog_texts(h: int) -> str:
        kids = []
        try:
            win32gui.EnumChildWindows(h, lambda c, _: kids.append(c), None)
        except Exception:
            pass
        texts = [win32gui.GetWindowText(h)]
        for c in kids:
            try:
                if win32gui.GetClassName(c) in ("Static", "DirectUIHWND") or win32gui.GetClassName(c) == "Button":
                    texts.append(win32gui.GetWindowText(c))
            except Exception:
                pass
        return " | ".join(x.strip() for x in texts if x and x.strip())

    def _dismiss_dialog(self, h: int) -> bool:
        """Đóng 1 hộp thoại #32770 theo cách an toàn: nút No → Cancel → WM_COMMAND IDCANCEL → WM_CLOSE (không bấm Yes)"""
        kids = []
        try:
            win32gui.EnumChildWindows(h, lambda c, _: kids.append(c), None)
        except Exception:
            return False
        buttons = []
        for c in kids:
            try:
                if win32gui.GetClassName(c) == "Button" and win32gui.IsWindowVisible(c):
                    buttons.append((c, win32gui.GetDlgCtrlID(c), win32gui.GetWindowText(c).strip().lower()))
            except Exception:
                pass
        for want in SAFE_BUTTON_IDS:
            for c, cid, _ in buttons:
                if cid == want:
                    winutil.post_click(c)
                    return True
        for c, _, text in buttons:
            if text.replace("&", "") in [x.replace("&", "") for x in SAFE_BUTTON_TEXTS]:
                winutil.post_click(c)
                return True
        try:
            win32gui.PostMessage(h, win32con.WM_COMMAND, 2, 0)      # IDCANCEL
            win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            return True
        except Exception:
            return False

    def close_stray_dialogs(self, why: str = "") -> int:
        """
        Đóng các hộp thoại Windows (Save As, Confirm Save As, báo lỗi lưu file...) còn sót của MESClient.
        Hộp thoại sót lại khóa MES (modal) → lần chạy sau thao tác sai / tưởng MES đang ở màn hình đăng nhập.
        Đóng hộp thoại con (Confirm) trước, hộp thoại cha (Save As) sau — lặp vài vòng tới khi hết.
        """
        closed, seen = 0, []
        for _ in range(6):
            try:
                hs = list(self.dialogs())
            except Exception:
                break
            if not hs:
                break
            for h in hs:
                seen.append(self._dialog_texts(h)[:120])
                if self._dismiss_dialog(h):
                    closed += 1
            time.sleep(0.8)
        if closed:
            self.log.warning("Đã đóng %d hộp thoại Windows còn sót%s: %s", closed, f" ({why})" if why else "",
                             " || ".join(dict.fromkeys(seen))[:300])
        return closed

    def save_via_dialog(self, dlg, target: Path, timeout: int = 90) -> Path:
        """
        Lưu file MES xuất ra bằng hộp thoại Save As → trả về đường dẫn file cuối cùng (target + đuôi .xls/.xlsx).

        VÌ SAO LƯU QUA THƯ MỤC TẠM RỖNG: thư mục SanLuong còn file cũ (vd 2026-10-29_150055.xls chưa import) →
        hộp thoại Save As có thể lấy nhầm file đang được chọn / gợi ý tự hoàn thành cùng tiền tố "2026-10-29_"
        → hiện "Confirm Save As: ... already exists. Do you want to replace it?" → MES bị khóa bởi hộp thoại.
        Cách làm:
          1. Tạo thư mục tạm RỖNG riêng cho lần lưu này (storage/SanLuong/.dang_luu_<tên>/)
          2. Điền đường dẫn thư mục → Save = hộp thoại chuyển vào thư mục rỗng (không còn file nào để chọn nhầm)
          3. Điền đường dẫn đầy đủ của file → Save
          4. Hiện hộp thoại hỏi ghi đè → bấm No (không bao giờ ghi đè), lưu lại với tên mới; vẫn bị → báo lỗi
          5. MES ghi xong → chuyển file ra thư mục đích, xóa thư mục tạm
        Lỗi ở bất kỳ bước nào → đóng hết hộp thoại còn mở để MES không bị khóa cho lần chạy sau.
        """
        self._cleanup_tmp_dirs(target.parent)
        try:
            for attempt in (1, 2):
                name = target.name if attempt == 1 else f"{target.name}_{time.strftime('%H%M%S')}"
                tmp = target.parent / f"{TMP_PREFIX}{name}"
                tmp.mkdir(parents=True, exist_ok=True)
                f = self._save_into(dlg, tmp, name, timeout)
                if f is not None:
                    return self._move_out(f, target.parent, tmp)
                self.log.warning("Windows hỏi ghi đè file → đã bấm No, lưu lại với tên mới")
                dlg = self._current_save_dialog(dlg)
            raise MesError("Hộp thoại Save As vẫn hỏi ghi đè sau khi đổi tên file", "MES_SAVE_DIALOG_FAILED")
        except Exception:
            self.close_stray_dialogs("lưu file lỗi")
            raise

    def _current_save_dialog(self, dlg):
        """Hộp thoại Save As còn mở sau khi bấm No ở hộp thoại hỏi ghi đè"""
        h = dlg.wrapper_object().handle if hasattr(dlg, "wrapper_object") else dlg.handle
        if win32gui.IsWindow(h) and win32gui.IsWindowVisible(h):
            return dlg
        raise MesError("Hộp thoại Save As đã đóng sau khi từ chối ghi đè", "MES_SAVE_DIALOG_FAILED")

    def _save_into(self, dlg, folder: Path, name: str, timeout: int) -> Path | None:
        """Điền + bấm Save rồi chờ file xuất hiện trong `folder`. None = Windows hỏi ghi đè (đã bấm No)"""
        dlg_h = dlg.wrapper_object().handle
        edit_h, save_h = self._save_dialog_controls(dlg_h)
        # Bước 1: chuyển hộp thoại vào thư mục tạm rỗng (điền đường dẫn thư mục + Save = mở thư mục đó)
        winutil.set_text(edit_h, str(folder))
        winutil.post_click(save_h)
        time.sleep(1.2)
        if win32gui.IsWindow(dlg_h):
            edit_h, save_h = self._save_dialog_controls(dlg_h)
        # Bước 2: tên file đầy đủ (vẫn dùng đường dẫn tuyệt đối — bước 1 không chuyển được thư mục cũng lưu đúng chỗ)
        target = folder / name
        winutil.set_text(edit_h, str(target))
        winutil.post_click(save_h)
        self.log.info("Lưu file vào: %s.*", target)

        deadline, last_size, stable = time.time() + timeout, -1, 0
        while time.time() < deadline:
            time.sleep(1)
            self.handle_popup()
            # Hộp thoại khác của Windows trong lúc lưu: hỏi ghi đè → No; còn lại (đường dẫn sai, file đang mở...) → lỗi
            for h in self.dialogs():
                if h == dlg_h or not win32gui.IsWindowVisible(h):
                    continue
                text = self._dialog_texts(h)
                if any(w in text.lower() for w in OVERWRITE_WORDS):
                    self._dismiss_dialog(h)
                    time.sleep(1)
                    self.log.warning("Windows hỏi ghi đè: %s", text[:200])
                    return None
                raise MesError(f"Windows báo khi lưu file: {text[:300]}", "MES_SAVE_DIALOG_FAILED")
            files = [f for f in folder.glob(name + "*") if f.is_file()]
            if files:
                f = max(files, key=lambda x: x.stat().st_mtime)
                size = f.stat().st_size
                stable = stable + 1 if size == last_size and size > 0 else 0
                last_size = size
                if stable >= 2:
                    self.log.info("MES đã ghi xong file %s (%d byte)", f.name, size)
                    return f
        raise MesError(f"Sau {timeout}s chưa thấy file được lưu ở {folder}", "MES_SAVE_DIALOG_FAILED")

    def _move_out(self, f: Path, dest_dir: Path, tmp: Path) -> Path:
        """Chuyển file từ thư mục tạm ra thư mục đích (MES có thể còn giữ file vài giây → thử lại)"""
        dest = dest_dir / f.name
        i = 1
        while dest.exists():
            dest, i = dest_dir / f"{f.stem}_{i}{f.suffix}", i + 1
        for _ in range(15):
            try:
                shutil.move(str(f), str(dest))
                break
            except OSError:
                time.sleep(1)
        else:
            self.log.warning("Chưa chuyển được %s ra ngoài (MES còn giữ file) — dùng luôn file trong thư mục tạm", f.name)
            return f
        try:
            tmp.rmdir()
        except OSError:
            pass
        return dest

    def _cleanup_tmp_dirs(self, parent: Path):
        """Xóa thư mục tạm RỖNG còn sót của các lần lưu trước (thư mục còn file thì giữ — file đó vẫn đang được dùng)"""
        try:
            for d in parent.glob(TMP_PREFIX + "*"):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
        except OSError:
            pass

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

    # ================= Ô mã [code][...][mô tả] (Plant, Oper...) =================
    def find_code_view(self, form, candidates: list[str], value_re: str | None = None, what: str = ""):
        """
        Tìm ô mã Miracom (cdv*) theo danh sách AutomationId; không thấy thì dò mọi ô cdv* trên form có ô txtDisplay
        đang chứa giá trị khớp `value_re` (vd Plant: ^PL\\w+$). Trả về WindowSpecification của ô cdv.
        """
        for aid in candidates:
            spec = form.child_window(auto_id=aid)
            if spec.exists(timeout=0.5):
                return spec
        if value_re:
            rx = re.compile(value_re, re.I)
            try:
                for w in form.wrapper_object().descendants():
                    aid = w.element_info.automation_id or ""
                    if not aid.lower().startswith("cdv"):
                        continue
                    spec = form.child_window(auto_id=aid)
                    edit = spec.child_window(auto_id="txtDisplay")
                    if edit.exists(timeout=0) and rx.match((edit.wrapper_object().window_text() or "").strip()):
                        self.log.info("Ô %s trên MES có AutomationId '%s'", what, aid)
                        return spec
            except Exception:
                pass
        raise MesError(f"Không tìm thấy ô {what} trên màn hình MES (đã thử {candidates}). "
                       "Chạy `manage.py probe_form \"View Work Order\"` để xem AutomationId", "MES_CONTROL_NOT_FOUND")

    @staticmethod
    def _edit_text(w) -> str:
        try:
            return (w.get_value() or w.window_text() or "").strip()
        except Exception:
            try:
                return (w.window_text() or "").strip()
            except Exception:
                return ""

    def code_view_state(self, cdv) -> tuple[str, str, bool]:
        """(mã đang chọn, mô tả, ô có phần mô tả không)"""
        code_w = self._control(cdv, "ô nhập mã", auto_id="txtDisplay")
        code = self._edit_text(code_w)
        descs = []
        for e in cdv.wrapper_object().descendants(control_type="Edit"):
            if e.element_info.automation_id == "txtDisplay":
                continue
            descs.append(self._edit_text(e))
        return code, " ".join(d for d in descs if d), bool(descs)

    @staticmethod
    def _code_ok(code: str, desc: str, has_desc: bool, value: str) -> bool:
        """
        Ô đã nhận mã chưa. Mỗi ô MES hiện khác nhau: Plant hiện mã ở ô chính + tên ở ô mô tả ("PL20" | "TV BOARD PLANT"),
        Oper thì ngược lại ("SMD" | "2010") → mã nằm ở ô nào cũng được, ô còn lại phải có chữ.
        """
        v = value.strip().casefold()
        c, d = code.strip().casefold(), desc.strip().casefold()
        if c == v:
            return bool(d) or not has_desc
        return d == v and bool(c)

    def _visible_windows(self) -> set[int]:
        """
        Mọi cửa sổ đang hiện của MESClient: cửa sổ cấp cao (popup danh sách mã thường là cửa sổ riêng, không tiêu đề)
        + cửa sổ con của khung chính (popup kiểu control ẩn/hiện ngay trên form). So 2 lần chụp → biết popup mới.
        """
        import win32process

        res = set()

        def visit(h, _):
            try:
                if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] == self.pid:
                    res.add(h)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(visit, None)
        res |= {h for h in self._child_hwnds() if win32gui.IsWindowVisible(h)}
        return res

    def select_code_view(self, cdv, value: str, what: str) -> str:
        """
        Chọn mã cho ô [code][...][mô tả] GIỐNG NGƯỜI DÙNG: bấm nút "..." → chọn dòng có mã đó trong danh sách → Enter.
        (Không điền chữ vào ô: MES không nhận, và ô mô tả còn chữ của lần chọn trước → tưởng đã chọn đúng mà bộ lọc sai.)
        Xác nhận: sau khi chọn, mã + mô tả trên ô phải đúng là các cột của dòng vừa chọn (vd "SMD" | "2010").
        Lựa chọn đã xác nhận được nhớ trong phiên → lần sau cùng mã thì bỏ qua (ô vẫn đúng như lúc xác nhận).
        Trả về mô tả (vd "TV BOARD PLANT").
        """
        code, desc, has_desc = self.code_view_state(cdv)
        if self._confirmed.get(what) == (value, code, desc):
            self.log.info("%s đã là %s %s", what, code, desc)
            return desc

        btns = cdv.wrapper_object().descendants(control_type="Button")
        if not btns:
            raise MesError(f"Ô {what} không có nút '...' để mở danh sách", "MES_CONTROL_NOT_FOUND")
        before = self._visible_windows()
        winutil.post_click(btns[0].handle)
        popup = set()
        deadline = time.time() + 6
        while time.time() < deadline and not popup:
            time.sleep(0.4)
            popup = self._visible_windows() - before
        if not popup:
            raise MesError(f"Bấm nút '...' của ô {what} nhưng không thấy danh sách xổ ra", "MES_CODE_SELECT_FAILED")
        cells = self._pick_in_popup(popup, value, what)
        self.wait(0.8)

        code, desc, has_desc = self.code_view_state(cdv)
        row = {c.strip().casefold() for c in cells if c and c.strip()}
        shown = {x.strip().casefold() for x in (code, desc) if x and x.strip()}
        if self._code_ok(code, desc, has_desc, value) and shown <= row:
            self._confirmed[what] = (value, code, desc)
            self.log.info("Chọn %s = %s %s (qua danh sách)", what, code, desc)
            return desc
        self._confirmed.pop(what, None)
        raise MesError(f"Chọn {what} = {value} thất bại: ô đang là '{code}' / '{desc}', dòng đã chọn {cells}",
                       "MES_CODE_SELECT_FAILED")

    def _pick_in_popup(self, popup: set[int], value: str, what: str) -> list[str]:
        """
        Trong danh sách xổ ra: tìm dòng có 1 cột đúng bằng `value` → chọn → Enter. Trả về các cột của dòng đã chọn.
        Không thấy dòng → Esc + báo lỗi kèm các dòng đọc được.
        """
        from pywinauto.controls.common_controls import ListViewWrapper

        want = value.strip().casefold()
        seen, classes = [], []
        # Chỉ xét "gốc" của popup (cửa sổ mới hiện mà cửa sổ cha không phải cũng mới hiện)
        roots = [h for h in popup if win32gui.GetParent(h) not in popup]
        for top in roots:
            hs = [top]
            win32gui.EnumChildWindows(top, lambda c, _: hs.append(c), None)
            classes += [win32gui.GetClassName(h) for h in hs]
            # Cách 1: ListView Win32 (đọc được từng ô)
            for h in hs:
                if "LISTVIEW" not in win32gui.GetClassName(h).upper():
                    continue
                lv = ListViewWrapper(h)
                ncol = max(lv.column_count(), 1)
                for i in range(lv.item_count()):
                    cells = [(lv.get_item(i, j).text() or "").strip() for j in range(ncol)]
                    seen.append(" ".join(cells))
                    if want in (c.casefold() for c in cells):
                        item = lv.get_item(i)
                        item.ensure_visible()
                        item.select()
                        time.sleep(0.3)
                        winutil.send_key(h, win32con.VK_RETURN, 13)
                        self._wait_closed(h, roots, what, value)
                        return cells
            # Cách 2: UI Automation (danh sách/lưới kiểu khác)
            try:
                root = Desktop(backend="uia").window(handle=top).wrapper_object()
                items = root.descendants(control_type="ListItem") + root.descendants(control_type="DataItem")
            except Exception:
                items = []
            for it in items:
                try:
                    texts = [it.window_text()] + [c.window_text() for c in it.descendants()]
                except Exception:
                    continue
                cells = [t.strip() for t in texts if t and t.strip()]
                seen.append(" ".join(cells))
                if want in (c.casefold() for c in cells):
                    try:
                        it.select()
                    except Exception:
                        pass
                    try:
                        it.invoke()
                    except Exception:
                        winutil.send_key(it.handle or top, win32con.VK_RETURN, 13)
                    self._wait_closed(it.handle or top, roots, what, value)
                    return cells
        for top in roots:
            winutil.post_keys(top, [win32con.VK_ESCAPE])
        raise MesError(f"Danh sách {what} không có mã {value}. Các dòng đọc được: {seen[:20] or '(không đọc được)'}; "
                       f"loại control: {sorted(set(classes))[:15]}", "MES_CODE_SELECT_FAILED")

    def _wait_closed(self, list_h: int, roots: list[int], what: str, value: str):
        """Chờ danh sách đóng (control danh sách hết hiện). Enter chưa đóng → thử nhấp đúp; vẫn chưa → Esc + báo lỗi"""
        def is_open():
            return win32gui.IsWindow(list_h) and win32gui.IsWindowVisible(list_h)

        deadline = time.time() + 4
        while time.time() < deadline:
            if not is_open():
                return
            time.sleep(0.3)
        try:   # ListView tự gửi NM_DBLCLK/ItemActivate khi nhận WM_LBUTTONDBLCLK
            win32gui.SendMessageTimeout(list_h, win32con.WM_LBUTTONDBLCLK, win32con.MK_LBUTTON, 0,
                                        win32con.SMTO_ABORTIFHUNG, 3000)
        except Exception:
            pass
        time.sleep(1)
        if is_open():
            for h in roots:
                winutil.post_keys(h, [win32con.VK_ESCAPE])
            raise MesError(f"Đã chọn {value} trong danh sách {what} nhưng danh sách không đóng", "MES_CODE_SELECT_FAILED")

    # ================= Combo (vd kiểu kỳ: Curr.D / Prev.D / ... / Period) =================
    def find_combo_with_item(self, form, item: str) -> int:
        """Handle ComboBox trên form có mục `item` (so không phân biệt hoa thường)"""
        from pywinauto.controls.win32_controls import ComboBoxWrapper

        want = item.strip().casefold()
        hs = []
        win32gui.EnumChildWindows(form.wrapper_object().handle, lambda c, _: hs.append(c), None)
        for h in hs:
            if "COMBOBOX" not in win32gui.GetClassName(h).upper() or not win32gui.IsWindowVisible(h):
                continue
            try:
                texts = [t.strip().casefold() for t in ComboBoxWrapper(h).item_texts()]
            except Exception:
                continue
            if want in texts:
                return h
        raise MesError(f"Không thấy ô chọn có mục '{item}' trên màn hình MES", "MES_CONTROL_NOT_FOUND")

    def select_combo(self, h: int, item: str):
        """Chọn mục trong ComboBox WinForms: CB_SETCURSEL + báo CBN_SELCHANGE cho form cha (MES nhận như người chọn)"""
        from pywinauto.controls.win32_controls import ComboBoxWrapper

        cb = ComboBoxWrapper(h)
        texts = [t.strip() for t in cb.item_texts()]
        idx = next((i for i, t in enumerate(texts) if t.casefold() == item.strip().casefold()), None)
        if idx is None:
            raise MesError(f"Ô chọn không có mục '{item}' (có: {texts})", "MES_CONTROL_NOT_FOUND")
        if cb.selected_index() != idx:
            cb.select(idx)
            time.sleep(0.6)
        if cb.selected_index() != idx:
            raise MesError(f"Chọn '{item}' thất bại (đang là '{cb.selected_text()}')", "MES_CONTROL_NOT_FOUND")
        self.log.info("Chọn kiểu kỳ = %s", texts[idx])

    # ================= Ô ngày (DateTimePicker) =================
    def get_date(self, form, auto_id: str):
        """Ngày đang hiện ở ô DateTimePicker (date) hoặc None"""
        v = winutil.dtp_get(self._control(form, f"ô {auto_id}", auto_id=auto_id).handle)
        return v.date() if v else None

    def set_date(self, form, auto_id: str, value, what: str, up_first: bool = False):
        """Đặt ngày cho ô DateTimePicker (dtpFromDate / dtpToDate) rồi đọc lại để chắc chắn MES đã nhận.
        up_first: xem winutil.dtp_set — "từ ngày" False, "đến ngày" True"""
        from datetime import datetime as _dt

        w = self._control(form, f"ô {what}", auto_id=auto_id)
        h = w.handle
        cls = win32gui.GetClassName(h)
        if "DATETIMEPICK" not in cls.upper():
            raise MesError(f"Ô {what} không phải DateTimePicker (loại: {cls})", "MES_DATE_SET_FAILED")
        deadline = time.time() + 3
        while not win32gui.IsWindowEnabled(h):
            if time.time() > deadline:
                raise MesError(f"Ô {what} đang bị khóa (chưa chọn kiểu kỳ 'Period'?)", "MES_DATE_SET_FAILED")
            time.sleep(0.3)
        target = value if isinstance(value, _dt) else _dt(value.year, value.month, value.day)
        try:
            retries = winutil.dtp_set(h, target, up_first=up_first)
        except OSError as e:
            raise MesError(f"Đặt {what} = {target:%d/%m/%Y} thất bại: {e}", "MES_DATE_SET_FAILED")
        time.sleep(0.3)
        got = winutil.dtp_get(h)
        if got is None or got.date() != target.date():
            now = f"{got:%d/%m/%Y}" if got else "(trống)"
            raise MesError(f"Đặt {what} thất bại: muốn {target:%d/%m/%Y}, ô đang là {now}", "MES_DATE_SET_FAILED")
        self.log.info("Đặt %s = %s (ô hiện '%s'%s)", what, f"{target:%d/%m/%Y}", w.window_text(),
                      f", thử lại {retries} lần" if retries else "")

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
