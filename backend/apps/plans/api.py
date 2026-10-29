"""API: file kế hoạch, dữ liệu Work Order"""
from datetime import date

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from apps.core.models import Oper

from .models import ExportFile, ProductionReport, WorkOrder

router = Router(tags=["Plans"])


def _file_out(f: ExportFile) -> dict:
    return {"id": f.id, "run_id": f.run_id, "oper": f.oper.code, "oper_name": f.oper.name,
            "export_date": f.export_date, "file_name": f.file_name, "file_path": f.file_path,
            "rows": f.rows, "size": f.size, "status": f.status, "status_label": f.get_status_display(),
            "created_at": f.created_at}


class FileFilter(Schema):
    oper: str | None = None
    status: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


@router.get("/files")
def list_files(request, f: Query[FileFilter]):
    qs = ExportFile.objects.select_related("oper")
    if f.oper:
        qs = qs.filter(oper__code=f.oper)
    if f.status:
        qs = qs.filter(status=f.status)
    if f.date_from:
        qs = qs.filter(export_date__date__gte=f.date_from)
    if f.date_to:
        qs = qs.filter(export_date__date__lte=f.date_to)
    total = qs.count()
    size = max(1, min(f.page_size, 100))
    start = (max(f.page, 1) - 1) * size
    return {"total": total, "page": f.page, "page_size": size,
            "items": [_file_out(x) for x in qs.order_by("-export_date")[start:start + size]]}


@router.get("/files/{file_id}/download")
def download_file(request, file_id: int):
    ef = get_object_or_404(ExportFile, id=file_id)
    path = settings.STORAGE_DIR / ef.file_path
    if ef.status == ExportFile.Status.DELETED or not path.is_file():
        raise HttpError(404, "File không còn trên ổ đĩa")
    return FileResponse(open(path, "rb"), as_attachment=True,
                        filename=f"{ef.oper.folder}_{ef.file_name}")


@router.delete("/files/{file_id}")
def delete_file(request, file_id: int, with_data: bool = False):
    """Xóa file trên ổ đĩa; with_data=true thì xóa luôn dữ liệu WO trong DB"""
    ef = get_object_or_404(ExportFile, id=file_id)
    path = settings.STORAGE_DIR / ef.file_path
    if path.is_file():
        path.unlink()
    if with_data:
        ef.delete()
        return {"ok": True, "deleted": "file+data"}
    ef.status = ExportFile.Status.DELETED
    ef.save(update_fields=["status"])
    return {"ok": True, "deleted": "file"}


@router.get("/workorders/exports")
def latest_exports(request, oper: str, limit: int = 30):
    """Các lần export gần nhất của 1 xưởng — để chọn snapshot muốn xem"""
    qs = ExportFile.objects.filter(oper__code=oper).order_by("-export_date")[:limit]
    return [{"id": f.id, "export_date": f.export_date, "rows": f.rows} for f in qs]


class WOFilter(Schema):
    oper: str
    export_file_id: int | None = None      # bỏ trống = bản mới nhất
    line: str | None = None
    status: str | None = None
    q: str | None = None
    page: int = 1
    page_size: int = 50


@router.get("/workorders")
def list_workorders(request, f: Query[WOFilter]):
    oper = get_object_or_404(Oper, code=f.oper)
    files = ExportFile.objects.filter(oper=oper)
    ef = files.filter(id=f.export_file_id).first() if f.export_file_id else files.order_by("-export_date").first()
    empty = {"export": None, "total": 0, "page": 1, "page_size": f.page_size, "items": [],
             "lines": [], "statuses": [], "summary": {}}
    if not ef:
        return empty

    base = WorkOrder.objects.filter(export_file=ef)
    qs = base
    if f.line:
        qs = qs.filter(line=f.line)
    if f.status:
        qs = qs.filter(status=f.status)
    if f.q:
        qs = qs.filter(Q(work_order_no__icontains=f.q) | Q(f_code__icontains=f.q) |
                       Q(s_code__icontains=f.q) | Q(material_desc__icontains=f.q))

    total = qs.count()
    size = max(1, min(f.page_size, 200))
    start = (max(f.page, 1) - 1) * size
    items = [{
        "id": w.id, "work_order_no": w.work_order_no, "factory": w.factory, "f_code": w.f_code,
        "material_desc": w.material_desc, "s_code": w.s_code, "spec": w.spec, "line": w.line,
        "real_line": w.real_line, "rework_flag": w.rework_flag, "status": w.status,
        "wo_qty": w.wo_qty, "input_qty": w.input_qty, "remain_qty": w.remain_qty,
        "oper_text": w.oper_text, "array": w.array,
        "start_time": w.start_time, "stop_time": w.stop_time, "end_time": w.end_time,
    } for w in qs.order_by("line", "work_order_no")[start:start + size]]

    # Tóm tắt theo trạng thái cho cả snapshot
    summary = {}
    for st, qty, inp in base.values_list("status", "wo_qty", "input_qty"):
        s = summary.setdefault(st or "(trống)", {"count": 0, "wo_qty": 0, "input_qty": 0})
        s["count"] += 1
        s["wo_qty"] += qty or 0
        s["input_qty"] += inp or 0

    return {
        "export": {"id": ef.id, "export_date": ef.export_date, "file_name": ef.file_name,
                   "rows": ef.rows, "status": ef.status},
        "total": total, "page": f.page, "page_size": size, "items": items,
        "lines": sorted(x for x in base.values_list("line", flat=True).distinct() if x),
        "statuses": sorted(x for x in base.values_list("status", flat=True).distinct() if x),
        "summary": summary,
    }


# ======================= SẢN LƯỢNG =======================
def _report_out(p: ProductionReport) -> dict:
    return {"id": p.id, "run_id": p.run_id, "report_date": p.report_date, "export_date": p.export_date,
            "file_name": p.file_name, "rows": p.rows, "size": p.size, "status": p.status,
            "status_label": p.get_status_display(), "file_deleted": p.file_deleted,
            "download_url": None if p.file_deleted else f"/api/production-reports/{p.id}/download",
            "imported_at": p.imported_at, "imported_by": p.imported_by, "dys_message": p.dys_message,
            "created_at": p.created_at}


class ReportFilter(Schema):
    status: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


@router.get("/production-reports", tags=["Sản lượng"])
def list_production_reports(request, f: Query[ReportFilter]):
    qs = ProductionReport.objects.all()
    if f.status:
        qs = qs.filter(status=f.status)
    if f.date_from:
        qs = qs.filter(report_date__gte=f.date_from)
    if f.date_to:
        qs = qs.filter(report_date__lte=f.date_to)
    total = qs.count()
    size = max(1, min(f.page_size, 100))
    start = (max(f.page, 1) - 1) * size
    return {"total": total, "page": f.page, "page_size": size,
            "items": [_report_out(p) for p in qs.order_by("-report_date", "-id")[start:start + size]]}


@router.get("/production-reports/{report_id}", tags=["Sản lượng"])
def get_production_report(request, report_id: int):
    return _report_out(get_object_or_404(ProductionReport, id=report_id))


@router.get("/production-reports/{report_id}/download", tags=["Sản lượng"])
def download_production_report(request, report_id: int):
    """Tải file .xlsx để DYS gọi POST /api/ProductionResult/import (bằng token người dùng DYS)"""
    p = get_object_or_404(ProductionReport, id=report_id)
    path = settings.STORAGE_DIR / p.file_path
    if p.file_deleted or not path.is_file():
        raise HttpError(404, "File đã được xóa (thường là sau khi DYS import thành công)")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=p.file_name)


class ImportResultIn(Schema):
    success: bool
    message: str = ""          # phản hồi / lỗi từ API import của DYS
    imported_by: str = ""      # username bên DYS (bỏ trống = lấy X-Requested-By / tên mật khẩu API)


@router.post("/production-reports/{report_id}/import-result", tags=["Sản lượng"])
def report_import_result(request, report_id: int, data: ImportResultIn):
    """DYS gọi sau khi import xong để SMS ghi nhận (thành công thì xóa file nếu bật delete_file_after_import)"""
    from apps.core.models import get_setting
    from apps.core.security import requested_by

    from .services import mark_import_result
    p = get_object_or_404(ProductionReport, id=report_id)
    mark_import_result(p, data.success, data.message, data.imported_by or requested_by(request),
                       delete_file=get_setting("delete_file_after_import", True))
    return _report_out(p)
