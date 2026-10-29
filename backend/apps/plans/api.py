"""
API: file lấy từ MES (bảng files) và dữ liệu Work Order (bảng work_orders)

  GET    /api/files                     danh sách file (lọc kind=workorder|production, oper, report_date...)
  GET    /api/files/{id}                chi tiết 1 file
  GET    /api/files/{id}/download       tải file .xlsx
  POST   /api/files/{id}/import-result  DYS báo kết quả import file sản lượng
  DELETE /api/files/{id}                xóa file trên ổ đĩa (with_data=true: xóa luôn dòng + work_orders)
  GET    /api/workorders                các dòng Work Order của 1 xưởng (bản mới nhất hoặc file_id)
"""
from datetime import date

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from apps.core.models import Oper

from .models import File, WorkOrder

router = Router()


def file_out(f: File) -> dict:
    """Một file trả về cho DYS — dùng chung cho /files và /runs/{id}"""
    return {
        "id": f.id, "kind": f.kind, "kind_label": f.get_kind_display(), "run_id": f.run_id,
        "oper": f.oper.code if f.oper_id else None, "oper_name": f.oper.name if f.oper_id else None,
        "report_date": f.report_date, "export_date": f.export_date,
        "file_name": f.file_name, "rows": f.rows, "size": f.size,
        "status": f.status, "status_label": f.get_status_display(), "file_deleted": f.file_deleted,
        "download_url": None if f.file_deleted else f"/api/files/{f.id}/download",
        "imported_at": f.imported_at, "imported_by": f.imported_by, "dys_message": f.dys_message,
        "created_at": f.created_at,
    }


def _page(qs, page: int, page_size: int, out, max_size: int = 100) -> dict:
    total = qs.count()
    size = max(1, min(page_size, max_size))
    page = max(page, 1)
    start = (page - 1) * size
    return {"total": total, "page": page, "page_size": size, "items": [out(x) for x in qs[start:start + size]]}


# ======================= FILE =======================
class FileFilter(Schema):
    kind: File.Kind | None = None       # workorder | production
    oper: str | None = None             # mã xưởng (file workorder)
    report_date: date | None = None     # ngày sản lượng (file production)
    status: File.Status | None = None
    date_from: date | None = None       # theo export_date
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


@router.get("/files", tags=["File"])
def list_files(request, f: Query[FileFilter]):
    qs = File.objects.select_related("oper")
    if f.kind:
        qs = qs.filter(kind=f.kind)
    if f.oper:
        qs = qs.filter(oper__code=f.oper)
    if f.report_date:
        qs = qs.filter(report_date=f.report_date)
    if f.status:
        qs = qs.filter(status=f.status)
    if f.date_from:
        qs = qs.filter(export_date__date__gte=f.date_from)
    if f.date_to:
        qs = qs.filter(export_date__date__lte=f.date_to)
    return _page(qs.order_by("-export_date", "-id"), f.page, f.page_size, file_out)


@router.get("/files/{file_id}", tags=["File"])
def get_file(request, file_id: int):
    return file_out(get_object_or_404(File.objects.select_related("oper"), id=file_id))


@router.get("/files/{file_id}/download", tags=["File"])
def download_file(request, file_id: int):
    """Tải file .xlsx (file sản lượng: DYS dùng để gọi POST /api/ProductionResult/import của DYS)"""
    f = get_object_or_404(File.objects.select_related("oper"), id=file_id)
    path = settings.STORAGE_DIR / f.file_path
    if f.file_deleted or not path.is_file():
        raise HttpError(404, "File không còn trên ổ đĩa (đã import xong hoặc quá hạn lưu)")
    name = f"{f.oper.folder}_{f.file_name}" if f.oper_id else f.file_name
    return FileResponse(open(path, "rb"), as_attachment=True, filename=name)


class ImportResultIn(Schema):
    success: bool
    message: str = ""          # phản hồi / lỗi từ API import của DYS
    imported_by: str = ""      # username bên DYS (bỏ trống = lấy X-Requested-By / tên API key)


@router.post("/files/{file_id}/import-result", tags=["File"])
def import_result(request, file_id: int, data: ImportResultIn):
    """DYS báo kết quả import file sản lượng → thành công thì xóa file nếu bật delete_file_after_import"""
    from apps.core.models import get_setting
    from apps.core.security import requested_by

    from .services import mark_import_result

    f = get_object_or_404(File, id=file_id)
    if f.kind != File.Kind.PRODUCTION:
        raise HttpError(400, "Chỉ file sản lượng (kind=production) mới có kết quả import")
    mark_import_result(f, data.success, data.message, data.imported_by or requested_by(request),
                       delete_file=get_setting("delete_file_after_import", True))
    return file_out(f)


@router.delete("/files/{file_id}", tags=["File"])
def delete_file(request, file_id: int, with_data: bool = False):
    """Xóa file trên ổ đĩa; with_data=true thì xóa luôn dòng trong bảng files (+ các dòng work_orders)"""
    from .services import delete_file_on_disk

    f = get_object_or_404(File, id=file_id)
    delete_file_on_disk(f)
    if with_data:
        f.delete()
        return {"ok": True, "deleted": "file+data"}
    return {"ok": True, "deleted": "file"}


# ======================= WORK ORDER =======================
class WOFilter(Schema):
    oper: str
    file_id: int | None = None      # bỏ trống = file mới nhất của xưởng (xem các bản cũ: GET /files?kind=workorder&oper=)
    line: str | None = None
    status: str | None = None
    q: str | None = None
    page: int = 1
    page_size: int = 50             # tối đa 1000 — DYS lấy hết 1 xưởng thì đặt 1000 và lặp page tới khi đủ total


def _wo_out(w: WorkOrder) -> dict:
    return {
        "id": w.id, "work_order_no": w.work_order_no, "factory": w.factory, "f_code": w.f_code,
        "material_desc": w.material_desc, "s_code": w.s_code, "spec": w.spec, "line": w.line,
        "real_line": w.real_line, "rework_flag": w.rework_flag, "status": w.status,
        "wo_qty": w.wo_qty, "input_qty": w.input_qty, "remain_qty": w.remain_qty,
        "oper_text": w.oper_text, "array": w.array,
        "start_time": w.start_time, "stop_time": w.stop_time, "end_time": w.end_time,
    }


@router.get("/workorders", tags=["Work Order"])
def list_workorders(request, f: Query[WOFilter]):
    oper = get_object_or_404(Oper, code=f.oper)
    files = File.objects.filter(kind=File.Kind.WORKORDER, oper=oper)
    ef = files.filter(id=f.file_id).first() if f.file_id else files.order_by("-export_date").first()
    if not ef:
        return {"file": None, "total": 0, "page": 1, "page_size": f.page_size, "items": [],
                "lines": [], "statuses": [], "summary": {}}

    base = WorkOrder.objects.filter(file=ef)
    qs = base
    if f.line:
        qs = qs.filter(line=f.line)
    if f.status:
        qs = qs.filter(status=f.status)
    if f.q:
        qs = qs.filter(Q(work_order_no__icontains=f.q) | Q(f_code__icontains=f.q) |
                       Q(s_code__icontains=f.q) | Q(material_desc__icontains=f.q))

    # Tóm tắt theo trạng thái cho cả file
    summary = {}
    for st, qty, inp in base.values_list("status", "wo_qty", "input_qty"):
        s = summary.setdefault(st or "(trống)", {"count": 0, "wo_qty": 0, "input_qty": 0})
        s["count"] += 1
        s["wo_qty"] += qty or 0
        s["input_qty"] += inp or 0

    return {
        "file": file_out(ef),
        **_page(qs.order_by("line", "work_order_no"), f.page, f.page_size, _wo_out, max_size=1000),
        "lines": sorted(x for x in base.values_list("line", flat=True).distinct() if x),
        "statuses": sorted(x for x in base.values_list("status", flat=True).distinct() if x),
        "summary": summary,
    }
