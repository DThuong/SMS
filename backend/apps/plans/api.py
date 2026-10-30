"""
API: file lấy từ MES (bảng files) — DYS tải file về, import vào DB của DYS rồi báo kết quả

  GET    /api/files                     danh sách file (lọc kind=workorder|production, oper, report_date...)
  GET    /api/files/{id}                chi tiết 1 file
  GET    /api/files/{id}/download       tải file .xlsx
  POST   /api/files/{id}/import-result  DYS báo kết quả import file (sản lượng / kế hoạch)
  DELETE /api/files/{id}                xóa file trên ổ đĩa (with_data=true: xóa luôn dòng trong files)
"""
from datetime import date

from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from apps.core.timeutil import filter_days

from .models import File

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


@router.get("/files", tags=["File"], summary="Danh sách file lấy từ MES")
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
    qs = filter_days(qs, "export_date", f.date_from, f.date_to)
    return _page(qs.order_by("-export_date", "-id"), f.page, f.page_size, file_out)


@router.get("/files/{file_id}", tags=["File"], summary="Chi tiết 1 file")
def get_file(request, file_id: int):
    return file_out(get_object_or_404(File.objects.select_related("oper"), id=file_id))


@router.get("/files/{file_id}/download", tags=["File"], summary="Tải file .xlsx để DYS import")
def download_file(request, file_id: int):
    """Tải file .xlsx để DYS import (sản lượng: POST /api/ProductionResult/import của DYS)"""
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


@router.post("/files/{file_id}/import-result", tags=["File"], summary="DYS báo kết quả import file")
def import_result(request, file_id: int, data: ImportResultIn):
    """DYS báo kết quả import file (sản lượng hoặc kế hoạch) → thành công thì xóa file nếu bật delete_file_after_import"""
    from apps.core.models import get_setting
    from apps.core.security import requested_by

    from .services import mark_import_result

    f = get_object_or_404(File, id=file_id)
    mark_import_result(f, data.success, data.message, data.imported_by or requested_by(request),
                       delete_file=get_setting("delete_file_after_import", True))
    return file_out(f)


@router.delete("/files/{file_id}", tags=["File"], summary="Xóa file trên ổ đĩa (with_data: xóa cả dòng)")
def delete_file(request, file_id: int, with_data: bool = False):
    """Xóa file trên ổ đĩa; with_data=true thì xóa luôn dòng trong bảng files"""
    from .services import delete_file_on_disk

    f = get_object_or_404(File, id=file_id)
    delete_file_on_disk(f)
    if with_data:
        f.delete()
        return {"ok": True, "deleted": "file+data"}
    return {"ok": True, "deleted": "file"}

