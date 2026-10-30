"""Chạy ngay một job (không cần worker) — tiện khi debug:
  python manage.py run_job WORKORDER_PLAN
  python manage.py run_job PRODUCTION_OUTPUT --from 2026-10-25 --to 2026-10-28
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.jobs.models import Job, JobRun
from apps.jobs.runner import execute


class Command(BaseCommand):
    help = "Chạy ngay một job theo mã"

    def add_arguments(self, parser):
        parser.add_argument("code", help="Mã job, vd: WORKORDER_PLAN")
        parser.add_argument("--oper", action="append", help="Chỉ lấy Oper này (lặp lại được)")
        parser.add_argument("--from", dest="date_from", help="PRODUCTION_OUTPUT: từ ngày YYYY-MM-DD")
        parser.add_argument("--to", dest="date_to", help="PRODUCTION_OUTPUT: đến ngày YYYY-MM-DD (bỏ trống = bằng --from)")

    def handle(self, *args, code, oper, date_from=None, date_to=None, **opts):
        job = Job.objects.filter(code=code).first()
        if not job:
            raise CommandError(f"Không có job {code}. Đã chạy `manage.py seed` chưa?")
        params = dict(job.params or {})
        if oper:
            params["opers"] = oper
        if date_from:
            start = date.fromisoformat(date_from)
            end = date.fromisoformat(date_to) if date_to else start
            params["report_dates"] = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        run = execute(JobRun.objects.create(job=job, trigger=JobRun.Trigger.CLI, params=params))
        self.stdout.write(f"Kết quả: {run.get_status_display()} — {run.rows_total} dòng")
