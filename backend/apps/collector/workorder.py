"""
Lấy kế hoạch View Work Order từ MESClient.
Luồng (đúng như thao tác tay):
  menu [PM]Production Management → Process Control → Work Order Management → View Work Order
  → Plant: chọn mã (vd PL20 TV BOARD PLANT) qua ô Plant / nút "..."
  → Period: ô chọn kiểu kỳ = "Period" → đặt từ ngày ~ đến ngày (ô ngày hiển thị MM/DD/YYYY)
  → Oper: chọn mã (2010 SMD, 3010 MAIN...) qua ô Oper / nút "..."
  → View → Excel (MES đổ dữ liệu sang WPS) → đọc bảng qua COM → đóng workbook không lưu → trả DataFrame
Sau khi đọc vẫn lọc lại theo cột Oper cho chắc (phòng khi MES trả lẫn xưởng khác).
"""
import time
from datetime import date, datetime, timedelta

import pandas as pd

from .base import BaseCollector
from .errors import MesError

MENU_VIEW_WORK_ORDER = [
    "[PM]Production Management",
    "Process Control",
    "Work Order Management",
    "View Work Order",
]
FORM_VIEW_WORK_ORDER = "frmCWIPViewWorkOrderList"   # AutomationId của form
EXPORT_TITLE = "View Work Order"                     # tiêu đề ô A1 trong bảng xuất
MIN_EXPORT_TIMEOUT = 240                             # giây chờ WPS nhận dữ liệu (xuất cả nhà máy ~90s)

# AutomationId các ô điều kiện. Ô Plant chưa khảo sát → thử lần lượt, không thấy thì dò ô cdv* đang chứa "PL.."
PLANT_CDV = ["cdvPlant", "cdvFactory", "cdvPlantID", "cdvPlantCode", "cdvFac"]
OPER_CDV = ["cdvOper"]
PERIOD_ITEM = "Period"                               # mục cuối của ô chọn kỳ (Curr.D, Prev.D, ..., Period)
FROM_DATE, TO_DATE = "dtpFromDate", "dtpToDate"


class _NoData(Exception):
    """MES báo không có dữ liệu trong lúc chờ xuất Excel"""


class WorkOrderCollector(BaseCollector):
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
        """
        Đặt điều kiện → View → (chờ MES tải xong) → Excel → đọc TOÀN BỘ bảng MES xuất ra (chưa lọc lại theo Oper).
        MES báo "không có dữ liệu" → trả bảng rỗng (0 dòng), không phải lỗi.
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
            return self._empty(), datetime.now().replace(microsecond=0)

        self.sheets.close_stale(EXPORT_TITLE)
        before = self.sheets.book_names()
        clicked_at = datetime.now().replace(microsecond=0) - timedelta(seconds=2)
        self.mes.click(self.form, "btnExcel")
        # MES đổ ~800 dòng x 255 cột sang WPS mất ~90s → chờ tối thiểu 240s
        timeout = max(int(self.cfg["export_timeout"]), MIN_EXPORT_TIMEOUT)
        self.log.info("Chờ WPS nhận dữ liệu (tối đa %ss)...", timeout)

        def tick():
            self.mes.handle_popup()
            if self.mes.no_data_message:                  # MES báo không có gì để xuất
                raise _NoData()

        try:
            wb, ws, export_dt = self.sheets.wait_for_export(EXPORT_TITLE, clicked_at, timeout, tick, before=before)
        except _NoData:
            return self._empty(), datetime.now().replace(microsecond=0)
        self.sheets.wait_until_stable(ws, tick=self.mes.handle_popup, timeout=timeout)
        df = self.sheets.read_table(ws)
        self.sheets.close(wb)
        self.log.info("Đọc được %d dòng, %d cột", len(df), len(df.columns))
        return df, export_dt

    def _empty(self) -> pd.DataFrame:
        self.log.info("MES không có Work Order nào với điều kiện này → lưu file 0 dòng")
        return pd.DataFrame(columns=["Factory", "Work Order", "Oper"])

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
