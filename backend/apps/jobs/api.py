"""API: job, chạy tay, lịch sử chạy, timeline từng bước"""
from datetime import date, datetime

from croniter import croniter
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from apps.collector.errors import error_info
from apps.core.security import requested_by

from .models import Job, JobRun

router = Router(tags=["Jobs"])


class JobOut(Schema):
    id: int
    code: str
    name: str
    description: str
    cron: str
    is_active: bool
    params: dict
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None = None
    has_pending: bool = False


class JobIn(Schema):
    name: str
    description: str = ""
    cron: str
    is_active: bool
    params: dict = {}


def _job_out(job: Job) -> dict:
    last = job.runs.order_by("-id").first()
    d = {f: getattr(job, f) for f in JobOut.model_fields if hasattr(job, f)}
    d["last_status"] = last.status if last else None
    d["has_pending"] = job.runs.filter(status__in=[JobRun.Status.QUEUED, JobRun.Status.RUNNING]).exists()
    return d


@router.get("/jobs", response=list[JobOut])
def list_jobs(request):
    return [_job_out(j) for j in Job.objects.all()]


@router.put("/jobs/{job_id}", response=JobOut)
def update_job(request, job_id: int, data: JobIn):
    if not croniter.is_valid(data.cron):
        raise HttpError(400, f"Biểu thức cron không hợp lệ: {data.cron}")
    job = get_object_or_404(Job, id=job_id)
    for k, v in data.dict().items():
        setattr(job, k, v)
    job.save()
    return _job_out(job)


class RunIn(Schema):
    opers: list[str] | None = None
    report_date: date | None = None     # ngày cần scan (job sản lượng)


@router.post("/jobs/{job_id}/run")
def run_job_now(request, job_id: int, data: RunIn = None):
    """Đưa job vào hàng đợi — worker sẽ chạy ngay ở vòng lặp kế tiếp. Header X-Requested-By = người bấm bên DYS"""
    job = get_object_or_404(Job, id=job_id)
    if job.runs.filter(status__in=[JobRun.Status.QUEUED, JobRun.Status.RUNNING]).exists():
        raise HttpError(400, "Job này đang chờ hoặc đang chạy")
    from django.utils import timezone
    params = dict(job.params or {})
    if data and data.opers:
        params["opers"] = data.opers
    if data and data.report_date:
        if data.report_date > timezone.localdate():
            raise HttpError(400, "Không lấy được ngày tương lai")
        params["report_date"] = data.report_date.isoformat()
    run = JobRun.objects.create(job=job, trigger=JobRun.Trigger.MANUAL,
                                requested_by=requested_by(request), params=params)
    return {"run_id": run.id}


# ---------------- Lịch sử chạy ----------------
class RunFilter(Schema):
    job_id: int | None = None
    status: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 20


def _run_out(r: JobRun) -> dict:
    return {
        "id": r.id, "job_id": r.job_id, "job_code": r.job.code, "job_name": r.job.name,
        "status": r.status, "status_label": r.get_status_display(),
        "trigger": r.trigger, "trigger_label": r.get_trigger_display(),
        "requested_by": r.requested_by,
        "params": r.params, "rows_total": r.rows_total, "error": r.error, "error_info": error_info(r.error_code),
        "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at,
        "duration_seconds": r.duration_seconds,
    }


@router.get("/runs")
def list_runs(request, f: Query[RunFilter]):
    qs = JobRun.objects.select_related("job")
    if f.job_id:
        qs = qs.filter(job_id=f.job_id)
    if f.status:
        qs = qs.filter(status=f.status)
    if f.date_from:
        qs = qs.filter(created_at__date__gte=f.date_from)
    if f.date_to:
        qs = qs.filter(created_at__date__lte=f.date_to)
    total = qs.count()
    size = max(1, min(f.page_size, 100))
    start = (max(f.page, 1) - 1) * size
    return {"total": total, "page": f.page, "page_size": size,
            "items": [_run_out(r) for r in qs.order_by("-id")[start:start + size]]}


@router.get("/runs/{run_id}")
def get_run(request, run_id: int):
    r = get_object_or_404(JobRun.objects.select_related("job"), id=run_id)
    data = _run_out(r)
    data["production_reports"] = [
        {"id": p.id, "report_date": p.report_date, "file_name": p.file_name, "rows": p.rows,
         "status": p.status, "status_label": p.get_status_display(), "file_deleted": p.file_deleted,
         "download_url": None if p.file_deleted else f"/api/production-reports/{p.id}/download"}
        for p in r.production_reports.all()]
    data["files"] = [{"id": f.id, "oper": f.oper.code, "oper_name": f.oper.name, "file_name": f.file_name,
                      "rows": f.rows, "export_date": f.export_date, "status": f.status}
                     for f in r.export_files.select_related("oper")]
    return data


@router.get("/runs/{run_id}/steps")
def get_run_steps(request, run_id: int, after_seq: int = 0):
    """Frontend gọi định kỳ với after_seq để chỉ lấy các bước mới (cập nhật trực tiếp)"""
    r = get_object_or_404(JobRun, id=run_id)
    steps = r.steps.filter(seq__gt=after_seq).order_by("seq")
    return {"status": r.status, "steps": [
        {"seq": s.seq, "level": s.level, "message": s.message,
         "screenshot_url": f"/api/runs/{r.id}/steps/{s.seq}/screenshot" if s.screenshot else None,
         "created_at": s.created_at} for s in steps]}


@router.get("/runs/{run_id}/steps/{seq}/screenshot")
def step_screenshot(request, run_id: int, seq: int):
    from pathlib import Path

    from django.conf import settings
    from django.http import FileResponse

    r = get_object_or_404(JobRun, id=run_id)
    step = get_object_or_404(r.steps, seq=seq)
    path = (settings.STORAGE_DIR / step.screenshot).resolve()
    if not step.screenshot or not path.is_file() or settings.STORAGE_DIR.resolve() not in path.parents:
        raise HttpError(404, "Không có ảnh")
    return FileResponse(open(Path(path), "rb"), content_type="image/png")
