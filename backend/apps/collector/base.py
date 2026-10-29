"""Phần chung của mọi agent thao tác MES: kiểm tra WPS, mở/kết nối MESClient, tự đăng nhập, đóng popup"""
import logging
from pathlib import Path

from . import winutil
from .mes_client import MesClient
from .sheet_bridge import SheetBridge

# ProgID COM của app bảng tính (WPS Spreadsheets đăng ký "Ket.Application")
SPREADSHEET_PROGIDS = ("Ket.Application", "Excel.Application", "KET.Application", "ET.Application")

DEFAULT_CONFIG = {
    "view_wait": 8,                 # chờ MES tải dữ liệu sau khi bấm View (giây)
    "export_timeout": 90,           # chờ WPS nhận dữ liệu sau khi bấm Excel (giây)
    "auto_dismiss_upgrade": True,   # tự bấm No ở hộp thoại hỏi nâng cấp
    "exe_path": "",                 # đường dẫn MESClient.exe để tự mở
    "auto_start": True,
    "start_timeout": 90,
    "user_id": "",                  # tài khoản MES để tự đăng nhập
    "password": "",
    "login_timeout": 60,
}


class BaseCollector:
    def __init__(self, log: logging.Logger, config: dict | None = None):
        winutil.enable_dpi_awareness()
        self.log = log
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.mes = MesClient(log, self.cfg["auto_dismiss_upgrade"])
        self.sheets = SheetBridge(SPREADSHEET_PROGIDS, log)
        self.form = None

    def prepare(self):
        """Bước chuẩn bị chung — lỗi ở đây có mã lỗi rõ ràng (SHEET_APP_MISSING, MES_NOT_RUNNING, MES_LOGIN_*...)"""
        progid = self.sheets.check_available()
        self.log.info("Máy agent có bảng tính COM: %s", progid)
        self.mes.connect(self.cfg["exe_path"], self.cfg["auto_start"], self.cfg["start_timeout"])
        self.mes.handle_popup()
        self.mes.ensure_logged_in(self.cfg["user_id"], self.cfg["password"], self.cfg["login_timeout"])
        self.mes.handle_popup()

    def screenshot(self, path: Path):
        self.mes.screenshot(path)
