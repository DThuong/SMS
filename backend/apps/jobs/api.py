"""
API: job (bảng jobs), lần chạy (job_runs), nhật ký (job_logs)

  GET  /api/jobs                               danh sách job + lịch + lần chạy gần nhất
  PUT  /api/jobs/{code}                        sửa lịch cron / bật tắt / tham số mặc định
  POST /api/jobs/{code}/run                    chạy ngay (DYS bấm nút) — có thể giữ kết nối chờ xong
  GET  /api/runs                               lịch sử chạy
  GET  /api/runs/{id}                          chi tiết 1 lần chạy: trạng thái, lỗi, nhật ký, file tạo ra
  GET  /api/runs/{id}/logs?after_seq=          chỉ lấy nhật ký mới (cập nhật trực tiếp)
  GET  /api/runs/{id}/logs/{seq}/screenshot    ảnh màn hình MES lúc lỗi

Luồng sản lượng cho DYS:
  1. POST /api/jobs/PRODUCTION_OUTPUT/run
       1 ngày:     {"report_date": "2026-10-28"}
       nhiều ngày: {"date_from": "2026-10-25", "date_to": "2026-10-28"}   (tối đa 31 ngày, agent lặp từng ngày)
  2. finished=false → gọi GET /api/runs/{id} vài giây một lần tới khi finished=true
     status: success = đủ ngày · partial = có ngày lỗi (xem error) · failed = không lấy được ngày nào
  3. files[] — MỖI NGÀY 1 FILE (report_date) → tải download_url → DYS gọi POST /api/ProductionResult/import (của DYS)
  4. Mỗi file: POST /api/files/{file_id}/import-result {"success": true, "message": "..."}

Luồng kế hoạch Work Order cho DYS (mỗi xưởng một file):
  1. POST /api/jobs/WORKORDER_PLAN/run {"opers": ["2010", "3010"], "wait_seconds": 90}   (bỏ opers = mọi xưởng)
  2. finished=false → gọi GET /api/runs/{id} tới khi finished=true
     status: success = đủ xưởng · partial = có xưởng lỗi (xem error) · failed = không lấy được xưởng nào
  3. files[] — mỗi xưởng 1 file (oper, rows, id) → tải download_url → DYS import vào DB của DYS
  4. Mỗi file: POST /api/files/{file_id}/import-result {"success": true, "message": "..."}
"""
import time
from datetime import date

from croniter import croniter
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from apps.collector.errors import error_info
from apps.core.security import requested_by
from apps.core.timeutil import filter_days

from .models import Job, JobRun, WorkerStatus, cron_period

router = Router(tags=["Job"])

PENDING = [JobRun.Status.QUEUED, JobRun.Status.RUNNING]
FINAL = {JobRun.Status.SUCCESS, JobRun.Status.PARTIAL, JobRun.Status.FAILED}


# ======================= JOB =======================
def _job_out(job: Job) -> dict:
    last = job.runs.order_by("-id").first()
    return {
        "code": job.code, "name": job.name, "description": job.description,
        "cron": job.cron, "is_active": job.is_active, "params": job.params,
        "next_run_at": job.next_run_at, "last_run_at": job.last_run_at,
        "last_run": None if not last else {"id": last.id, "status": last.status, "rows_total": last.rows_total,
                                           "finished_at": last.finished_at},
        "running": job.runs.filter(status__in=PENDING).exists(),
        "period": _period_out(job),
    }


def _period_out(job: Job) -> dict:
    """Kỳ lịch hiện tại (vd 08:00 hôm nay → 08:00 hôm sau) và lần chạy tự động của kỳ đó"""
    start, end = cron_period(job.cron)
    sr = job.scheduled_run_in_period()
    return {"start": start, "end": end,
            "scheduled_run": None if not sr else {"id": sr.id, "status": sr.status,
                                                  "status_label": sr.get_status_display(),
                                                  "created_at": sr.created_at, "finished_at": sr.finished_at}}


@router.get("/jobs", summary="Danh sách job + lịch + kỳ hiện tại")
def list_jobs(request):
    return [_job_out(j) for j in Job.objects.all()]


class JobIn(Schema):
    cron: str
    is_active: bool
    params: dict = {}


@router.put("/jobs/{code}", summary="Sửa lịch / bật tắt job")
def update_job(request, code: str, data: JobIn):
    if not croniter.is_valid(data.cron):
        raise HttpError(400, f"Biểu thức cron không hợp lệ: {data.cron}")
    job = get_object_or_404(Job, code=code)
    job.cron, job.is_active, job.params = data.cron, data.is_active, data.params
    job.save()
    return _job_out(job)


MAX_RANGE_DAYS = 31                     # PRODUCTION_OUTPUT: tối đa 31 ngày / 1 lần chạy (mỗi ngày MES ~1 phút)


class RunIn(Schema):
    report_date: date | None = None     # PRODUCTION_OUTPUT: lấy 1 ngày. Bỏ trống (và không có date_from) = hôm nay
    date_from: date | None = None       # PRODUCTION_OUTPUT: lấy nhiều ngày — từ ngày (MES mỗi lần chỉ 1 ngày → agent lặp)
    date_to: date | None = None         #   đến ngày (bỏ trống = bằng date_from). Mỗi ngày ra 1 file
    opers: list[str] | None = None      # WORKORDER_PLAN: chỉ lấy các xưởng này. Bỏ trống = mọi xưởng đang dùng
    wait_seconds: int = 0               # >0: giữ kết nối chờ job xong (tối đa 90s vì Cloudflare cắt request sau 100s)


def _report_dates(data: RunIn) -> list[date]:
    """Danh sách ngày sản lượng cần lấy: date_from..date_to, hoặc report_date. [] = để job tự lấy hôm nay"""
    from datetime import timedelta

    today = timezone.localdate()
    if data.date_from or data.date_to:
        start = data.date_from or data.date_to
        end = data.date_to or start
        if start > end:
            raise HttpError(400, f"Từ ngày ({start:%d/%m/%Y}) phải trước hoặc bằng đến ngày ({end:%d/%m/%Y})")
    elif data.report_date:
        start = end = data.report_date
    else:
        return []
    if end > today:
        raise HttpError(400, f"Không lấy được ngày tương lai ({end:%d/%m/%Y}); hôm nay là {today:%d/%m/%Y}")
    n = (end - start).days + 1
    if n > MAX_RANGE_DAYS:
        raise HttpError(400, f"Tối đa {MAX_RANGE_DAYS} ngày mỗi lần chạy (đang chọn {n} ngày)")
    return [start + timedelta(days=i) for i in range(n)]


@router.post("/jobs/{code}/run", summary="Chạy job ngay (sản lượng theo ngày / khoảng ngày, kế hoạch theo xưởng)")
def run_job(request, code: str, data: RunIn):
    """
    Đưa yêu cầu vào HÀNG CHỜ (không bao giờ trả 409 vì đang bận). Ai bấm trước chạy trước; đang có job chạy thì
    yêu cầu này chờ tới lượt — xem `queue_ahead` (số lần chạy đứng trước) trong kết quả / GET /api/runs/{id}.
    Header X-Requested-By = người bấm bên DYS.
    """
    job = get_object_or_404(Job, code=code)
    report_dates = _report_dates(data)
    params = dict(job.params or {})
    if report_dates:
        params.pop("report_date", None)
        params["report_dates"] = [d.isoformat() for d in report_dates]
    if data.opers:
        from apps.core.models import Oper
        codes = [c.strip() for c in data.opers if c and c.strip()]
        known = set(Oper.objects.filter(code__in=codes, is_active=True).values_list("code", flat=True))
        unknown = [c for c in codes if c not in known]
        if unknown:
            raise HttpError(400, f"Mã xưởng không có hoặc đang tắt: {', '.join(unknown)}. Xem GET /api/opers")
        params["opers"] = codes
    # HÀNG CHỜ: không từ chối khi đang có job chạy — mọi yêu cầu đều được xếp hàng.
    # id tự tăng = thứ tự bấm; worker luôn lấy lần chạy `queued` có id nhỏ nhất → ai bấm trước chạy trước,
    # người sau chờ tới lượt (kể cả khác job: 1 worker chỉ điều khiển 1 MESClient tại một thời điểm).
    run = JobRun.objects.create(job=job, trigger=JobRun.Trigger.API, params=params,
                                requested_by=requested_by(request))

    wait = max(0, min(data.wait_seconds, 90))
    deadline = time.time() + wait
    while wait and time.time() < deadline:
        time.sleep(1)
        run.refresh_from_db(fields=["status"])
        if run.status in FINAL:
            break
    return _run_detail(run)


# ======================= LẦN CHẠY =======================
def queue_ahead(r: JobRun) -> int | None:
    """Số lần chạy đứng TRƯỚC lần này trong hàng chờ (đang chạy + đang chờ có id nhỏ hơn). None nếu không còn chờ"""
    if r.status != JobRun.Status.QUEUED:
        return None
    return JobRun.objects.filter(
        Q(status=JobRun.Status.RUNNING) | Q(status=JobRun.Status.QUEUED, id__lt=r.id)).count()


def _run_out(r: JobRun) -> dict:
    return {
        "id": r.id, "job": r.job.code, "job_name": r.job.name,
        "status": r.status, "status_label": r.get_status_display(), "finished": r.status in FINAL,
        "trigger": r.trigger, "trigger_label": r.get_trigger_display(), "requested_by": r.requested_by,
        "params": r.params, "rows_total": r.rows_total,
        "error": r.error, "error_info": error_info(r.error_code),
        "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at,
        "duration_seconds": r.duration_seconds,
        "queue_ahead": queue_ahead(r),      # 0 = chạy ngay khi worker rảnh; 2 = còn 2 lần chạy phía trước
    }


def _log_out(r: JobRun, lg) -> dict:
    return {"seq": lg.seq, "level": lg.level, "message": lg.message, "created_at": lg.created_at,
            "screenshot_url": f"/api/runs/{r.id}/logs/{lg.seq}/screenshot" if lg.screenshot else None}


def _run_detail(r: JobRun) -> dict:
    from apps.plans.api import file_out

    r = JobRun.objects.select_related("job").get(pk=r.pk)
    return {
        **_run_out(r),
        "worker_online": WorkerStatus.is_online(),
        "logs": [_log_out(r, lg) for lg in r.logs.order_by("seq")],
        "files": [file_out(f) for f in r.files.select_related("oper").order_by("id")],
    }



@router.get("/queue", summary="Hàng chờ: lần đang chạy + các lần đang chờ theo thứ tự")
def get_queue(request):
    """Thứ tự thực hiện hiện tại: phần tử đầu là lần đang chạy (nếu có), sau đó các lần chờ theo thứ tự bấm"""
    running = list(JobRun.objects.select_related("job").filter(status=JobRun.Status.RUNNING).order_by("id"))
    queued = list(JobRun.objects.select_related("job").filter(status=JobRun.Status.QUEUED).order_by("id"))
    items = []
    for i, r in enumerate(running + queued):
        items.append({"position": i, "id": r.id, "job": r.job.code, "job_name": r.job.name, "status": r.status,
                      "requested_by": r.requested_by, "trigger": r.trigger, "params": r.params,
                      "created_at": r.created_at, "started_at": r.started_at})
    return {"worker_online": WorkerStatus.is_online(), "running": len(running), "waiting": len(queued), "items": items}

class RunFilter(Schema):
    job: str | None = None              # mã job
    status: JobRun.Status | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


@router.get("/runs", summary="Lịch sử chạy (lọc job, trạng thái, ngày)")
def list_runs(request, f: Query[RunFilter]):
    qs = JobRun.objects.select_related("job")
    if f.job:
        qs = qs.filter(job__code=f.job)
    if f.status:
        qs = qs.filter(status=f.status)
    qs = filter_days(qs, "created_at", f.date_from, f.date_to)
    total = qs.count()
    size = max(1, min(f.page_size, 100))
    page = max(f.page, 1)
    start = (page - 1) * size
    return {"total": total, "page": page, "page_size": size,
            "items": [_run_out(r) for r in qs.order_by("-id")[start:start + size]]}


@router.get("/runs/{int:run_id}", summary="Chi tiết 1 lần chạy + nhật ký + file")
def get_run(request, run_id: int):
    """Chi tiết 1 lần chạy: trạng thái, error_info, toàn bộ nhật ký, các file đã lấy, worker_online"""
    return _run_detail(get_object_or_404(JobRun, id=run_id))


@router.get("/runs/{run_id}/logs", summary="Nhật ký mới của lần chạy (after_seq)")
def get_run_logs(request, run_id: int, after_seq: int = 0):
    """Gọi định kỳ với after_seq = seq lớn nhất đã có để chỉ lấy dòng nhật ký mới"""
    r = get_object_or_404(JobRun, id=run_id)
    return {"status": r.status, "finished": r.status in FINAL, "queue_ahead": queue_ahead(r),
            "logs": [_log_out(r, lg) for lg in r.logs.filter(seq__gt=after_seq).order_by("seq")]}


@router.get("/runs/{run_id}/logs/{seq}/screenshot", summary="Ảnh màn hình lúc lỗi")
def log_screenshot(request, run_id: int, seq: int):
    from django.conf import settings
    from django.http import FileResponse

    r = get_object_or_404(JobRun, id=run_id)
    lg = get_object_or_404(r.logs, seq=seq)
    path = (settings.STORAGE_DIR / lg.screenshot).resolve()
    if not lg.screenshot or not path.is_file() or settings.STORAGE_DIR.resolve() not in path.parents:
        raise HttpError(404, "Không có ảnh")
    return FileResponse(open(path, "rb"), content_type="image/png")


# ======================= XÓA LỊCH SỬ CHẠY =======================
def _delete_runs(qs) -> dict:
    """Xóa các lần chạy (đã kết thúc) + nhật ký (CASCADE) + ảnh lỗi trên ổ đĩa. File MES giữ lại (run_id → null)"""
    from django.conf import settings

    from .models import JobLog

    qs = qs.filter(status__in=FINAL)
    ids = list(qs.values_list("id", flat=True))
    if not ids:
        return {"deleted_runs": 0, "deleted_logs": 0, "deleted_screenshots": 0, "ids": []}
    shots = [s for s in JobLog.objects.filter(run_id__in=ids).exclude(screenshot="")
             .values_list("screenshot", flat=True)]
    n_logs = JobLog.objects.filter(run_id__in=ids).count()
    WorkerStatus.objects.filter(current_run_id__in=ids).update(current_run=None)
    with transaction.atomic():
        JobRun.objects.filter(id__in=ids).delete()
    root = settings.STORAGE_DIR.resolve()
    n_shots = 0
    for rel in set(shots):
        p = (settings.STORAGE_DIR / rel).resolve()
        if root in p.parents and p.is_file():
            p.unlink(missing_ok=True)
            n_shots += 1
    return {"deleted_runs": len(ids), "deleted_logs": n_logs, "deleted_screenshots": n_shots, "ids": ids}


@router.delete("/runs/{int:run_id}", summary="Xóa 1 lần chạy khỏi lịch sử")
def delete_run(request, run_id: int):
    """
    Xóa 1 lần chạy khỏi lịch sử (kèm nhật ký + ảnh lỗi). Lần chạy đang chờ/đang chạy → 409.
    File lấy từ MES của lần chạy đó KHÔNG bị xóa (vẫn xem/tải ở /api/files).
    """
    r = get_object_or_404(JobRun, id=run_id)
    if r.status not in FINAL:
        raise HttpError(409, f"Lần chạy #{r.id} đang {r.get_status_display().lower()} — chờ xong rồi mới xóa được")
    return _delete_runs(JobRun.objects.filter(id=r.id))


class RunDeleteIn(Schema):
    ids: list[int] | None = None        # xóa đúng các id này (vd các dòng tích chọn trên DYS)
    job: str | None = None              # hoặc theo bộ lọc giống GET /api/runs
    status: JobRun.Status | None = None
    date_from: date | None = None
    date_to: date | None = None
    all: bool = False                   # true = xóa toàn bộ lịch sử (bắt buộc khi không có điều kiện nào)


@router.post("/runs/delete", summary="Xóa nhiều lần chạy (ids / bộ lọc / all)")
def delete_runs(request, data: RunDeleteIn):
    """
    Xóa nhiều lần chạy một lúc. Truyền `ids`, hoặc bộ lọc (`job`, `status`, `date_from`, `date_to`),
    hoặc `all: true` để xóa hết. Không có điều kiện nào → 400 (tránh lỡ tay xóa sạch).
    Lần chạy đang chờ/đang chạy luôn được bỏ qua. Trả về số lần chạy / dòng nhật ký / ảnh lỗi đã xóa.
    """
    has_filter = bool(data.ids or data.job or data.status or data.date_from or data.date_to)
    if not has_filter and not data.all:
        raise HttpError(400, "Cần ít nhất một điều kiện (ids, job, status, date_from, date_to) hoặc all=true")
    if data.status and data.status not in FINAL:
        raise HttpError(400, "Chỉ xóa được lần chạy đã kết thúc (success, partial, failed)")
    qs = JobRun.objects.all()
    if data.ids:
        qs = qs.filter(id__in=data.ids)
    if data.job:
        qs = qs.filter(job__code=data.job)
    if data.status:
        qs = qs.filter(status=data.status)
    qs = filter_days(qs, "created_at", data.date_from, data.date_to)
    skipped = qs.exclude(status__in=FINAL).count()
    return {**_delete_runs(qs), "skipped_pending": skipped}
