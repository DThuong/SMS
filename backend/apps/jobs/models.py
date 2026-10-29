from datetime import datetime

from croniter import croniter
from django.db import models
from django.utils import timezone


def next_cron_time(cron: str, base: datetime | None = None) -> datetime:
    """Thời điểm chạy kế tiếp theo biểu thức cron, tính theo giờ Việt Nam"""
    base = timezone.localtime(base or timezone.now())
    return croniter(cron, base).get_next(datetime)


class Job(models.Model):
    """Định nghĩa một loại công việc chạy nền + lịch chạy"""
    code = models.CharField("Mã job", max_length=50, unique=True,
                            help_text="Khớp với handler trong apps/jobs/registry.py")
    name = models.CharField("Tên", max_length=200)
    description = models.CharField("Mô tả", max_length=500, blank=True)
    cron = models.CharField("Lịch (cron)", max_length=100, default="0 8 * * *",
                            help_text="phút giờ ngày tháng thứ — vd: 0 8 * * * = 8:00 mỗi ngày")
    is_active = models.BooleanField("Bật lịch", default=True)
    params = models.JSONField("Tham số", default=dict, blank=True,
                              help_text='vd: {"opers": ["2010", "3010"]} — để trống = mọi xưởng đang dùng')
    next_run_at = models.DateTimeField("Lần chạy kế tiếp", null=True, blank=True)
    last_run_at = models.DateTimeField("Lần chạy gần nhất", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Job"
        verbose_name_plural = "Job"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Mỗi lần lưu (đổi lịch / bật tắt) thì tính lại lần chạy kế tiếp
        self.next_run_at = next_cron_time(self.cron) if self.is_active else None
        super().save(*args, **kwargs)


class JobRun(models.Model):
    """Một lần chạy job"""
    class Status(models.TextChoices):
        QUEUED = "queued", "Chờ chạy"
        RUNNING = "running", "Đang chạy"
        SUCCESS = "success", "Thành công"
        PARTIAL = "partial", "Thành công một phần"
        FAILED = "failed", "Lỗi"

    class Trigger(models.TextChoices):
        SCHEDULE = "schedule", "Theo lịch"
        MANUAL = "manual", "Chạy tay (DYS)"
        CLI = "cli", "Dòng lệnh"
        API = "api", "Gọi API"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="runs", verbose_name="Job")
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices, default=Status.QUEUED)
    trigger = models.CharField("Kích hoạt", max_length=20, choices=Trigger.choices, default=Trigger.MANUAL)
    requested_by = models.CharField("Người yêu cầu (DYS)", max_length=150, blank=True)
    params = models.JSONField("Tham số", default=dict, blank=True)
    rows_total = models.PositiveIntegerField("Tổng số dòng", default=0)
    error = models.TextField("Lỗi", blank=True)
    error_code = models.CharField("Mã lỗi", max_length=50, blank=True, db_index=True)
    created_at = models.DateTimeField("Tạo lúc", auto_now_add=True)
    started_at = models.DateTimeField("Bắt đầu", null=True, blank=True)
    finished_at = models.DateTimeField("Kết thúc", null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["status", "created_at"])]
        verbose_name = "Lần chạy"
        verbose_name_plural = "Lịch sử chạy"

    def __str__(self):
        return f"#{self.id} {self.job.code} [{self.status}]"

    @property
    def duration_seconds(self):
        if self.started_at and self.finished_at:
            return round((self.finished_at - self.started_at).total_seconds(), 1)
        return None


class JobRunStep(models.Model):
    """Timeline từng bước của một lần chạy (log chữ, ảnh chỉ khi lỗi)"""
    class Level(models.TextChoices):
        INFO = "INFO", "Thông tin"
        WARNING = "WARNING", "Cảnh báo"
        ERROR = "ERROR", "Lỗi"

    run = models.ForeignKey(JobRun, on_delete=models.CASCADE, related_name="steps")
    seq = models.PositiveIntegerField("STT")
    level = models.CharField("Mức", max_length=10, choices=Level.choices, default=Level.INFO)
    message = models.TextField("Nội dung")
    screenshot = models.CharField("Ảnh lỗi", max_length=500, blank=True)
    created_at = models.DateTimeField("Thời điểm", auto_now_add=True)

    class Meta:
        ordering = ["run", "seq"]
        unique_together = [("run", "seq")]
        verbose_name = "Bước"
        verbose_name_plural = "Các bước"


class WorkerHeartbeat(models.Model):
    """Worker cập nhật mỗi vòng lặp — dashboard biết worker có đang chạy không"""
    hostname = models.CharField(max_length=200, blank=True)
    pid = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    current_run = models.ForeignKey(JobRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "Trạng thái worker"
        verbose_name_plural = "Trạng thái worker"
