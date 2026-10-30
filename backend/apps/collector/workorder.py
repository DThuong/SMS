"""
Lấy kế hoạch View Work Order từ MESClient theo từng Oper.
Luồng: menu → form View Work Order → điền Oper → View → Excel (MES mở WPS)
       → đọc bảng qua COM → đóng workbook không lưu → trả DataFrame + Export Date
Bộ lọc Oper trên MES không có tác dụng (bảng xuất ra có mọi xưởng) → xuất 1 lần, tách theo cột Oper cho từng xưởng.
"""
from datetime import datetime, timedelta

import pandas as pd

from .base import BaseCollector

MENU_VIEW_WORK_ORDER = [
    "[PM]Production Management",
    "Process Control",
    "Work Order Management",
    "View Work Order",
]
FORM_VIEW_WORK_ORDER = "frmCWIPViewWorkOrderList"   # AutomationId của form
EXPORT_TITLE = "View Work Order"                     # tiêu đề ô A1 trong bảng xuất
MIN_EXPORT_TIMEOUT = 240                             # giây chờ WPS nhận dữ liệu (xuất cả nhà máy ~90s)


class WorkOrderCollector(BaseCollector):
    def open(self):
        self.prepare()
        self.form = self.mes.open_form(FORM_VIEW_WORK_ORDER, MENU_VIEW_WORK_ORDER)
        self.log.info("Period: %s", self.mes.read_period(self.form))

    def export_all(self, code: str) -> tuple[pd.DataFrame, datetime]:
        """
        Điền Oper → View → Excel → đọc TOÀN BỘ bảng MES xuất ra (chưa lọc).
        Thực tế bộ lọc Oper của MES không có tác dụng (xuất cả nhà máy, ~90s) → bảng này dùng chung cho mọi xưởng.
        """
        if self.form is None:
            self.open()
        self.log.info("----- Xuất Work Order (Oper %s) -----", code)
        self.mes.set_code_view(self.form, "cdvOper", code)

        self.mes.click(self.form, "btnListView")
        self.log.info("Chờ MES tải dữ liệu %ss...", self.cfg["view_wait"])
        self.mes.wait(self.cfg["view_wait"])

        self.sheets.close_stale(EXPORT_TITLE)
        before = self.sheets.book_names()
        clicked_at = datetime.now().replace(microsecond=0) - timedelta(seconds=2)
        self.mes.click(self.form, "btnExcel")
        # MES đổ ~800 dòng x 255 cột sang WPS mất ~90s → chờ tối thiểu 240s
        timeout = max(int(self.cfg["export_timeout"]), MIN_EXPORT_TIMEOUT)
        self.log.info("Chờ WPS nhận dữ liệu (tối đa %ss)...", timeout)
        tick = self.mes.handle_popup
        wb, ws, export_dt = self.sheets.wait_for_export(EXPORT_TITLE, clicked_at, timeout, tick, before=before)
        self.sheets.wait_until_stable(ws, tick=tick, timeout=timeout)   # MES ghi ~90s+ → không dùng mặc định 120s
        df = self.sheets.read_table(ws)
        self.sheets.close(wb)
        self.log.info("Đọc được %d dòng, %d cột", len(df), len(df.columns))
        return df, export_dt

    @staticmethod
    def split_oper(df: pd.DataFrame, code: str) -> pd.DataFrame:
        """Chỉ giữ dòng của xưởng `code` (cột Oper dạng "2010 - SMD")"""
        if "Oper" not in df.columns:
            return df
        mask = df["Oper"].astype(str).str.strip().str.startswith(code)
        return df[mask].reset_index(drop=True)

    @staticmethod
    def opers_in(df: pd.DataFrame) -> set[str]:
        """Các mã Oper có trong bảng (phần trước dấu '-')"""
        if "Oper" not in df.columns:
            return set()
        return {str(v).split("-")[0].strip() for v in df["Oper"].dropna()}
