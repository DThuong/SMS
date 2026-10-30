"""
Lấy sản lượng theo giờ: Inquiry → 타임별 실적 조회(TV) (form "Product Time Confirm (TV)", AutomationId frmBASViewFlexibleInquiryMenu)
  → FROM_DATE = ngày cần lấy → View → nút Excel → hộp thoại Save As → lưu thẳng file của MES vào thư mục SanLuong
"""
import re
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import win32con
import win32gui
from pywinauto.controls.hwndwrapper import HwndWrapper

from . import winutil
from .base import BaseCollector
from .errors import MesError

MENU_PRODUCTION_TV = ["Inquiry", "타임별 실적 조회(TV)"]
FORM_TITLE = "Product Time Confirm (TV)"
DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _parse_mdy(text: str) -> date | None:
    m = DATE_RE.search(text or "")
    return date(int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None


def _children(h: int) -> list[int]:
    res = []
    try:
        win32gui.EnumChildWindows(h, lambda c, _: res.append(c), None)
    except Exception:
        pass
    return res


def count_data_rows(path: Path) -> int:
    """Đếm số dòng dữ liệu trong file MES xuất (bỏ dòng tiêu đề và dòng trống)"""
    df = None
    for _ in range(5):                      # MES vừa ghi xong có thể còn giữ file vài giây
        try:
            df = pd.read_excel(path, header=None)
            break
        except PermissionError:
            time.sleep(1)
        except Exception:
            return 0
    if df is None:
        return 0
    df = df.dropna(how="all")
    return max(len(df) - 1, 0)


class ProductionCollector(BaseCollector):
    def open(self, fresh: bool = False):
        """
        Dùng lại form đang mở (FROM_DATE luôn được đặt lại nên không cần mở mới).
        KHÔNG đóng rồi mở lại ngay: MESClient hay lỗi ".NET – Cannot access a disposed object" khi làm vậy.
        """
        self.prepare()
        if fresh:
            self.mes.close_form(FORM_TITLE, by="title")
            self.mes.wait(3)
        self.form = self.mes.open_form(FORM_TITLE, MENU_PRODUCTION_TV, by="title")

    def set_from_date(self, d: date):
        """
        Ô FROM_DATE nằm trong lưới FarPoint (spdInquiryCondition1). Form mở mới mặc định lùi 7 ngày → luôn phải đặt.
        Cách làm (không dùng chuột/bàn phím thật):
          1. Gửi phím mũi tên vào lưới để đứng ở ô giá trị (dòng 1, cột 2)
          2. Gửi F2 → FarPoint tạo ô sửa (một cửa sổ con)
          3. Ghi ngày vào ô sửa bằng WM_SETTEXT, đọc lại để kiểm tra, gửi Enter để chốt
        """
        target = f"{d.month:02d}/{d.day:02d}/{d.year}"
        spd = self.mes._control(self.form, "lưới điều kiện (FROM_DATE)", auto_id="spdInquiryCondition1")
        h = spd.handle
        before = {c for c in _children(h) if win32gui.IsWindowVisible(c)}

        winutil.post_keys(h, [win32con.VK_UP] * 3 + [win32con.VK_LEFT] * 3 + [win32con.VK_RIGHT])
        time.sleep(0.4)
        winutil.post_keys(h, [win32con.VK_F2])
        time.sleep(0.8)

        editors = [c for c in _children(h) if win32gui.IsWindowVisible(c) and c not in before]
        if not editors:
            raise MesError("Gửi F2 nhưng lưới FROM_DATE không mở ô sửa (FarPoint). Chạy `mes_diag date` để kiểm tra",
                           "MES_DATE_SET_FAILED")
        classes = [win32gui.GetClassName(c) for c in editors]
        edit = next((c for c in editors if "EDIT" in win32gui.GetClassName(c).upper()), None)
        if edit is None:   # ô sửa là khung chứa, ô nhập chữ nằm bên trong
            inner = [c for e in editors for c in _children(e) if "EDIT" in win32gui.GetClassName(c).upper()]
            edit = inner[0] if inner else editors[0]

        winutil.set_text(edit, target)
        time.sleep(0.4)
        value = HwndWrapper(edit).window_text()
        if _parse_mdy(value) != d:
            raise MesError(f"Đặt FROM_DATE thất bại: muốn {target}, ô sửa đang là '{value}' (loại ô: {classes})",
                           "MES_DATE_SET_FAILED")
        winutil.post_keys(edit, [win32con.VK_RETURN])
        time.sleep(0.6)
        self.log.info("Đặt FROM_DATE = %s", target)

    def collect(self, report_date: date, target: Path) -> tuple[Path, int, datetime]:
        """target: đường dẫn KHÔNG đuôi, vd .../SanLuong/2026-10-28_080012 → trả (file đã lưu, số dòng, thời điểm)"""
        if self.form is None:
            self.open()
        self.set_from_date(report_date)

        self.mes.click(self.form, "btnView")
        self.log.info("Chờ MES tải dữ liệu %ss...", self.cfg["view_wait"])
        self.mes.wait(self.cfg["view_wait"])

        before = self.mes.dialogs()
        export_dt = datetime.now().replace(microsecond=0)
        self.mes.click(self.form, "btnExcel")
        dlg = self.mes.wait_save_dialog(before, timeout=30)
        self.log.info("MES mở hộp thoại lưu file")
        path = self.mes.save_via_dialog(dlg, target, timeout=self.cfg["export_timeout"])
        self.mes.wait(2)   # đóng thông báo "xuất xong" nếu MES hiện
        rows = count_data_rows(path)
        self.log.info("File %s có %d dòng dữ liệu", path.name, rows)
        return path, rows, export_dt
