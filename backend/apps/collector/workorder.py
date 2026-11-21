"""
Lấy kế hoạch View Work Order từ MESClient.
Luồng (đúng như thao tác tay):
  menu [PM]Production Management → Process Control → Work Order Management → View Work Order
  → Plant: chọn mã (vd PL20 TV BOARD PLANT) qua ô Plant / nút "..."
  → Period: ô chọn kiểu kỳ = "Period" → đặt từ ngày ~ đến ngày (ô ngày hiển thị MM/DD/YYYY)
  → Oper: chọn mã (2010 SMD, 3010 MAIN...) qua ô Oper / nút "..."
  → View → bấm ô góc lưới (chọn hết) → Ctrl+C → đọc clipboard → DataFrame (grid_copy.py; không dùng nút Excel/WPS)
Sau khi đọc vẫn lọc lại theo cột Oper cho chắc (phòng khi MES trả lẫn xưởng khác).
"""
import time
from datetime import date, datetime

import pandas as pd

from . import winutil
from .base import BaseCollector
from .errors import MesError
from .grid_copy import copy_grid, parse_grid_text

MENU_VIEW_WORK_ORDER = [
    "[PM]Production Management",
    "Process Control",
    "Work Order Management",
    "View Work Order",
]
FORM_VIEW_WORK_ORDER = "frmCWIPViewWorkOrderList"   # AutomationId của form

# AutomationId các ô điều kiện. Ô Plant chưa khảo sát → thử lần lượt, không thấy thì dò ô cdv* đang chứa "PL.."
PLANT_CDV = ["cdvPlant", "cdvFactory", "cdvPlantID", "cdvPlantCode", "cdvFac"]
OPER_CDV = ["cdvOper"]
PERIOD_ITEM = "Period"                               # mục cuối của ô chọn kỳ (Curr.D, Prev.D, ..., Period)
FROM_DATE, TO_DATE = "dtpFromDate", "dtpToDate"
GRID = "spdData_top"                                 # lưới kết quả (FarPoint — không đọc được ô, chỉ chụp ảnh)


class WorkOrderCollector(BaseCollector):
    def __init__(self, log, config=None):
        super().__init__(log, config)
        # Work Order chép lưới qua clipboard → hay lỗi tạm thời hơn sản lượng
        self.cfg["step_retries"] = max(int(self.cfg.get("step_retries") or 1),
                                       int(self.cfg.get("step_retries_workorder") or 4))

    def open(self):
        self.prepare()
        self.form = self.mes.open_form(FORM_VIEW_WORK_ORDER, MENU_VIEW_WORK_ORDER)
        self.log.info("Period đang hiện trên MES: %s", self.mes.read_period(self.form))

    # ---------- từng bước điều kiện ----------
    def set_plant(self, code: str) -> str:
        cdv = self.mes.find_code_view(self.form, PLANT_CDV, value_re=r"^PL\w*$", what="Plant")
        return self.mes.select_code_view(cdv, code, "Plant")

    def set_period(self, date_from: date, date_to: date):
        if date_from > date_to:
            raise ValueError(f"Từ ngày {date_from:%d/%m/%Y} sau đến ngày {date_to:%d/%m/%Y}")
        combo = self.mes.find_combo_with_item(self.form, PERIOD_ITEM)
        self.mes.select_combo(combo, PERIOD_ITEM)          # mở khóa ô "đến ngày"
        self.mes.wait(0.5)
        # Từ ngày 00:00:00, đến ngày 23:59:59 → lấy trọn cả 2 ngày đầu/cuối dù MES so sánh cả giờ
        f_dt = datetime(date_from.year, date_from.month, date_from.day)
        t_dt = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        # MES tự sửa ô kia khi Từ > Đến → đặt xong cả 2 thì đọc lại; ô nào bị MES đổi thì đặt lại (tối đa 3 vòng)
        for rnd in range(3):
            if self.mes.get_date(self.form, FROM_DATE) != date_from or rnd == 0:
                self.mes.set_date(self.form, FROM_DATE, f_dt, "Period từ ngày", up_first=False)
            if self.mes.get_date(self.form, TO_DATE) != date_to or rnd == 0:
                self.mes.set_date(self.form, TO_DATE, t_dt, "Period đến ngày", up_first=True)
            self.mes.wait(0.3)
            got = (self.mes.get_date(self.form, FROM_DATE), self.mes.get_date(self.form, TO_DATE))
            if got == (date_from, date_to):
                break
            self.log.info("MES tự đổi khoảng ngày thành %s ~ %s → đặt lại",
                          *(f"{d:%d/%m/%Y}" if d else "?" for d in got))
        else:
            raise MesError(f"MES không giữ khoảng ngày {date_from:%d/%m/%Y} ~ {date_to:%d/%m/%Y} "
                           f"(đang {self.mes.read_period(self.form)})", "MES_DATE_SET_FAILED")
        self.log.info("Period trên MES: %s", self.mes.read_period(self.form))

    def set_oper(self, code: str) -> str:
        cdv = self.mes.find_code_view(self.form, OPER_CDV, what="Oper")
        return self.mes.select_code_view(cdv, code, "Oper")

    def set_conditions(self, plant: str, date_from: date, date_to: date, oper: str):
        """Đúng thứ tự thao tác tay: Plant → Period → Oper"""
        if self.form is None:
            self.open()
        self.set_plant(plant)
        self.set_period(date_from, date_to)
        self.set_oper(oper)

    # ---------- xuất ----------
    def export(self, plant: str, date_from: date, date_to: date, oper: str,
               view_wait: float | None = None) -> tuple[pd.DataFrame, datetime]:
        """Như _export_once; lỗi tạm thời (hộp thoại MES, chép lưới rỗng...) → tự đóng, làm lại (with_retry)"""
        return self.with_retry(f"Xuất Work Order Oper {oper}", self._export_once, plant, date_from, date_to, oper,
                               view_wait=view_wait)

    def _export_once(self, plant: str, date_from: date, date_to: date, oper: str,
                     view_wait: float | None = None) -> tuple[pd.DataFrame, datetime]:
        """
        Đặt điều kiện → View → (chờ MES tải xong) → chép TOÀN BỘ lưới (chưa lọc lại theo Oper).
        Lưới trống / MES báo "không có dữ liệu" → trả bảng rỗng (0 dòng), không phải lỗi.
        """
        self.log.info("----- Xuất Work Order: Plant %s · %s ~ %s · Oper %s -----",
                      plant, f"{date_from:%d/%m/%Y}", f"{date_to:%d/%m/%Y}", oper)
        self.set_conditions(plant, date_from, date_to, oper)

        self.mes.no_data_message = ""
        self.mes.click(self.form, "btnListView")
        time.sleep(1)
        busy = self.mes.wait_idle()                       # MES truy vấn trên luồng giao diện → chờ trả lời lại
        wait = self.cfg["view_wait"] if view_wait is None else view_wait
        self.log.info("MES tải dữ liệu %.0fs; chờ thêm %ss cho lưới vẽ xong...", busy, wait)
        self.mes.wait(wait)
        if self.mes.no_data_message:
            return self._empty(f"MES báo: {self.mes.no_data_message}"), datetime.now().replace(microsecond=0)
        self.mes.check_popup_errors("tải dữ liệu (View)")      # vd Request Reply Timeout → làm lại xưởng này
        # MES không báo gì khi không có Work Order, chỉ để lưới trống (19/11) → chụp lưới; trống 2 lần liền → dừng
        if self.grid_has_rows() is False:
            self.mes.wait(5)
            if self.grid_has_rows(save_as=f"nodata_{oper}") is False:
                return self._empty("lưới MES trống sau khi bấm View"), datetime.now().replace(microsecond=0)

        # Chép lưới như thao tác tay: chọn hết → Ctrl+C → đọc clipboard → DataFrame.
        # KHÔNG dùng nút Excel → WPS nữa (21/11: Class not registered / Server execution failed liên tục)
        with self.clip.paused():
            self.clip.preempt()
            why = self.clip.ensure_free()       # clipboard bị khóa thì Ctrl+C cũng hỏng → chờ như trước
            if why:
                raise MesError(f"Không chép được lưới: {why}", "CLIPBOARD_BUSY")
            export_dt = datetime.now().replace(microsecond=0)
            grid = self.mes._control(self.form, "lưới Work Order", auto_id=GRID)
            text = copy_grid(grid.handle, self.mes.hwnd, self.log)
        self.mes.check_popup_errors("chép lưới (Ctrl+C)")
        df = parse_grid_text(text)
        self.log.info("Đọc được %d dòng, %d cột từ lưới MES", len(df), len(df.columns))
        return df, export_dt

    def _empty(self, why: str) -> pd.DataFrame:
        self.log.info("Không có Work Order với điều kiện này (%s) → không chép lưới", why)
        return pd.DataFrame(columns=["Factory", "Work Order", "Oper"])

    def grid_has_rows(self, save_as: str = "") -> bool | None:
        """
        Lưới kết quả có dòng dữ liệu không — chụp lưới (PrintWindow) rồi đếm điểm ảnh tối (chữ) ở vùng ngay dưới
        dòng tiêu đề. Lưới trống = nền xanh nhạt, không có chữ. None = không chụp được → coi như có, cứ chép lưới.
        save_as: lưu ảnh vào storage/screenshots để đối chiếu khi kết luận "trống".
        """
        import ctypes

        from PIL import ImageStat

        try:
            h = self.mes._control(self.form, "lưới Work Order", auto_id=GRID).handle
            img, ok = winutil.grab_window(h)
            if not ok:
                return None
            scale = (ctypes.windll.user32.GetDpiForWindow(h) or 96) / 96
            w, ht = img.size
            box = (int(40 * scale), int(48 * scale), w - int(25 * scale), min(ht - int(25 * scale), int(170 * scale)))
            if box[2] - box[0] < 50 or box[3] - box[1] < 20:
                return None
            gray = img.convert("L")
            if ImageStat.Stat(gray).mean[0] < 40:          # ảnh đen = PrintWindow không vẽ được
                return None
            region = gray.crop(box)
            dark = sum(region.histogram()[:110])
            has = dark > max(30, region.width * region.height // 2000)
            self.log.info("Lưới sau View: %d điểm chữ ở vùng dòng đầu → %s", dark, "CÓ dữ liệu" if has else "TRỐNG")
            if not has and save_as:
                from .clipboard_guard import STORAGE
                p = STORAGE / "screenshots" / f"{save_as}_{datetime.now():%Y%m%d_%H%M%S}.png"
                p.parent.mkdir(parents=True, exist_ok=True)
                img.save(p)
                self.log.info("Ảnh lưới trống: %s", p.name)
            return has
        except Exception as e:
            self.log.info("Không chụp được lưới để kiểm tra dữ liệu (%s) → vẫn chép lưới", e)
            return None

    @staticmethod
    def split_oper(df: pd.DataFrame, code: str) -> pd.DataFrame:
        """Chỉ giữ dòng của xưởng `code` — so ĐÚNG mã trước dấu '-' (cột Oper dạng "2010 - SMD"; "2010" ≠ "20101")"""
        if "Oper" not in df.columns:
            return df
        mask = df["Oper"].astype(str).str.split("-").str[0].str.strip() == code
        return df[mask].reset_index(drop=True)

    @staticmethod
    def opers_in(df: pd.DataFrame) -> set[str]:
        """Các mã Oper có trong bảng (phần trước dấu '-')"""
        if "Oper" not in df.columns:
            return set()
        return {str(v).split("-")[0].strip() for v in df["Oper"].dropna()}
