"""Lưu kết quả lấy từ MES vào bảng files và thư mục storage/ (dữ liệu chi tiết do DYS import)"""
import hashlib
from pathlib import Path
from datetime import datetime

import pandas as pd
from django.conf import settings
from django.utils import timezone

from apps.core.models import Oper

from .models import File

def _write_excel(df: pd.DataFrame, oper: Oper, export_dt: datetime):
    folder = settings.PLAN_DIR / oper.folder
    folder.mkdir(parents=True, exist_ok=True)
    base = export_dt.strftime("%Y-%m-%d_%H%M%S")      # Windows không cho dấu ':' trong tên file
    path, i = folder / f"{base}.xlsx", 1
    while path.exists():
        path, i = folder / f"{base}_{i}.xlsx", i + 1
    info = pd.DataFrame([{"Export Date": export_dt, "Oper": oper.code, "Xưởng": oper.name,
                          "Số dòng": len(df), "Lưu lúc": datetime.now().replace(microsecond=0)}])
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="WorkOrder", index=False)
        info.to_excel(w, sheet_name="Info", index=False)
    return path


def save_export(run, oper: Oper, df: pd.DataFrame, export_dt: datetime) -> File:
    """Lưu file kế hoạch 1 xưởng (chỉ file + 1 dòng `files`, không lưu từng dòng WO).
    export_dt: giờ địa phương (naive) đọc từ dòng 'Export Date' của MES"""
    path = _write_excel(df, oper, export_dt)
    content = path.read_bytes()
    return File.objects.create(
        kind=File.Kind.WORKORDER, run=run, oper=oper,
        export_date=timezone.make_aware(export_dt) if timezone.is_naive(export_dt) else export_dt,
        file_path=str(path.relative_to(settings.STORAGE_DIR)).replace("\\", "/"),
        file_name=path.name, rows=len(df), size=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
    )


# ======================= SẢN LƯỢNG =======================
def production_target(report_date) -> Path:
    """Đường dẫn (chưa có đuôi) để MES lưu file sản lượng: storage/SanLuong/<YYYY-MM-DD>_<HHMMSS>"""
    folder = settings.STORAGE_DIR / "SanLuong"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{report_date:%Y-%m-%d}_{datetime.now():%H%M%S}"


def register_production_file(run, report_date, path: Path, rows: int, export_dt: datetime):
    """Ghi nhận file sản lượng MES đã tự lưu (giữ nguyên file gốc của MES để DYS import)"""
    content = path.read_bytes()
    return File.objects.create(
        kind=File.Kind.PRODUCTION, run=run, report_date=report_date,
        export_date=timezone.make_aware(export_dt) if timezone.is_naive(export_dt) else export_dt,
        file_path=str(path.relative_to(settings.STORAGE_DIR)).replace("\\", "/"),
        file_name=path.name, rows=rows, size=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
    )


def mark_import_result(report, success: bool, message: str = "", imported_by: str = "", delete_file: bool = True):
    """DYS báo kết quả import file (sản lượng / kế hoạch). Thành công + delete_file → xóa file trên máy"""
    report.status = File.Status.IMPORTED if success else File.Status.IMPORT_FAILED
    report.imported_at = timezone.now() if success else None
    report.imported_by = imported_by[:150]
    report.dys_message = message[:5000]
    if success and delete_file:
        delete_file_on_disk(report, save=False)
    report.save()
    return report


def delete_file_on_disk(f: File, save: bool = True):
    """Xóa file trên ổ đĩa, giữ lại dòng trong bảng files (đánh dấu file_deleted)"""
    if not f.file_deleted:
        (settings.STORAGE_DIR / f.file_path).unlink(missing_ok=True)
        f.file_deleted = True
        if save:
            f.save(update_fields=["file_deleted"])
