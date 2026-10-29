"""Lưu kết quả lấy từ MES: file Excel + bảng ExportFile + bảng WorkOrder"""
import hashlib
from pathlib import Path
from datetime import datetime

import pandas as pd
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.models import Oper

from .models import ExportFile, WorkOrder

# Tên cột trên MES -> tên trường trong bảng WorkOrder
COLUMN_MAP = {
    "Factory": "factory",
    "Work Order": "work_order_no",
    "F Code": "f_code",
    "Material Desc": "material_desc",
    "S Code": "s_code",
    "SPEC": "spec",
    "Line": "line",
    "REAL Line": "real_line",
    "Rework Flag": "rework_flag",
    "Work Order Status": "status",
    "Work Order Qty": "wo_qty",
    "Input Qty": "input_qty",
    "Remain Qty": "remain_qty",
    "Oper": "oper_text",
    "Array": "array",
    "Start Time": "start_time",
    "Stop Time": "stop_time",
    "End Time": "end_time",
}
INT_FIELDS = {"wo_qty", "input_qty", "remain_qty", "array"}
DT_FIELDS = {"start_time", "stop_time", "end_time"}


def _to_int(v):
    try:
        return None if v in (None, "") else int(float(str(v).replace(",", "")))
    except ValueError:
        return None


def _to_dt(v):
    if v in (None, ""):
        return None
    try:
        dt = v if isinstance(v, datetime) else pd.to_datetime(str(v)).to_pydatetime()
    except (ValueError, TypeError):
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _to_json(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if v is None or isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


def _row_to_workorder(row: dict, ef: ExportFile) -> WorkOrder:
    data, extra = {}, {}
    for col, val in row.items():
        field = COLUMN_MAP.get(col)
        if field is None:
            if val not in (None, ""):
                extra[col] = _to_json(val)
        elif field in INT_FIELDS:
            data[field] = _to_int(val)
        elif field in DT_FIELDS:
            data[field] = _to_dt(val)
        else:
            max_len = WorkOrder._meta.get_field(field).max_length
            data[field] = ("" if val is None else str(val))[:max_len]
    return WorkOrder(export_file=ef, oper=ef.oper, extra=extra, **data)


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


def save_export(run, oper: Oper, df: pd.DataFrame, export_dt: datetime) -> ExportFile:
    """export_dt: giờ địa phương (naive) đọc từ dòng 'Export Date' của MES"""
    path = _write_excel(df, oper, export_dt)
    content = path.read_bytes()
    with transaction.atomic():
        ef = ExportFile.objects.create(
            run=run, oper=oper,
            export_date=timezone.make_aware(export_dt) if timezone.is_naive(export_dt) else export_dt,
            file_path=str(path.relative_to(settings.STORAGE_DIR)).replace("\\", "/"),
            file_name=path.name, rows=len(df), size=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
        )
        records = df.astype(object).where(pd.notna(df), None).to_dict("records")
        WorkOrder.objects.bulk_create([_row_to_workorder(r, ef) for r in records], batch_size=500)
    return ef


# ======================= SẢN LƯỢNG =======================
def production_target(report_date) -> Path:
    """Đường dẫn (chưa có đuôi) để MES lưu file sản lượng: storage/SanLuong/<YYYY-MM-DD>_<HHMMSS>"""
    folder = settings.STORAGE_DIR / "SanLuong"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{report_date:%Y-%m-%d}_{datetime.now():%H%M%S}"


def register_production_file(run, report_date, path: Path, rows: int, export_dt: datetime):
    """Ghi nhận file sản lượng MES đã tự lưu (giữ nguyên file gốc của MES để DYS import)"""
    from .models import ProductionReport

    content = path.read_bytes()
    return ProductionReport.objects.create(
        run=run, report_date=report_date,
        export_date=timezone.make_aware(export_dt) if timezone.is_naive(export_dt) else export_dt,
        file_path=str(path.relative_to(settings.STORAGE_DIR)).replace("\\", "/"),
        file_name=path.name, rows=rows, size=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
    )


def mark_import_result(report, success: bool, message: str = "", imported_by: str = "", delete_file: bool = True):
    """DYS báo kết quả import file sản lượng. Thành công + delete_file → xóa file trên máy"""
    from .models import ProductionReport

    report.status = ProductionReport.Status.IMPORTED if success else ProductionReport.Status.IMPORT_FAILED
    report.imported_at = timezone.now() if success else None
    report.imported_by = imported_by[:150]
    report.dys_message = message[:5000]
    if success and delete_file and not report.file_deleted:
        (settings.STORAGE_DIR / report.file_path).unlink(missing_ok=True)
        report.file_deleted = True
    report.save()
    return report
