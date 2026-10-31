"""Lưu kết quả lấy từ MES vào bảng files và thư mục storage/ (dữ liệu chi tiết do DYS import)"""
import hashlib
from pathlib import Path
from datetime import datetime

import pandas as pd
from django.conf import settings
from django.utils import timezone

from apps.core.models import Oper, Plant

from .models import File

def _write_excel(df: pd.DataFrame, plant: Plant, oper: Oper, export_dt: datetime, d1, d2):
    """storage/KeHoach/<PLANT>/<XƯỞNG>/<từ ngày>-<đến ngày>_<YYYY-MM-DD_HHMMSS>.xlsx (sheet WorkOrder + Info)"""
    ensure_storage_marker()
    folder = settings.PLAN_DIR / plant.code / oper.folder
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{d1:%Y%m%d}-{d2:%Y%m%d}_{export_dt:%Y-%m-%d_%H%M%S}"   # Windows không cho dấu ':' trong tên file
    path, i = folder / f"{base}.xlsx", 1
    while path.exists():
        path, i = folder / f"{base}_{i}.xlsx", i + 1
    info = pd.DataFrame([{"Export Date": export_dt, "Plant": plant.code, "Tên plant": plant.name,
                          "Period từ": d1, "Period đến": d2, "Oper": oper.code, "Xưởng": oper.name,
                          "Số dòng": len(df), "Lưu lúc": datetime.now().replace(microsecond=0)}])
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="WorkOrder", index=False)
        info.to_excel(w, sheet_name="Info", index=False)
    return path


def save_export(run, plant: Plant, oper: Oper, df: pd.DataFrame, export_dt: datetime, d1, d2) -> File:
    """Lưu file kế hoạch của 1 plant + 1 xưởng trong kỳ d1..d2 (chỉ file + 1 dòng `files`, không lưu từng dòng WO).
    export_dt: giờ địa phương (naive) đọc từ dòng 'Export Date' của MES"""
    path = _write_excel(df, plant, oper, export_dt, d1, d2)
    content = path.read_bytes()
    return File.objects.create(
        kind=File.Kind.WORKORDER, run=run, plant=plant, oper=oper, period_from=d1, period_to=d2,
        export_date=timezone.make_aware(export_dt) if timezone.is_naive(export_dt) else export_dt,
        file_path=str(path.relative_to(settings.STORAGE_DIR)).replace("\\", "/"),
        file_name=path.name, rows=len(df), size=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
    )


# ======================= SẢN LƯỢNG =======================
def production_target(report_date) -> Path:
    """Đường dẫn (chưa có đuôi) để MES lưu file sản lượng: storage/SanLuong/<YYYY-MM-DD>_<HHMMSS>"""
    ensure_storage_marker()
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


def mark_import_result(report, success: bool, message: str = "", imported_by: str = "", delete_file: bool = True) -> bool:
    """
    DYS báo kết quả import file (sản lượng / kế hoạch).
    Thành công + delete_file → xóa file trên máy VÀ dòng trong bảng files. Trả về True nếu đã xóa.
    """
    report.status = File.Status.IMPORTED if success else File.Status.IMPORT_FAILED
    report.imported_at = timezone.now() if success else None
    report.imported_by = imported_by[:150]
    report.dys_message = message[:5000]
    if success:
        record_import_history(report)       # ghi lịch sử TRƯỚC khi xóa file + dòng files
    if success and delete_file:
        delete_file_and_row(report)
        return True
    report.save()
    return False


def record_import_history(f: File):
    """Ghi 1 dòng import_dys_history cho file vừa vào DYS thành công (không làm hỏng lần import nếu ghi lỗi)"""
    from .models import ImportDysHistory
    try:
        ImportDysHistory.objects.create(
            kind=f.kind, run_id=f.run_id, file_id=f.pk, plant_id=f.plant_id, oper_id=f.oper_id,
            plant_code=f.plant.code if f.plant_id else "", oper_code=f.oper.code if f.oper_id else "",
            oper_name=f.oper.name if f.oper_id else "", report_date=f.report_date,
            period_from=f.period_from, period_to=f.period_to, file_name=f.file_name, rows=f.rows,
            export_date=f.export_date, imported_at=f.imported_at or timezone.now(),
            imported_by=f.imported_by, dys_message=f.dys_message)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Không ghi được import_dys_history cho file %s", f.pk)


def delete_file_and_row(f: File):
    """Xóa file trên ổ đĩa rồi xóa luôn dòng trong bảng files (file không còn thì dòng cũng không giữ lại)"""
    (settings.STORAGE_DIR / f.file_path).unlink(missing_ok=True)
    if f.pk:
        File.objects.filter(pk=f.pk).delete()


STORAGE_MARKER = ".sms_storage"


def ensure_storage_marker():
    """
    Worker ghi file đánh dấu vào thư mục storage nó đang dùng. API (Docker) chỉ dọn bảng files khi thấy file này
    → nếu volume Docker gắn sai thư mục (thấy thư mục trống) thì không xóa nhầm toàn bộ dòng.
    """
    try:
        settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        marker = settings.STORAGE_DIR / STORAGE_MARKER
        if not marker.exists():
            marker.write_text("Thư mục storage của SMS worker — đừng xóa file này.\n", encoding="utf-8")
    except OSError:
        pass


def purge_missing_files(qs=None) -> int:
    """
    Xóa các dòng `files` mà file trên ổ đĩa không còn (bị xóa tay khỏi storage, đã import xong, quá hạn...)
    hoặc đã đánh dấu file_deleted (dữ liệu cũ trước khi đổi cách làm). Trả về số dòng đã xóa.
    An toàn: không thấy file đánh dấu .sms_storage (ổ chưa gắn, Docker gắn sai volume) → không xóa gì.
    """
    root = settings.STORAGE_DIR
    if not (root / STORAGE_MARKER).is_file():       # storage chưa gắn / gắn sai thư mục → không đụng tới bảng
        return 0
    qs = File.objects.all() if qs is None else qs
    gone = [f.id for f in qs.only("id", "file_path", "file_deleted")
            if f.file_deleted or not (root / f.file_path).is_file()]
    if gone:
        File.objects.filter(id__in=gone).delete()
    return len(gone)
