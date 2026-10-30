from datetime import datetime, timedelta

from croniter import croniter
from django.db import models
from django.utils import timezone


def next_cron_time(cron: str, base: datetime | None = None) -> datetime:
    """Thời điểm chạy kế tiếp theo biểu thức cron, tính theo giờ Việt Nam"""
    base = timezone.localtime(base or timezone.now())
    return croniter(cron, base).get_next(datetime)


def cron_period(cron: str, base: datetime | None = None) -> tuple[datetime, datetime]:
    """
    Kỳ hiện tại của lịch cron: [lần hẹn gần nhất, lần hẹn kế tiếp).
    Vd cron "0 8 * * *", lúc 15:00 ngày 30 → [30/10 08:00, 31/10 08:00); lúc 03:00 ngày 31 → vẫn kỳ đó.
    """
    base = timezone.localtime(base or timezone.now())
    start = croniter(cron, base + timedelta(seconds=1)).get_prev(datetime)   # +1s: đúng 08:00:00 tính là kỳ mới
    return start, croniter(cron, start).get_next(datetime)


class Job(models.Model):
    """Bảng `jobs` — ĐỊNH NGHĨA công việc + lịch chạy (chỉ vài dòng cố định: WORKORDER_PLAN, PRODUCTION_OUTPUT, CLEANUP)"""
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
        db_table = "jobs"
        ordering = ["id"]
        verbose_name = "Job"
        verbose_name_plural = "Job"

    def __str__(self):
        return self.name

    def scheduled_run_in_period(self, base: datetime | None = None):
        """Lần chạy TỰ ĐỘNG (trigger=schedule) của kỳ hiện tại, hoặc None nếu kỳ này chưa chạy"""
        start, _ = cron_period(self.cron, base)
        return (self.runs.filter(trigger=JobRun.Trigger.SCHEDULE, created_at__gte=start)
                .order_by("id").first())

    def save(self, *args, **kwargs):
        # Mỗi lần lưu (đổi lịch / bật tắt) thì tính lại lần chạy kế tiếp
        self.next_run_at = next_cron_time(self.cron) if self.is_active else None
        super().save(*args, **kwargs)


class JobRun(models.Model):
    """Bảng `job_runs` — MỖI LẦN CHẠY một job: ai yêu cầu, tham số, trạng thái, số dòng, mã lỗi"""
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
        db_table = "job_runs"
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


class JobLog(models.Model):
    """Bảng `job_logs` — NHẬT KÝ từng bước của một lần chạy (mỗi dòng log 1 bản ghi, ảnh màn hình khi lỗi)"""
    class Level(models.TextChoices):
        INFO = "INFO", "Thông tin"
        WARNING = "WARNING", "Cảnh báo"
        ERROR = "ERROR", "Lỗi"

    run = models.ForeignKey(JobRun, on_delete=models.CASCADE, related_name="logs")
    seq = models.PositiveIntegerField("STT")
    level = models.CharField("Mức", max_length=10, choices=Level.choices, default=Level.INFO)
    message = models.TextField("Nội dung")
    screenshot = models.CharField("Ảnh lỗi", max_length=500, blank=True)
    created_at = models.DateTimeField("Thời điểm", auto_now_add=True)

    class Meta:
        db_table = "job_logs"
        ordering = ["run", "seq"]
        unique_together = [("run", "seq")]
        verbose_name = "Nhật ký"
        verbose_name_plural = "Nhật ký"


class WorkerStatus(models.Model):
    """Bảng `worker_status` — 1 dòng duy nhất, worker cập nhật mỗi 5s → biết worker còn sống và đang chạy gì"""
    hostname = models.CharField(max_length=200, blank=True)
    pid = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    current_run = models.ForeignKey(JobRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = "worker_status"
        verbose_name = "Trạng thái worker"
        verbose_name_plural = "Trạng thái worker"

    @classmethod
    def is_online(cls) -> bool:
        ws = cls.objects.filter(pk=1).first()
        return bool(ws and ws.last_seen and (timezone.now() - ws.last_seen).total_seconds() < 60)
