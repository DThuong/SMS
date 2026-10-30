"""Tạo dữ liệu mặc định: xưởng SMD/MAINLINE, cài đặt, job. Chạy lại nhiều lần vẫn an toàn."""
from django.core.management.base import BaseCommand

from apps.core.models import DEFAULT_SETTINGS, OBSOLETE_SETTINGS, Oper, Setting
from apps.jobs.models import Job


class Command(BaseCommand):
    help = "Tạo dữ liệu mặc định cho SMS"

    def handle(self, *args, **opts):
        for i, (code, name, folder, desc) in enumerate([
            ("2010", "SMD", "SMD", "Kế hoạch xưởng SMD"),
            ("3010", "MAINLINE", "MAINLINE", "Kế hoạch xưởng Mainline (MAIN Input)"),
        ]):
            _, created = Oper.objects.get_or_create(
                code=code, defaults={"name": name, "folder": folder, "description": desc, "sort_order": i})
            self.stdout.write(f"Oper {code}: {'tạo mới' if created else 'đã có'}")

        for key, value, vtype, label, desc, secret in DEFAULT_SETTINGS:
            obj, created = Setting.objects.get_or_create(
                key=key, defaults={"value": value, "value_type": vtype, "label": label,
                                   "description": desc, "is_secret": secret})
            if not created and (obj.label, obj.description, obj.is_secret) != (label, desc, secret):
                obj.label, obj.description, obj.is_secret = label, desc, secret   # giữ nguyên giá trị
                obj.save(update_fields=["label", "description", "is_secret"])
            self.stdout.write(f"Setting {key}: {'tạo mới' if created else 'đã có'}")

        Setting.objects.filter(key__in=OBSOLETE_SETTINGS).delete()

        for code, name, cron, desc in [
            ("WORKORDER_PLAN", "Lấy kế hoạch Work Order", "0 8 * * *",
             "MES → View Work Order → lọc theo từng Oper → lưu file + DB"),
            ("PRODUCTION_OUTPUT", "Lấy sản lượng theo giờ (TV)", "0 8 * * *",
             "MES → Inquiry → 타임별 실적 조회(TV) → ngày hiện tại → View → Excel → lưu file chờ DYS import"),
            ("CLEANUP", "Dọn file & dữ liệu cũ", "30 23 * * *",
             "Xóa file + dữ liệu sản lượng và kế hoạch cũ hơn data_retention_days (mặc định 3 ngày)"),
        ]:
            job, created = Job.objects.get_or_create(
                code=code, defaults={"name": name, "cron": cron, "description": desc})
            if created:
                job.save()   # tính next_run_at
            self.stdout.write(f"Job {code}: {'tạo mới' if created else 'đã có'}")

        self.stdout.write(self.style.SUCCESS("Seed xong."))
