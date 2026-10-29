"""
API rút gọn cho DYS gọi vào SMS (chạy job + theo dõi). Xác thực: mật khẩu API (X-Api-Password hoặc ?password=).
Header tùy chọn X-Requested-By: <username bên DYS> để ghi ai đã bấm chạy.

Luồng sản lượng: chạy job → có production_report.download_url → DYS tải file → DYS tự gọi
POST /api/ProductionResult/import của DYS bằng token người dùng → báo lại POST .../import-result

  POST /api/integrations/production-output/run   chạy trọn quy trình sản lượng: scan MES → file → đẩy DYS
  POST /api/integrations/jobs/{code}/run         chạy một job bất kỳ (WORKORDER_PLAN, PRODUCTION_OUTPUT...)
  GET  /api/integrations/runs/{run_id}           xem trạng thái / kết quả một lần chạy
"""
import time
from datetime import date

from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from apps.collector.errors import error_info
from apps.core.security import requested_by
from apps.jobs.models import Job, JobRun, WorkerHeartbeat

FINAL = {JobRun.Status.SUCCESS, JobRun.Status.PARTIAL, JobRun.Status.FAILED}


router = Router(tags=["Tích hợp (DYS gọi vào)"])


class TriggerIn(Schema):
    report_date: date | None = None     # ngày cần scan (sản lượng). Bỏ trống = hôm nay (giờ VN)
    opers: list[str] | None = None      # chỉ dùng cho WORKORDER_PLAN
    wait_seconds: int = 0               # >0: giữ kết nối chờ job xong (tối đa 90s, vì Cloudflare cắt request sau 100s)


def _worker_online() -> bool:
    hb = WorkerHeartbeat.objects.filter(pk=1).first()
    return bool(hb and hb.last_seen and (timezone.now() - hb.last_seen).total_seconds() < 60)


def _result(run: JobRun) -> dict:
    run.refresh_from_db()
    data = {
        "run_id": run.id, "job": run.job.code, "status": run.status, "status_label": run.get_status_display(),
        "finished": run.status in FINAL, "requested_by": run.requested_by, "params": run.params, "rows_total": run.rows_total, "error": run.error,
        "error_info": error_info(run.error_code), "worker_online": _worker_online(),
        "created_at": run.created_at, "started_at": run.started_at, "finished_at": run.finished_at,
        "steps": [{"seq": s.seq, "level": s.level, "message": s.message, "at": s.created_at}
                  for s in run.steps.order_by("seq")],
    }
    rp = run.production_reports.order_by("-id").first()
    if rp:
        data["production_report"] = {
            "id": rp.id, "report_date": rp.report_date, "file_name": rp.file_name, "rows": rp.rows,
            "status": rp.status, "file_deleted": rp.file_deleted,
            "download_url": None if rp.file_deleted else f"/api/production-reports/{rp.id}/download",
            "import_result_url": f"/api/production-reports/{rp.id}/import-result",
        }
    files = list(run.export_files.select_related("oper"))
    if files:
        data["files"] = [{"oper": f.oper.code, "file_name": f.file_name, "rows": f.rows} for f in files]
    return data


def _trigger(request, code: str, data: TriggerIn) -> dict:
    job = Job.objects.filter(code=code).first()
    if not job:
        raise HttpError(404, f"Không có job {code}")
    if data.report_date and data.report_date > timezone.localdate():
        raise HttpError(400, f"Không lấy được ngày tương lai ({data.report_date}); hôm nay là {timezone.localdate()}")

    pending = job.runs.filter(status__in=[JobRun.Status.QUEUED, JobRun.Status.RUNNING]).order_by("id").first()
    if pending:
        raise HttpError(409, f"Job đang chờ/đang chạy (run_id={pending.id}). Gọi GET /api/integrations/runs/{pending.id} để theo dõi")

    params = dict(job.params or {})
    if data.report_date:
        params["report_date"] = data.report_date.isoformat()
    if data.opers:
        params["opers"] = data.opers
    run = JobRun.objects.create(job=job, trigger=JobRun.Trigger.API, params=params,
                                requested_by=requested_by(request))

    wait = max(0, min(data.wait_seconds, 90))
    deadline = time.time() + wait
    while wait and time.time() < deadline:
        time.sleep(1)
        run.refresh_from_db(fields=["status"])
        if run.status in FINAL:
            break
    return _result(run)


@router.post("/production-output/run")
def run_production_output(request, data: TriggerIn):
    """Scan sản lượng theo giờ (TV) của `report_date` trên MES → lưu file chờ DYS tải về import"""
    return _trigger(request, "PRODUCTION_OUTPUT", data)


@router.post("/jobs/{code}/run")
def run_job(request, code: str, data: TriggerIn):
    return _trigger(request, code, data)


@router.get("/runs/{run_id}")
def get_run(request, run_id: int):
    run = JobRun.objects.filter(id=run_id).select_related("job").first()
    if not run:
        raise HttpError(404, "Không có lần chạy này")
    return _result(run)
