from django.db import models

from apps.core.models import Oper, Plant
from apps.jobs.models import JobRun


class File(models.Model):
    """
    Bảng `files` — mọi file một lần chạy job lấy từ MES:
      kind = workorder  : file kế hoạch Work Order của 1 plant + 1 xưởng (oper) trong 1 kỳ (period_from..period_to)
                          → DYS tải về import rồi báo lại
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
    plant = models.ForeignKey(Plant, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="files", verbose_name="Nhà máy (file workorder)")
    oper = models.ForeignKey(Oper, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="files", verbose_name="Xưởng (file workorder)")
    report_date = models.DateField("Ngày sản lượng (file production)", null=True, blank=True)
    period_from = models.DateField("Period từ ngày (file workorder)", null=True, blank=True)
    period_to = models.DateField("Period đến ngày (file workorder)", null=True, blank=True)
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


class ImportDysHistory(models.Model):
    """
    Bảng `import_dys_history` — MỖI LẦN import vào DYS thành công ghi 1 dòng (file thì bị xóa ngay sau import).
    Dashboard đọc bảng này để biết hôm nay đã lấy + đưa vào DYS bao nhiêu dòng. Job CLEANUP xóa dòng cũ hơn
    `data_retention_days`. Lưu kèm mã/tên plant + xưởng dạng chữ để dòng vẫn đọc được nếu xưởng bị sửa/xóa.
    """
    kind = models.CharField("Loại", max_length=20, choices=File.Kind.choices)
    run = models.ForeignKey(JobRun, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="import_history", verbose_name="Lần chạy")
    file_id = models.PositiveIntegerField("Id file (bảng files, có thể đã xóa)", null=True, blank=True, db_index=True)
    plant = models.ForeignKey(Plant, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="import_history", verbose_name="Nhà máy")
    oper = models.ForeignKey(Oper, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="import_history", verbose_name="Xưởng")
    plant_code = models.CharField("Mã plant", max_length=50, blank=True)
    oper_code = models.CharField("Mã xưởng", max_length=50, blank=True)
    oper_name = models.CharField("Tên xưởng", max_length=150, blank=True)
    report_date = models.DateField("Ngày sản lượng", null=True, blank=True)
    period_from = models.DateField("Period từ ngày", null=True, blank=True)
    period_to = models.DateField("Period đến ngày", null=True, blank=True)
    file_name = models.CharField("Tên file", max_length=200, blank=True)
    rows = models.PositiveIntegerField("Số dòng", default=0)
    export_date = models.DateTimeField("Thời điểm xuất từ MES")
    imported_at = models.DateTimeField("Vào DYS lúc")
    imported_by = models.CharField("Người/hệ thống import", max_length=150, blank=True)
    dys_message = models.TextField("Kết quả DYS trả về", blank=True)
    created_at = models.DateTimeField("Tạo lúc", auto_now_add=True)

    class Meta:
        db_table = "import_dys_history"
        ordering = ["-imported_at", "-id"]
        indexes = [models.Index(fields=["kind", "export_date"], name="import_dys__kind_e_idx"),
                   models.Index(fields=["kind", "oper", "export_date"], name="import_dys__kind_o_idx"),
                   models.Index(fields=["kind", "report_date"], name="import_dys__kind_r_idx"),
                   models.Index(fields=["imported_at"], name="import_dys__importe_idx")]
        verbose_name = "Lịch sử import DYS"
        verbose_name_plural = "Lịch sử import DYS"

    def __str__(self):
        return f"[{self.kind}] {self.file_name} → DYS {self.imported_at:%Y-%m-%d %H:%M}"

