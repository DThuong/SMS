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


@router.post("/jobs/{code}/run")
def run_job(request, code: str, data: RunIn):
    """Đưa job vào hàng đợi, worker chạy ngay ở vòng lặp kế tiếp (≤5s). Header X-Requested-By = người bấm bên DYS"""
    job = get_object_or_404(Job, code=code)
    report_dates = _report_dates(data)
    pending = job.runs.filter(status__in=PENDING).order_by("id").first()
    if pending:
        raise HttpError(409, f"Job đang chờ/đang chạy (run id={pending.id}). Theo dõi: GET /api/runs/{pending.id}")

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
