"""
Worker chạy nền — PHẢI chạy trong phiên Windows đang đăng nhập (UI Automation cần desktop).
Mỗi vòng lặp (mặc định 5 giây):
  1. Cập nhật heartbeat
  2. Lịch tự động theo KỲ: mỗi job đang bật phải có đúng 1 lần chạy tự động trong mỗi kỳ cron
     (vd "0 8 * * *": từ 08:00 hôm nay tới 08:00 hôm sau). Kỳ này chưa có → tạo ngay, kể cả khi máy
     bật trễ (9h, 15h, 3h sáng hôm sau...). Đã có (thành công hay lỗi) → không tạo thêm; chạy lại thì gọi API.
  3. Lấy JobRun đang chờ (queued) cũ nhất -> chạy. Chỉ 1 job chạy tại một thời điểm.
Trong lúc chạy job (có thể vài chục phút), một luồng phụ vẫn cập nhật heartbeat mỗi 5 giây → API không báo
nhầm "worker tắt". Mất kết nối MySQL (MySQL khởi động lại, mạng chập chờn) → worker KHÔNG chết: ghi lỗi, chờ rồi
nối lại, lần chạy bị dở được đánh dấu lỗi để không chặn hàng đợi.
Dừng: Ctrl+C
"""
import os
import socket
import threading
import time
import traceback
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.jobs.models import Job, JobRun, WorkerStatus, cron_period
from apps.jobs.runner import execute

PENDING = [JobRun.Status.QUEUED, JobRun.Status.RUNNING]
RETRY_SECONDS = 15          # mất kết nối DB → chờ bấy nhiêu giây rồi thử lại


class Heartbeat(threading.Thread):
    """Luồng phụ cập nhật worker_status.last_seen trong lúc luồng chính đang chạy job (dùng kết nối DB riêng)"""

    def __init__(self, interval: int):
        super().__init__(daemon=True, name="sms-heartbeat")
        self.interval = interval
        self.stopped = threading.Event()

    def run(self):
        try:
            while not self.stopped.wait(self.interval):
                try:
                    WorkerStatus.objects.filter(pk=1).update(last_seen=timezone.now())
                except Exception:
                    close_old_connections()
        finally:
            connection.close()

    def stop(self):
        self.stopped.set()
        self.join(timeout=self.interval + 5)


def _fail_stale_runs(reason: str) -> int:
    """Chỉ có 1 worker → lúc worker rảnh mà còn lần chạy 'running' thì đó là lần chạy bị dở"""
    return JobRun.objects.filter(status=JobRun.Status.RUNNING).update(
        status=JobRun.Status.FAILED, finished_at=timezone.now(), error=reason, error_code="WORKER_INTERRUPTED")


class Command(BaseCommand):
    help = "Chạy worker thực thi job theo lịch và hàng đợi"

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=5, help="Số giây giữa các vòng lặp")

    def handle(self, *args, interval, **opts):
        now = timezone.now()
        n = _fail_stale_runs("Worker khởi động lại khi job đang chạy")
        if n:
            self.stdout.write(self.style.WARNING(f"Đánh dấu {n} lần chạy dang dở là lỗi"))

        from apps.plans.services import ensure_storage_marker
        ensure_storage_marker()     # API chỉ dọn bảng files khi thấy đúng thư mục storage của worker

        WorkerStatus.objects.update_or_create(pk=1, defaults={
            "hostname": socket.gethostname(), "pid": os.getpid(),
            "started_at": now, "last_seen": now, "current_run": None})
        self.stdout.write(self.style.SUCCESS(f"Worker đã chạy (mỗi {interval}s). Ctrl+C để dừng."))

        self.interval = interval
        recovering = False
        try:
            while True:
                try:
                    close_old_connections()   # tránh lỗi "MySQL server has gone away"
                    if recovering:
                        n = _fail_stale_runs("Mất kết nối database khi đang chạy job — hãy chạy lại")
                        self.stdout.write(self.style.SUCCESS(f"Đã nối lại database (đánh dấu {n} lần chạy dở là lỗi)"))
                        recovering = False
                    self._beat(None)
                    self._schedule_due_jobs()
                    run = (JobRun.objects.select_related("job")
                           .filter(status=JobRun.Status.QUEUED).order_by("id").first())
                    if run:
                        self._run(run)
                        continue          # có job chờ tiếp thì chạy luôn
                except Exception:
                    # Lỗi DB / lỗi bất ngờ: KHÔNG để worker chết (máy MES chạy không người trông)
                    recovering = True
                    self.stderr.write(f"Worker gặp lỗi, thử lại sau {RETRY_SECONDS}s:\n{traceback.format_exc(limit=3)}")
                    try:
                        connection.close()
                    except Exception:
                        pass
                    time.sleep(RETRY_SECONDS)
                    continue
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("Đã dừng worker.")

    def _run(self, run):
        self._beat(run)
        hb = Heartbeat(self.interval)
        hb.start()
        try:
            execute(run)
        finally:
            hb.stop()
        self._beat(None)

    def _beat(self, run):
        WorkerStatus.objects.filter(pk=1).update(last_seen=timezone.now(), current_run=run)

    def _schedule_due_jobs(self):
        """Mỗi job đang bật: kỳ hiện tại chưa có lần chạy tự động → tạo đúng 1 lần"""
        now = timezone.now()
        for job in Job.objects.filter(is_active=True):
            start, end = cron_period(job.cron, now)
            if job.next_run_at != end:
                Job.objects.filter(pk=job.pk).update(next_run_at=end)
            if job.scheduled_run_in_period(now):
                continue                                   # kỳ này đã chạy tự động rồi
            if job.runs.filter(status__in=PENDING).exists():
                continue                                   # đang có lần chạy tay dở dang → chờ xong rồi xếp lịch
            params = dict(job.params or {})
            params["scheduled_for"] = start.isoformat()
            if job.code == "PRODUCTION_OUTPUT":
                # Lấy sản lượng NGÀY HÔM TRƯỚC của kỳ: kỳ 30/10 08:00 → FROM_DATE = 29/10 (ngày 29 đã chốt đủ trên MES).
                # Tính theo ngày bắt đầu kỳ nên chạy bù sau nửa đêm vẫn đúng (kỳ 30/10, chạy lúc 02:00 ngày 31 → vẫn 29/10)
                params.setdefault("report_date", (timezone.localtime(start).date() - timedelta(days=1)).isoformat())
            JobRun.objects.create(job=job, trigger=JobRun.Trigger.SCHEDULE, params=params)
            late = (timezone.localtime(now) - start).total_seconds() / 60
            self.stdout.write(f"Đến giờ chạy: {job.code} (kỳ {start:%d/%m %H:%M} → {end:%d/%m %H:%M}"
                              + (f", chạy bù trễ {late:.0f} phút)" if late >= 1 else ")"))
