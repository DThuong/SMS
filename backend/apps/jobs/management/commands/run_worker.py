"""
Worker chạy nền — PHẢI chạy trong phiên Windows đang đăng nhập (UI Automation cần desktop).
Mỗi vòng lặp (mặc định 5 giây):
  1. Cập nhật heartbeat
  2. Lịch tự động theo KỲ: mỗi job đang bật phải có đúng 1 lần chạy tự động trong mỗi kỳ cron
     (vd "0 8 * * *": từ 08:00 hôm nay tới 08:00 hôm sau). Kỳ này chưa có → tạo ngay, kể cả khi máy
     bật trễ (9h, 15h, 3h sáng hôm sau...). Đã có (thành công hay lỗi) → không tạo thêm; chạy lại thì gọi API.
  3. Lấy JobRun đang chờ (queued) cũ nhất -> chạy. Chỉ 1 job chạy tại một thời điểm.
Dừng: Ctrl+C
"""
import os
import socket
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from apps.jobs.models import Job, JobRun, WorkerStatus, cron_period
from apps.jobs.runner import execute

PENDING = [JobRun.Status.QUEUED, JobRun.Status.RUNNING]


class Command(BaseCommand):
    help = "Chạy worker thực thi job theo lịch và hàng đợi"

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=5, help="Số giây giữa các vòng lặp")

    def handle(self, *args, interval, **opts):
        now = timezone.now()
        stale = JobRun.objects.filter(status=JobRun.Status.RUNNING)
        n = stale.update(status=JobRun.Status.FAILED, finished_at=now,
                         error="Worker khởi động lại khi job đang chạy")
        if n:
            self.stdout.write(self.style.WARNING(f"Đánh dấu {n} lần chạy dang dở là lỗi"))

        WorkerStatus.objects.update_or_create(pk=1, defaults={
            "hostname": socket.gethostname(), "pid": os.getpid(),
            "started_at": now, "last_seen": now, "current_run": None})
        self.stdout.write(self.style.SUCCESS(f"Worker đã chạy (mỗi {interval}s). Ctrl+C để dừng."))

        try:
            while True:
                close_old_connections()   # tránh lỗi "MySQL server has gone away"
                self._beat(None)
                self._schedule_due_jobs()
                run = (JobRun.objects.select_related("job")
                       .filter(status=JobRun.Status.QUEUED).order_by("id").first())
                if run:
                    self._beat(run)
                    execute(run)
                    self._beat(None)
                    continue          # có job chờ tiếp thì chạy luôn
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("Đã dừng worker.")

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
                # Chạy bù sau nửa đêm vẫn lấy đúng ngày của kỳ (vd kỳ 30/10 08:00, chạy lúc 02:00 ngày 31 → lấy 30/10)
                params.setdefault("report_date", start.date().isoformat())
            JobRun.objects.create(job=job, trigger=JobRun.Trigger.SCHEDULE, params=params)
            late = (timezone.localtime(now) - start).total_seconds() / 60
            self.stdout.write(f"Đến giờ chạy: {job.code} (kỳ {start:%d/%m %H:%M} → {end:%d/%m %H:%M}"
                              + (f", chạy bù trễ {late:.0f} phút)" if late >= 1 else ")"))
