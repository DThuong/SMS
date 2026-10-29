"""
Worker chạy nền — PHẢI chạy trong phiên Windows đang đăng nhập (UI Automation cần desktop).
Mỗi vòng lặp (mặc định 5 giây):
  1. Cập nhật heartbeat
  2. Job nào tới giờ theo cron -> tạo JobRun (trigger=schedule)
  3. Lấy JobRun đang chờ (queued) cũ nhất -> chạy. Chỉ 1 job chạy tại một thời điểm.
Dừng: Ctrl+C
"""
import os
import socket
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from apps.jobs.models import Job, JobRun, WorkerHeartbeat, next_cron_time
from apps.jobs.runner import execute

MISSED_GRACE = timedelta(hours=12)   # máy tắt lúc 8h, bật lúc 9h vẫn chạy bù; trễ quá 12h thì bỏ qua


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

        WorkerHeartbeat.objects.update_or_create(pk=1, defaults={
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
        WorkerHeartbeat.objects.filter(pk=1).update(last_seen=timezone.now(), current_run=run)

    def _schedule_due_jobs(self):
        now = timezone.now()
        for job in Job.objects.filter(is_active=True, next_run_at__lte=now):
            if now - job.next_run_at <= MISSED_GRACE:
                # Không tạo trùng nếu job này đã có lần chạy đang chờ
                if not job.runs.filter(status__in=[JobRun.Status.QUEUED, JobRun.Status.RUNNING]).exists():
                    JobRun.objects.create(job=job, trigger=JobRun.Trigger.SCHEDULE, params=job.params)
                    self.stdout.write(f"Đến giờ chạy: {job.code}")
            else:
                self.stdout.write(self.style.WARNING(f"Bỏ qua lịch quá hạn của {job.code} ({job.next_run_at})"))
            Job.objects.filter(pk=job.pk).update(next_run_at=next_cron_time(job.cron, now))
