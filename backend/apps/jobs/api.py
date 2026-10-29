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
  1. POST /api/jobs/PRODUCTION_OUTPUT/run {"report_date": "2026-10-28", "wait_seconds": 90}
  2. finished=false → gọi GET /api/runs/{id} vài giây một lần tới khi finished=true
  3. status=success → files[0].download_url → tải file → DYS gọi POST /api/ProductionResult/import (của DYS)
  4. POST /api/files/{file_id}/import-result {"success": true, "message": "..."}

Luồng kế hoạch Work Order cho DYS (mỗi xưởng một file):
  1. POST /api/jobs/WORKORDER_PLAN/run {"opers": ["2010", "3010"], "wait_seconds": 90}   (bỏ opers = mọi xưởng)
  2. finished=false → gọi GET /api/runs/{id} tới khi finished=true
     status: success = đủ xưởng · partial = có xưởng lỗi (xem error) · failed = không lấy được xưởng nào
  3. files[] — mỗi xưởng 1 phần tử (oper, rows, id) → GET /api/workorders?oper=2010&file_id={id}&page_size=1000
"""
import time
from datetime import date

from croniter import croniter
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from apps.collector.errors import error_info
from apps.core.security import requested_by

from .models import Job, JobRun, WorkerStatus

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
    }


@router.get("/jobs")
def list_jobs(request):
    return [_job_out(j) for j in Job.objects.all()]


class JobIn(Schema):
    cron: str
    is_active: bool
    params: dict = {}


@router.put("/jobs/{code}")
def update_job(request, code: str, data: JobIn):
    if not croniter.is_valid(data.cron):
        raise HttpError(400, f"Biểu thức cron không hợp lệ: {data.cron}")
    job = get_object_or_404(Job, code=code)
    job.cron, job.is_active, job.params = data.cron, data.is_active, data.params
    job.save()
    return _job_out(job)


class RunIn(Schema):
    report_date: date | None = None     # PRODUCTION_OUTPUT: ngày cần lấy. Bỏ trống = hôm nay (giờ VN)
    opers: list[str] | None = None      # WORKORDER_PLAN: chỉ lấy các xưởng này. Bỏ trống = mọi xưởng đang dùng
    wait_seconds: int = 0               # >0: giữ kết nối chờ job xong (tối đa 90s vì Cloudflare cắt request sau 100s)


@router.post("/jobs/{code}/run")
def run_job(request, code: str, data: RunIn):
    """Đưa job vào hàng đợi, worker chạy ngay ở vòng lặp kế tiếp (≤5s). Header X-Requested-By = người bấm bên DYS"""
    job = get_object_or_404(Job, code=code)
    if data.report_date and data.report_date > timezone.localdate():
        raise HttpError(400, f"Không lấy được ngày tương lai ({data.report_date}); hôm nay là {timezone.localdate()}")
    pending = job.runs.filter(status__in=PENDING).order_by("id").first()
    if pending:
        raise HttpError(409, f"Job đang chờ/đang chạy (run id={pending.id}). Theo dõi: GET /api/runs/{pending.id}")

    params = dict(job.params or {})
    if data.report_date:
        params["report_date"] = data.report_date.isoformat()
    if data.opers:
        from apps.core.models import Oper
        codes = [c.strip() for c in data.opers if c and c.strip()]
        known = set(Oper.objects.filter(code__in=codes, is_active=True).values_list("code", flat=True))
        unknown = [c for c in codes if c not in known]
        if unknown:
            raise HttpError(400, f"Mã xưởng không có hoặc đang tắt: {', '.join(unknown)}. Xem GET /api/opers")
        params["opers"] = codes
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
def _run_out(r: JobRun) -> dict:
    return {
        "id": r.id, "job": r.job.code, "job_name": r.job.name,
        "status": r.status, "status_label": r.get_status_display(), "finished": r.status in FINAL,
        "trigger": r.trigger, "trigger_label": r.get_trigger_display(), "requested_by": r.requested_by,
        "params": r.params, "rows_total": r.rows_total,
        "error": r.error, "error_info": error_info(r.error_code),
        "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at,
        "duration_seconds": r.duration_seconds,
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


class RunFilter(Schema):
    job: str | None = None              # mã job
    status: JobRun.Status | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


@router.get("/runs")
def list_runs(request, f: Query[RunFilter]):
    qs = JobRun.objects.select_related("job")
    if f.job:
        qs = qs.filter(job__code=f.job)
    if f.status:
        qs = qs.filter(status=f.status)
    if f.date_from:
        qs = qs.filter(created_at__date__gte=f.date_from)
    if f.date_to:
        qs = qs.filter(created_at__date__lte=f.date_to)
    total = qs.count()
    size = max(1, min(f.page_size, 100))
    page = max(f.page, 1)
    start = (page - 1) * size
    return {"total": total, "page": page, "page_size": size,
            "items": [_run_out(r) for r in qs.order_by("-id")[start:start + size]]}


@router.get("/runs/{run_id}")
def get_run(request, run_id: int):
    return _run_detail(get_object_or_404(JobRun, id=run_id))


@router.get("/runs/{run_id}/logs")
def get_run_logs(request, run_id: int, after_seq: int = 0):
    """Gọi định kỳ với after_seq = seq lớn nhất đã có để chỉ lấy dòng nhật ký mới"""
    r = get_object_or_404(JobRun, id=run_id)
    return {"status": r.status, "finished": r.status in FINAL,
            "logs": [_log_out(r, lg) for lg in r.logs.filter(seq__gt=after_seq).order_by("seq")]}


@router.get("/runs/{run_id}/logs/{seq}/screenshot")
def log_screenshot(request, run_id: int, seq: int):
    from django.conf import settings
    from django.http import FileResponse

    r = get_object_or_404(JobRun, id=run_id)
    lg = get_object_or_404(r.logs, seq=seq)
    path = (settings.STORAGE_DIR / lg.screenshot).resolve()
    if not lg.screenshot or not path.is_file() or settings.STORAGE_DIR.resolve() not in path.parents:
        raise HttpError(404, "Không có ảnh")
    return FileResponse(open(path, "rb"), content_type="image/png")
