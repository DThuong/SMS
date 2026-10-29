"""Thực thi một JobRun: ghi trạng thái, ghi từng bước vào DB qua logging"""
import logging
import traceback
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from .models import JobRun, JobRunStep
from .registry import HANDLERS


class DbStepHandler(logging.Handler):
    """Mỗi dòng log của job = một bước trong timeline (bảng jobs_jobrunstep)"""

    def __init__(self, run: JobRun):
        super().__init__(level=logging.INFO)
        self.run = run
        self.seq = run.steps.count()

    def emit(self, record):
        try:
            msg = record.getMessage()
            if record.exc_info:
                # Chỉ giữ 3 dòng cuối traceback cho gọn
                tb = "".join(traceback.format_exception(*record.exc_info)).strip().splitlines()
                msg += "\n" + "\n".join(tb[-3:])
            self.seq += 1
            JobRunStep.objects.create(
                run=self.run, seq=self.seq,
                level=record.levelname if record.levelname in JobRunStep.Level.values else "INFO",
                message=msg, screenshot=getattr(record, "screenshot", ""),
            )
        except Exception:
            self.handleError(record)


def _make_logger(run: JobRun) -> logging.Logger:
    log = logging.getLogger(f"sms.run.{run.id}")
    log.setLevel(logging.INFO)
    log.propagate = False
    log.handlers.clear()
    log.addHandler(DbStepHandler(run))
    # Ghi thêm ra console + file để xem khi chạy worker bằng tay
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(f"%(asctime)s [run {run.id}] %(levelname)-7s %(message)s", "%H:%M:%S")
    for h in (logging.StreamHandler(),
              logging.FileHandler(settings.LOG_DIR / f"worker_{datetime.now():%Y%m%d}.log", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return log


def execute(run: JobRun) -> JobRun:
    handler = HANDLERS.get(run.job.code)
    run.status = JobRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])
    log = _make_logger(run)
    log.info("Bắt đầu job %s (%s)", run.job.name, run.get_trigger_display())

    try:
        if handler is None:
            raise RuntimeError(f"Chưa có handler cho job '{run.job.code}' trong registry.py")
        result = handler(run, log)
        rows, errors = result if isinstance(result, tuple) else (result, [])
        run.rows_total = rows or 0
        if errors:
            run.status = JobRun.Status.PARTIAL
            run.error = "\n".join(errors)
            log.warning("Hoàn thành một phần: %d lỗi", len(errors))
        else:
            run.status = JobRun.Status.SUCCESS
            log.info("Hoàn thành: %d dòng", run.rows_total)
    except Exception as e:
        run.status = JobRun.Status.FAILED
        run.error = str(e)
        run.error_code = getattr(e, "code", None) or "UNKNOWN"
        log.exception("Job thất bại [%s]: %s", run.error_code, e)
    finally:
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "rows_total", "error", "error_code", "finished_at"])
        run.job.last_run_at = run.finished_at
        run.job.save(update_fields=["last_run_at"])
        for h in list(log.handlers):
            h.close()
            log.removeHandler(h)
    return run
