"""Chạy ngay một job (không cần worker) — tiện khi debug: python manage.py run_job WORKORDER_PLAN"""
from django.core.management.base import BaseCommand, CommandError

from apps.jobs.models import Job, JobRun
from apps.jobs.runner import execute


class Command(BaseCommand):
    help = "Chạy ngay một job theo mã"

    def add_arguments(self, parser):
        parser.add_argument("code", help="Mã job, vd: WORKORDER_PLAN")
        parser.add_argument("--oper", action="append", help="Chỉ lấy Oper này (lặp lại được)")

    def handle(self, *args, code, oper, **opts):
        job = Job.objects.filter(code=code).first()
        if not job:
            raise CommandError(f"Không có job {code}. Đã chạy `manage.py seed` chưa?")
        params = dict(job.params or {})
        if oper:
            params["opers"] = oper
        run = execute(JobRun.objects.create(job=job, trigger=JobRun.Trigger.CLI, params=params))
        self.stdout.write(f"Kết quả: {run.get_status_display()} — {run.rows_total} dòng")
