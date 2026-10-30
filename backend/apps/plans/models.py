from django.db import models

from apps.core.models import Oper
from apps.jobs.models import JobRun


class File(models.Model):
    """
    Bảng `files` — mọi file một lần chạy job lấy từ MES:
      kind = workorder  : file kế hoạch Work Order của 1 xưởng (oper) → DYS tải về import rồi báo lại
      kind = production : file sản lượng theo giờ (TV) của 1 ngày (report_date) → DYS tải về import rồi báo lại
    """
    class Kind(models.TextChoices):
        WORKORDER = "workorder", "Kế hoạch Work Order"
        PRODUCTION = "production", "Sản lượng theo giờ"

    class Status(models.TextChoices):
        SAVED = "saved", "Đã lưu"
        IMPORTED = "imported", "DYS đã import"
        IMPORT_FAILED = "import_failed", "DYS import lỗi"

    kind = models.CharField("Loại file", max_length=20, choices=Kind.choices)
    run = models.ForeignKey(JobRun, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="files", verbose_name="Lần chạy")
    oper = models.ForeignKey(Oper, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="files", verbose_name="Xưởng (file workorder)")
    report_date = models.DateField("Ngày sản lượng (file production)", null=True, blank=True)
    export_date = models.DateTimeField("Thời điểm xuất từ MES")
    file_path = models.CharField("Đường dẫn", max_length=500, help_text="Tương đối so với STORAGE_DIR")
    file_name = models.CharField("Tên file", max_length=200)
    rows = models.PositiveIntegerField("Số dòng", default=0)
    size = models.PositiveIntegerField("Dung lượng (byte)", default=0)
    checksum = models.CharField("SHA-256", max_length=64, blank=True)
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices, default=Status.SAVED)
    file_deleted = models.BooleanField("Đã xóa file trên ổ đĩa", default=False)
    imported_at = models.DateTimeField("DYS import lúc", null=True, blank=True)
    imported_by = models.CharField("Người import (DYS)", max_length=150, blank=True)
    dys_message = models.TextField("Thông báo từ DYS", blank=True)
    created_at = models.DateTimeField("Tạo lúc", auto_now_add=True)

    class Meta:
        db_table = "files"
        ordering = ["-export_date", "-id"]
        indexes = [models.Index(fields=["kind", "export_date"]),
                   models.Index(fields=["kind", "oper", "export_date"]),
                   models.Index(fields=["kind", "report_date"])]
        verbose_name = "File"
        verbose_name_plural = "File"

    def __str__(self):
        return f"[{self.kind}] {self.file_name}"

