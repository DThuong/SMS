"""Tạo dữ liệu mặc định: nhà máy (plant), xưởng (oper), cài đặt, job. Chạy lại nhiều lần vẫn an toàn."""
from django.core.management.base import BaseCommand

from apps.core.models import DEFAULT_SETTINGS, OBSOLETE_SETTINGS, Oper, Plant, Setting
from apps.jobs.models import Job


class Command(BaseCommand):
    help = "Tạo dữ liệu mặc định cho SMS"

    def handle(self, *args, **opts):
        # Plant trên màn hình View Work Order (ô Plant → nút "...")
        for i, (code, name, is_default) in enumerate([
            ("PL10", "SMPS PLANT", False),
            ("PL20", "TV BOARD PLANT", True),
        ]):
            _, created = Plant.objects.get_or_create(
                code=code, defaults={"name": name, "is_default": is_default, "sort_order": i})
            self.stdout.write(f"Plant {code}: {'tạo mới' if created else 'đã có'}")

        # Oper trên MES (ô Oper → nút "..."). Chỉ SMD + MAINLINE bật sẵn; xưởng khác bật bằng PUT /api/opers/{code}
        for i, (code, name, folder, desc, active) in enumerate([
            ("2010", "SMD", "SMD", "Kế hoạch xưởng SMD", True),
            ("3010", "MAINLINE", "MAINLINE", "Kế hoạch xưởng Mainline (MAIN Input)", True),
            ("1010", "EYELET", "EYELET", "Kế hoạch xưởng EYELET", False),
            ("1020", "AXIAL", "AXIAL", "Kế hoạch xưởng AXIAL", False),
            ("1030", "RADIAL", "RADIAL", "Kế hoạch xưởng RADIAL", False),
            ("2001", "SMD-RW", "SMD-RW", "Kế hoạch SMD Rework", False),
            ("3050", "ASSM", "ASSM", "Kế hoạch xưởng ASSM", False),
        ]):
            _, created = Oper.objects.get_or_create(
                code=code, defaults={"name": name, "folder": folder, "description": desc,
                                     "is_active": active, "sort_order": i})
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
             "MES → View Work Order → Plant → Period (từ ngày ~ đến ngày) → từng Oper → View → Excel → lưu file chờ DYS import"),
            ("PRODUCTION_OUTPUT", "Lấy sản lượng theo giờ (TV)", "0 8 * * *",
             "MES → Inquiry → 타임별 실적 조회(TV) → ngày hôm qua → View → Excel → lưu file chờ DYS import"),
            ("CLEANUP", "Dọn file & dữ liệu cũ", "30 23 * * *",
             "Xóa file + dữ liệu sản lượng và kế hoạch cũ hơn data_retention_days (mặc định 3 ngày)"),
        ]:
            job, created = Job.objects.get_or_create(
                code=code, defaults={"name": name, "cron": cron, "description": desc})
            if created:
                job.save()   # tính next_run_at
            elif (job.name, job.description) != (name, desc):
                Job.objects.filter(pk=job.pk).update(name=name, description=desc)   # giữ nguyên lịch người dùng đã sửa
            self.stdout.write(f"Job {code}: {'tạo mới' if created else 'đã có'}")

        self.stdout.write(self.style.SUCCESS("Seed xong."))
