"""
Đọc dữ liệu từ WPS Spreadsheets / Excel qua COM.
Nút Excel của MESClient KHÔNG lưu file mà mở WPS và ghi dữ liệu vào Workbook mới.
Ta "gắn" vào chính app đó, đọc bảng, rồi đóng workbook không lưu.
"""
import logging
import re
import time
from datetime import datetime

import pandas as pd
import psutil
import pythoncom
import pywintypes
import win32com.client

EXPORT_DATE_RE = re.compile(r"Export\s*Date\s*:\s*(.+)", re.I)
DATE_FORMATS = ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")


from .errors import SheetError

SHEET_PROCESSES = {"et.exe", "wps.exe", "wpsoffice.exe", "excel.exe"}


def _sheet_app_running() -> bool:
    return any((p.info["name"] or "").lower() in SHEET_PROCESSES for p in psutil.process_iter(["name"]))


def com_retry(fn, tries: int = 20, delay: float = 0.5):
    """App bảng tính đang bận (MES còn đang ghi) sẽ từ chối lệnh COM -> thử lại"""
    last = None
    for _ in range(tries):
        try:
            return fn()
        except pywintypes.com_error as e:
            last = e
            time.sleep(delay)
    raise last


def parse_export_date(text: str):
    m = EXPORT_DATE_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def _to_py(v):
    """Chuẩn hóa giá trị COM: datetime COM -> datetime thường, 1300.0 -> 1300"""
    if isinstance(v, datetime):
        return datetime(v.year, v.month, v.day, v.hour, v.minute, v.second)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        return v.strip()
    return v


class SheetBridge:
    def __init__(self, progids, log: logging.Logger):
        pythoncom.CoInitialize()
        self.progids = progids
        self.log = log

    def check_available(self) -> str:
        """Kiểm tra trước khi chạy: máy có WPS/Excel đăng ký COM không"""
        for pid in self.progids:
            try:
                pywintypes.IID(pid)
                return pid
            except pywintypes.com_error:
                pass
        raise SheetError("Máy agent chưa cài WPS Spreadsheets / Excel (không có ProgID COM: "
                         + ", ".join(self.progids) + ")", "SHEET_APP_MISSING")

    def timeout_error(self, what: str, timeout: int) -> SheetError:
        """Phân biệt: WPS có mở nhưng không gắn COM được, hay MES không xuất gì"""
        self._log_running_objects()
        if _sheet_app_running():
            return SheetError(f"{what}: WPS/Excel đang chạy nhưng SMS không đọc được qua COM sau {timeout}s",
                              "SHEET_COM_ATTACH_FAILED")
        return SheetError(f"{what}: sau {timeout}s không thấy MES mở WPS/Excel", "SHEET_EXPORT_TIMEOUT")

    def _apps(self):
        """Lấy các app bảng tính đang chạy (đã đăng ký trong Running Object Table)"""
        apps = []
        for pid in self.progids:
            try:
                apps.append((pid, win32com.client.GetActiveObject(pid)))
            except pywintypes.com_error:
                pass
        return apps

    def wait_for_export(self, title: str, after: datetime, timeout: int, tick=None):
        """Chờ workbook có tiêu đề `title` và Export Date >= `after` xuất hiện"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if tick:
                tick()   # vd: đóng hộp thoại nâng cấp của MES
            for progid, app in self._apps():
                try:
                    wbs = app.Workbooks
                    for i in range(1, wbs.Count + 1):
                        wb = wbs.Item(i)
                        ws = wb.Worksheets.Item(1)
                        top = ws.Range("A1:J3").Value or ()
                        texts = [str(c) for row in top for c in row if c not in (None, "")]
                        if not any(title.lower() in t.lower() for t in texts):
                            continue
                        export_dt = next((d for d in map(parse_export_date, texts) if d), None)
                        if export_dt and export_dt >= after:
                            self.log.info("Bắt được workbook '%s' qua %s, Export Date %s",
                                          wb.Name, progid, export_dt)
                            return wb, ws, export_dt
                except pywintypes.com_error:
                    pass   # app đang bận, vòng sau thử lại
            time.sleep(1)
        raise self.timeout_error(f"Không thấy bảng '{title}' mới", timeout)

    def wait_until_stable(self, ws, checks: int = 3, interval: float = 1.0, timeout: int = 120, tick=None):
        """MES ghi từng ô — chờ tới khi số dòng/cột và dòng cuối không đổi nữa mới đọc"""
        deadline = time.time() + timeout
        last, same = None, 0
        while time.time() < deadline:
            if tick:
                tick()
            try:
                ur = ws.UsedRange
                n_rows, n_cols = ur.Rows.Count, ur.Columns.Count
                last_cell = ws.Cells(ur.Row + n_rows - 1, ur.Column + 2).Value
                sig = (n_rows, n_cols, str(last_cell))
            except pywintypes.com_error:
                sig = None
            if sig is not None and sig == last:
                same += 1
                if same >= checks:
                    self.log.info("Dữ liệu ổn định: %s dòng x %s cột (gồm tiêu đề)", sig[0], sig[1])
                    return
            else:
                same, last = 0, sig
            time.sleep(interval)
        raise SheetError("Dữ liệu trong bảng tính không ổn định (MES vẫn đang ghi?)", "SHEET_DATA_UNSTABLE")

    def read_table(self, ws, header_keys=("Factory", "Work Order")) -> pd.DataFrame:
        """Đọc toàn bộ UsedRange 1 lần, tự tìm dòng tiêu đề, bỏ cột/dòng trống"""
        values = com_retry(lambda: ws.UsedRange.Value)
        rows = [list(r) for r in values]
        hdr_idx = None
        for i, r in enumerate(rows):
            cells = {str(c).strip() for c in r if c not in (None, "")}
            if all(k in cells for k in header_keys):
                hdr_idx = i
                break
        if hdr_idx is None:
            raise SheetError(f"Không tìm thấy dòng tiêu đề chứa {header_keys}", "SHEET_READ_FAILED")

        header = rows[hdr_idx]
        cols = [j for j, c in enumerate(header) if c not in (None, "")]
        names, seen = [], {}
        for j in cols:   # tránh trùng tên cột
            n = str(header[j]).strip()
            seen[n] = seen.get(n, 0) + 1
            names.append(n if seen[n] == 1 else f"{n}_{seen[n]}")

        data = []
        for r in rows[hdr_idx + 1:]:
            vals = [_to_py(r[j]) for j in cols]
            if all(v in (None, "") for v in vals):
                continue
            data.append(vals)
        return pd.DataFrame(data, columns=names)

    def close(self, wb):
        name = wb.Name
        com_retry(lambda: wb.Close(False))   # False = không lưu
        self.log.info("Đã đóng workbook %s (không lưu)", name)

    def _log_running_objects(self):
        """Debug: liệt kê mọi object COM đang đăng ký — giúp tìm ProgID đúng của WPS"""
        try:
            rot = pythoncom.GetRunningObjectTable()
            ctx = pythoncom.CreateBindCtx(0)
            enum, names = rot.EnumRunning(), []
            while True:
                ms = enum.Next(1)
                if not ms:
                    break
                names.append(ms[0].GetDisplayName(ctx, None))
            self.log.warning("Running Object Table: %s", names)
            self.log.warning("App COM gắn được: %s", [p for p, _ in self._apps()])
        except Exception as e:
            self.log.warning("Không đọc được ROT: %s", e)


# ---------- Dùng cho form không có dòng "Export Date" (vd: sản lượng) ----------
def _workbooks(bridge: "SheetBridge") -> dict:
    res = {}
    for _, app in bridge._apps():
        try:
            for i in range(1, app.Workbooks.Count + 1):
                wb = app.Workbooks.Item(i)
                res[wb.FullName] = wb
        except pywintypes.com_error:
            pass
    return res


def snapshot(bridge: "SheetBridge") -> set:
    """Danh sách workbook đang mở TRƯỚC khi bấm Excel"""
    return set(_workbooks(bridge))


def wait_for_new(bridge: "SheetBridge", before: set, timeout: int, tick=None):
    """Chờ workbook mới (không có trong `before`) xuất hiện"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if tick:
            tick()
        new = {k: v for k, v in _workbooks(bridge).items() if k not in before}
        if new:
            name, wb = next(iter(new.items()))
            bridge.log.info("Bắt được workbook mới: %s", wb.Name)
            return wb, wb.Worksheets.Item(1)
        time.sleep(1)
    raise bridge.timeout_error("Không thấy bảng mới sau khi bấm Excel", timeout)


def read_grid(ws) -> list[list]:
    """Đọc nguyên UsedRange (giữ đúng vị trí ô, kể cả dòng tiêu đề) — chuẩn hóa giá trị COM"""
    values = com_retry(lambda: ws.UsedRange.Value)
    if not isinstance(values, tuple):
        values = ((values,),)
    first_row, first_col = com_retry(lambda: (ws.UsedRange.Row, ws.UsedRange.Column))
    grid = [[None] * (first_col - 1) + [_to_py(c) for c in row] for row in values]
    return [[None] * len(grid[0])] * (first_row - 1) + grid if grid else []
