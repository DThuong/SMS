"""
API: file lấy từ MES (bảng files) — DYS tải file về, import vào DB của DYS rồi báo kết quả

  GET    /api/files                     danh sách file (lọc kind=workorder|production, plant, oper, report_date...)
  GET    /api/files/{id}                chi tiết 1 file
  GET    /api/files/{id}/download       tải file .xlsx
  POST   /api/files/{id}/import-result  DYS báo kết quả import file (sản lượng / kế hoạch)
  DELETE /api/files/{id}                xóa file trên ổ đĩa + dòng trong files
  GET    /api/import-history            lịch sử import vào DYS thành công (file đã xóa vẫn còn lịch sử)

File không còn trong storage (DYS import xong, CLEANUP, xóa tay...) thì dòng trong bảng files cũng bị xóa:
mỗi lần đọc danh sách / chi tiết / tải file, SMS dọn các dòng có file đã mất.
"""
from datetime import date

from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from apps.core.timeutil import filter_days

from .models import File, ImportDysHistory
from .services import delete_file_and_row, purge_missing_files

router = Router()


def file_out(f: File) -> dict:
    """Một file trả về cho DYS — dùng chung cho /files và /runs/{id}"""
    return {
        "id": f.id, "kind": f.kind, "kind_label": f.get_kind_display(), "run_id": f.run_id,
        "plant": f.plant.code if f.plant_id else None, "plant_name": f.plant.name if f.plant_id else None,
        "oper": f.oper.code if f.oper_id else None, "oper_name": f.oper.name if f.oper_id else None,
        "period_from": f.period_from, "period_to": f.period_to,
        "report_date": f.report_date, "export_date": f.export_date,
        "file_name": f.file_name, "rows": f.rows, "size": f.size,
        "status": f.status, "status_label": f.get_status_display(), "file_deleted": f.file_deleted,
        "download_url": None if f.file_deleted else f"/api/files/{f.id}/download",
        "imported_at": f.imported_at, "imported_by": f.imported_by, "dys_message": f.dys_message,
        "created_at": f.created_at, "source": "file",
    }


def history_out(h: ImportDysHistory) -> dict:
    """1 dòng lịch sử import DYS — CÙNG dạng với file_out để DYS hiển thị chung (file đã xóa: download_url = null)"""
    return {
        "id": h.id, "kind": h.kind, "kind_label": h.get_kind_display(), "run_id": h.run_id,
        "plant": h.plant_code or None, "plant_name": h.plant.name if h.plant_id else (h.plant_code or None),
        "oper": h.oper_code or None, "oper_name": h.oper_name or None,
        "period_from": h.period_from, "period_to": h.period_to,
        "report_date": h.report_date, "export_date": h.export_date,
        "file_name": h.file_name, "rows": h.rows, "size": 0,
        "status": File.Status.IMPORTED, "status_label": File.Status.IMPORTED.label, "file_deleted": True,
        "download_url": None,
        "imported_at": h.imported_at, "imported_by": h.imported_by, "dys_message": h.dys_message,
        "created_at": h.created_at, "source": "history", "file_id": h.file_id,
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
    plant: str | None = None            # mã plant (file workorder), vd PL20
    oper: str | None = None             # mã xưởng (file workorder)
    report_date: date | None = None     # ngày sản lượng (file production)
    status: File.Status | None = None
    date_from: date | None = None       # theo export_date
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


@router.get("/files", tags=["File"], summary="Danh sách file lấy từ MES")
def list_files(request, f: Query[FileFilter]):
    purge_missing_files()
    qs = File.objects.select_related("oper", "plant")
    if f.kind:
        qs = qs.filter(kind=f.kind)
    if f.plant:
        qs = qs.filter(plant__code=f.plant)
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
    purge_missing_files(File.objects.filter(id=file_id))
    return file_out(get_object_or_404(File.objects.select_related("oper", "plant"), id=file_id))


@router.get("/files/{file_id}/download", tags=["File"], summary="Tải file .xlsx để DYS import")
def download_file(request, file_id: int):
    """Tải file .xlsx để DYS import (sản lượng: POST /api/ProductionResult/import của DYS)"""
    f = get_object_or_404(File.objects.select_related("oper", "plant"), id=file_id)
    path = settings.STORAGE_DIR / f.file_path
    if f.file_deleted or not path.is_file():
        delete_file_and_row(f)              # file mất → bỏ luôn dòng trong bảng files
        raise HttpError(404, "File không còn trên ổ đĩa (đã import xong hoặc quá hạn lưu) — đã xóa khỏi danh sách")
    prefix = "_".join(x for x in (f.plant.code if f.plant_id else "", f.oper.folder if f.oper_id else "") if x)
    name = f"{prefix}_{f.file_name}" if prefix else f.file_name
    return FileResponse(open(path, "rb"), as_attachment=True, filename=name)


class ImportResultIn(Schema):
    success: bool
    message: str = ""          # phản hồi / lỗi từ API import của DYS
    imported_by: str = ""      # username bên DYS (bỏ trống = lấy X-Requested-By / tên API key)


@router.post("/files/{file_id}/import-result", tags=["File"], summary="DYS báo kết quả import file")
def import_result(request, file_id: int, data: ImportResultIn):
    """
    DYS báo kết quả import file (sản lượng hoặc kế hoạch).
    Thành công + bật delete_file_after_import → xóa file trên máy VÀ dòng trong bảng files (`deleted: true`).
    """
    from apps.core.models import get_setting
    from apps.core.security import requested_by

    from .services import mark_import_result

    f = get_object_or_404(File.objects.select_related("oper", "plant"), id=file_id)
    deleted = mark_import_result(f, data.success, data.message, data.imported_by or requested_by(request),
                                 delete_file=get_setting("delete_file_after_import", True))
    out = file_out(f)
    if deleted:
        out.update(file_deleted=True, download_url=None)
    return {**out, "deleted": deleted}


@router.delete("/files/{file_id}", tags=["File"], summary="Xóa file trên ổ đĩa + dòng trong bảng files")
def delete_file(request, file_id: int, with_data: bool = True):
    """Xóa file trên ổ đĩa và dòng trong bảng files (tham số with_data giữ để tương thích, luôn xóa cả hai)"""
    f = get_object_or_404(File, id=file_id)
    delete_file_and_row(f)
    return {"ok": True, "deleted": "file+data"}


# ======================= LỊCH SỬ IMPORT DYS =======================
class HistoryFilter(Schema):
    kind: File.Kind | None = None       # workorder | production
    oper: str | None = None             # mã xưởng
    report_date: date | None = None     # ngày sản lượng
    date_from: date | None = None       # theo export_date (giờ VN)
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


@router.get("/import-history", tags=["File"], summary="Lịch sử import vào DYS thành công")
def list_import_history(request, f: Query[HistoryFilter]):
    """Mỗi lần import vào DYS thành công 1 dòng (file đã bị xóa vẫn còn ở đây tới khi CLEANUP xóa theo data_retention_days)"""
    qs = ImportDysHistory.objects.select_related("plant")
    if f.kind:
        qs = qs.filter(kind=f.kind)
    if f.oper:
        qs = qs.filter(oper_code=f.oper)
    if f.report_date:
        qs = qs.filter(report_date=f.report_date)
    qs = filter_days(qs, "export_date", f.date_from, f.date_to)
    return _page(qs.order_by("-imported_at", "-id"), f.page, f.page_size, history_out)
