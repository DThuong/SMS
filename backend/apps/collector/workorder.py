"""
Lấy kế hoạch View Work Order từ MESClient theo từng Oper.
Luồng: menu → form View Work Order → điền Oper → View → Excel (MES mở WPS)
       → đọc bảng qua COM → đóng workbook không lưu → trả DataFrame + Export Date
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


class WorkOrderCollector(BaseCollector):
    def open(self):
        self.prepare()
        self.form = self.mes.open_form(FORM_VIEW_WORK_ORDER, MENU_VIEW_WORK_ORDER)
        self.log.info("Period: %s", self.mes.read_period(self.form))

    def export_oper(self, code: str) -> tuple[pd.DataFrame, datetime]:
        if self.form is None:
            self.open()
        self.log.info("----- Oper %s -----", code)
        self.mes.set_code_view(self.form, "cdvOper", code)

        self.mes.click(self.form, "btnListView")
        self.log.info("Chờ MES tải dữ liệu %ss...", self.cfg["view_wait"])
        self.mes.wait(self.cfg["view_wait"])

        clicked_at = datetime.now().replace(microsecond=0) - timedelta(seconds=2)
        self.mes.click(self.form, "btnExcel")
        tick = self.mes.handle_popup
        wb, ws, export_dt = self.sheets.wait_for_export(EXPORT_TITLE, clicked_at, self.cfg["export_timeout"], tick)
        self.sheets.wait_until_stable(ws, tick=tick)
        df = self.sheets.read_table(ws)
        self.sheets.close(wb)
        self.log.info("Đọc được %d dòng, %d cột", len(df), len(df.columns))

        # Chốt an toàn: chỉ giữ dòng đúng Oper (cột Oper dạng "2010 - SMD")
        if "Oper" in df.columns:
            mask = df["Oper"].astype(str).str.strip().str.startswith(code)
            other = int((~mask).sum())
            if other:
                self.log.warning("Có %d dòng khác Oper %s — đã loại bỏ (kiểm tra bộ lọc Oper trên MES)", other, code)
                df = df[mask].reset_index(drop=True)
        return df, export_dt
