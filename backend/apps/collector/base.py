"""Phần chung của mọi agent thao tác MES: mở/kết nối MESClient, tự đăng nhập, đóng popup"""
import logging
import time
from pathlib import Path

from . import winutil
from .clipboard_guard import ClipboardGuard
from .errors import AgentError
from .mes_client import MesClient


DEFAULT_CONFIG = {
    "view_wait": 8,                 # chờ MES tải dữ liệu sau khi bấm View (giây)
    "export_timeout": 240,          # sản lượng: chờ MES ghi xong file sau khi bấm Excel (giây)
    "auto_dismiss_upgrade": True,   # tự bấm No ở hộp thoại hỏi nâng cấp
    "exe_path": "",                 # đường dẫn MESClient.exe để tự mở
    "auto_start": True,
    "start_timeout": 90,
    "user_id": "",                  # tài khoản MES để tự đăng nhập
    "password": "",
    "login_timeout": 60,
    "step_retries": 3,              # số lần làm 1 bước (1 ngày sản lượng / 1 xưởng) khi gặp lỗi tạm thời
    "retry_wait": 5,                # nghỉ giữa 2 lần thử (giây)
    "clipboard_kill_list": "et.exe,wps.exe,wpsoffice.exe,wpscloudsvr.exe,wpscenter.exe,ksolaunch.exe",   # app ĐƯỢC PHÉP tắt khi đang giữ clipboard
    "clipboard_priority": True,     # ưu tiên scan MES: được tắt mọi app giữ clipboard (job xong tự mở lại)
    "clipboard_protect_list": "",   # app không bao giờ tắt
    "clipboard_max_wait": 1800,     # clipboard khóa bởi tiến trình không rõ → chờ tự nhả tối đa (giây, chung cả job)
    "clipboard_pause_list": "ditto.exe,1clipboard.exe,clipx.exe,clipmate.exe,clipclip.exe,clipdiary.exe",  # app tạm dừng suốt lúc xuất      # trước khi bấm Excel (Work Order): tắt WPS rỗng còn sót của các lần xuất trước
}

# Lỗi TẠM THỜI → tự đóng hộp thoại, dọn dẹp rồi làm lại bước đó (không bỏ cả ngày/xưởng ngay lần đầu).
# KHÔNG có MES_POPUP_BLOCKING: lỗi đó = đã thử mọi cách đóng hộp thoại mà vẫn còn → làm lại cũng vô ích, dừng job.
RETRYABLE_CODES = {
    "CLIPBOARD_BUSY",
    "MES_CODE_LIST_STUCK",        # danh sách Plant/Oper chọn xong không đóng (MES chậm) → đóng, làm lại (23/11)
    "MES_GRID_COPY_EMPTY",        # Ctrl+C không chép được gì (màn hình khóa / bị che) → làm lại
    "MES_SESSION_LOST",           # "Session Not Connected": phiên MES rớt → tắt + mở lại MES, đăng nhập lại
    "MES_POPUP_ERROR",            # MES hiện hộp thoại lỗi (Request Reply Timeout, Clipboard...) — đã tự đóng
    "MES_BUSY_TIMEOUT",
    "MES_SAVE_DIALOG_NOT_FOUND",
    "MES_SAVE_DIALOG_FAILED",
    "MES_DATA_MISMATCH",
}


# Phiên MES với máy chủ đã rớt (19/11: sau 30 phút chờ clipboard) → mọi thao tác sau đều lỗi "Object reference..."
# và hộp thoại có khi không đóng được → chỉ có cách tắt hẳn MES, mở lại, đăng nhập lại
SESSION_LOST_WORDS = ("session not connected",)


def classify(e: Exception) -> str | None:
    """Mã lỗi thật của e (ưu tiên nội dung hộp thoại MES hơn mã chung MES_POPUP_ERROR/MES_POPUP_BLOCKING)"""
    msg = str(e).lower()
    if any(w in msg for w in SESSION_LOST_WORDS):
        return "MES_SESSION_LOST"
    return getattr(e, "code", None)


class BaseCollector:
    def __init__(self, log: logging.Logger, config: dict | None = None):
        winutil.enable_dpi_awareness()
        self.log = log
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.mes = MesClient(log, self.cfg["auto_dismiss_upgrade"])
        self.clip = ClipboardGuard(log, self.cfg)
        self.form = None

    def prepare(self, _restarted: bool = False):
        """Bước chuẩn bị chung — lỗi ở đây có mã lỗi rõ ràng (MES_NOT_RUNNING, MES_LOGIN_*...)"""
        self.clip.resume_leftovers()           # worker chết lúc đang tạm dừng app → cho chạy lại
        self.mes.connect(self.cfg["exe_path"], self.cfg["auto_start"], self.cfg["start_timeout"])
        self._remember_exe_path()
        # Hộp thoại Save As / Confirm Save As còn sót của lần chạy trước khóa MES → đóng trước khi làm gì khác
        self.mes.close_stray_dialogs("trước khi chạy")
        self.mes.handle_popup()
        self.mes.ensure_logged_in(self.cfg["user_id"], self.cfg["password"], self.cfg["login_timeout"])
        self.mes.handle_popup()
        left = self.mes.take_popup_errors()    # hộp thoại lỗi còn sót từ trước (đã đóng) — không phải lỗi của lần này
        if left:
            self.log.info("Đã đóng %d hộp thoại lỗi còn sót trên MES trước khi chạy", len(left))
            if not _restarted and any(w in " ".join(left).lower() for w in SESSION_LOST_WORDS):
                self.restart_mes("còn hộp thoại 'Session Not Connected' từ lần trước")
                return self.prepare(_restarted=True)

    def recover(self, why: str = ""):
        """Dọn MES về trạng thái sạch trước khi làm lại 1 bước: đóng mọi hộp thoại MES + hộp thoại Windows còn sót"""
        try:
            self.mes.close_stray_dialogs(why or "chạy lại")
        except Exception as e:
            self.log.info("Dọn hộp thoại Windows lỗi: %s", e)
        try:
            self.mes.handle_popup()
        except Exception as e:
            self.log.info("Dọn hộp thoại MES lỗi: %s", e)
        self.mes.take_popup_errors()
        self.mes.no_data_message = ""

    def with_retry(self, what: str, fn, *args, **kwargs):
        """
        Chạy fn; gặp lỗi tạm thời (RETRYABLE_CODES — chủ yếu hộp thoại lỗi MES đã tự đóng) → recover() → chờ → làm lại,
        tối đa step_retries lần. Lỗi khác (sai cấu hình, không tìm thấy nút...) ném ra ngay.
        Bộ nhớ hộp thoại: hộp thoại đã đóng (vd Request Reply Timeout → bấm OK) được chấm bằng LẦN LÀM LẠI KẾ TIẾP —
        làm lại thành công → "đóng + chạy tiếp được" (ưu tiên lần sau); hết lượt vẫn lỗi → "đóng nhưng bước vẫn lỗi".
        (Không chấm lỗi ngay lần thử bị hộp thoại đó làm hỏng, vì hộp thoại chính là lý do lần đó lỗi.)
        """
        attempts = max(1, int(self.cfg.get("step_retries") or 1))
        wait = float(self.cfg.get("retry_wait") or 0)
        for i in range(1, attempts + 1):
            self.mes.step = what if i == 1 else f"{what} (lần {i})"
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                code = classify(e)
                if code == "MES_SESSION_LOST" and isinstance(e, AgentError):
                    e.code = code
                if not isinstance(e, AgentError) or code not in RETRYABLE_CODES or i >= attempts:
                    self.mes.resolve_popups(False, str(e))      # hết lượt → đóng xong mà bước KHÔNG chạy tiếp được
                    if not isinstance(e, AgentError):
                        raise
                    if i > 1:
                        self.log.error("%s vẫn lỗi sau %d lần thử", what, i)
                    raise
                self.log.warning("%s lỗi lần %d/%d [%s]: %s → đóng hộp thoại, chờ %ss rồi làm lại",
                                 what, i, attempts, code, e, int(wait))
                if code == "MES_SESSION_LOST":
                    self.restart_mes("MES báo 'Session Not Connected'")
                else:
                    self.recover(f"chạy lại {what}")
                time.sleep(wait)
            else:
                self.mes.resolve_popups(True)
                return result

    def restart_mes(self, why: str):
        """Tắt hẳn MESClient; lần thao tác sau open() tự mở lại MES, đăng nhập, mở lại màn hình"""
        from .mes_client import _mes_procs
        procs = _mes_procs()
        self.log.warning("%s → tắt MESClient (%d tiến trình), mở lại + đăng nhập lại", why, len(procs))
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        try:
            import psutil
            psutil.wait_procs(procs, timeout=10)
        except Exception:
            pass
        time.sleep(2)
        self.form = None

    def _remember_exe_path(self):
        """Cấu hình mes_exe_path sai/khác với MESClient đang chạy → lưu đường dẫn thật để lần sau tự mở được"""
        found = self.mes.exe_found
        if not found or Path(found) == Path(self.cfg["exe_path"] or "."):
            return
        try:
            from apps.core.models import Setting
            Setting.objects.filter(key="mes_exe_path").update(value=found)
            self.cfg["exe_path"] = found
            self.log.info("Đã cập nhật đường dẫn MESClient.exe trong cài đặt: %s", found)
        except Exception as e:
            self.log.info("Không lưu được đường dẫn MESClient.exe (%s): %s", found, e)

    def screenshot(self, path: Path):
        self.mes.screenshot(path)
